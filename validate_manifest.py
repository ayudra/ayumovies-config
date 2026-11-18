#!/usr/bin/env python3
"""Validate sources.json and standby_pool.json before they are published.

The health bot commits straight to main, and every installed client reads that
file on startup. A malformed manifest therefore breaks the app for everyone, so
this script gates the workflow: it exits non-zero and the commit step never runs.

Run locally with:  python3 validate_manifest.py
"""

import json
import os
import sys

VALID_REQUIRED_IDS = {"tmdb", "imdb", "anilist"}

# Placeholders the clients substitute. Anime mirrors use {tmdb_id}; movie
# mirrors use {id}, which the app resolves according to `required_id`.
ID_PLACEHOLDERS = ("{id}", "{tmdb_id}", "{imdb_id}", "{anilist_id}")

errors = []
warnings = []


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def load(path):
    if not os.path.exists(path):
        err(f"{path}: file is missing")
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        err(f"{path}: invalid JSON at line {exc.lineno}: {exc.msg}")
        return None


def check_domain(where, server):
    domain = server.get("domain")
    if not isinstance(domain, str) or not domain.strip():
        err(f"{where}: 'domain' must be a non-empty string")
        return
    if "://" in domain or "/" in domain:
        err(f"{where}: 'domain' must be a bare hostname, got '{domain}'")


def check_template(where, key, template):
    if not isinstance(template, str) or not template.strip():
        err(f"{where}: '{key}' must be a non-empty string")
        return
    if not any(placeholder in template for placeholder in ID_PLACEHOLDERS):
        err(f"{where}: '{key}' must contain an id placeholder ({', '.join(ID_PLACEHOLDERS)})")


def check_server(where, server, kind):
    if not isinstance(server, dict):
        err(f"{where}: entry must be an object")
        return

    sid = server.get("id")
    if not isinstance(sid, str) or not sid.strip():
        err(f"{where}: 'id' must be a non-empty string")
    elif sid != sid.lower() or " " in sid:
        warn(f"{where}: id '{sid}' should be lowercase with no spaces")

    if not isinstance(server.get("name"), str) or not server.get("name", "").strip():
        err(f"{where}: 'name' must be a non-empty string")

    required_id = server.get("required_id")
    if required_id not in VALID_REQUIRED_IDS:
        err(f"{where}: 'required_id' must be one of {sorted(VALID_REQUIRED_IDS)}, got '{required_id}'")

    if "enabled" in server and not isinstance(server["enabled"], bool):
        err(f"{where}: 'enabled' must be true or false")

    fail_count = server.get("fail_count", 0)
    if not isinstance(fail_count, int) or isinstance(fail_count, bool) or fail_count < 0:
        err(f"{where}: 'fail_count' must be a non-negative integer")

    check_domain(where, server)

    if kind == "movie":
        check_template(where, "url_movie", server.get("url_movie"))
        check_template(where, "url_tv", server.get("url_tv"))
    else:
        check_template(where, "url", server.get("url"))

    # A template that asks for the wrong id type can never play, and it looks
    # perfectly valid at a glance - exactly the bug that made IMDb-based
    # downloads fail while playback worked.
    templates = [server.get(k) for k in ("url", "url_movie", "url_tv") if isinstance(server.get(k), str)]
    if required_id == "tmdb" and any("{imdb_id}" in t for t in templates):
        err(f"{where}: required_id is 'tmdb' but the template uses {{imdb_id}}")
    if required_id == "imdb" and any("{tmdb_id}" in t for t in templates):
        err(f"{where}: required_id is 'imdb' but the template uses {{tmdb_id}}")


def main():
    sources = load("sources.json")
    standby = load("standby_pool.json")

    if sources is not None:
        for key, expected in (
            ("version", int),
            ("updated", str),
            ("movie_servers", list),
            ("anime_servers", list),
            ("deprecated_servers", list),
            ("ad_whitelist", list),
        ):
            if key not in sources:
                err(f"sources.json: missing required key '{key}'")
            elif not isinstance(sources[key], expected):
                err(f"sources.json: '{key}' must be {expected.__name__}")

        if isinstance(sources.get("version"), int) and sources["version"] < 1:
            err("sources.json: 'version' must be >= 1")

        if isinstance(sources.get("movie_servers"), list):
            for index, server in enumerate(sources["movie_servers"]):
                check_server(f"sources.json:movie_servers[{index}]", server, "movie")

        if isinstance(sources.get("anime_servers"), list):
            for index, server in enumerate(sources["anime_servers"]):
                check_server(f"sources.json:anime_servers[{index}]", server, "anime")

        if isinstance(sources.get("deprecated_servers"), list):
            for entry in sources["deprecated_servers"]:
                if not isinstance(entry, str) or not entry.strip():
                    err("sources.json: 'deprecated_servers' entries must be non-empty strings")

        whitelist = sources.get("ad_whitelist")
        if isinstance(whitelist, list):
            seen = set()
            for entry in whitelist:
                if not isinstance(entry, str) or not entry.strip():
                    err("sources.json: 'ad_whitelist' entries must be non-empty strings")
                    continue
                if entry in seen:
                    warn(f"sources.json: duplicate ad_whitelist entry '{entry}'")
                seen.add(entry)

            # The ad blocker treats these entries as "allowed to load", so a
            # streaming domain that is missing here gets blocked and the player
            # silently shows nothing.
            for server in (sources.get("movie_servers") or []) + (sources.get("anime_servers") or []):
                if not isinstance(server, dict) or server.get("enabled") is False:
                    continue
                domain = str(server.get("domain") or "")
                if domain and not any(w in domain or domain in w for w in seen if isinstance(w, str)):
                    warn(f"sources.json: enabled server '{server.get('id')}' domain '{domain}' is not covered by ad_whitelist")

    if standby is not None:
        if not isinstance(standby.get("version"), int):
            err("standby_pool.json: 'version' must be an integer")
        if not isinstance(standby.get("updated"), str):
            err("standby_pool.json: 'updated' must be a string")

        for kind in ("movie_servers", "anime_servers"):
            entries = standby.get(kind, [])
            if not isinstance(entries, list):
                err(f"standby_pool.json: '{kind}' must be a list")
                continue
            expected_kind = "movie" if kind == "movie_servers" else "anime"
            for index, server in enumerate(entries):
                check_server(f"standby_pool.json:{kind}[{index}]", server, expected_kind)

        # A standby entry that is already active can never be promoted, so the
        # pool silently stops healing. That is a hard error, not a warning.
        if sources is not None:
            active = {
                s.get("id")
                for s in (sources.get("movie_servers") or []) + (sources.get("anime_servers") or [])
                if isinstance(s, dict)
            }
            for kind in ("movie_servers", "anime_servers"):
                for server in standby.get(kind, []) or []:
                    if isinstance(server, dict) and server.get("id") in active:
                        err(f"standby_pool.json:{kind}: '{server.get('id')}' is already an active server and can never be promoted")

    for message in warnings:
        print(f"  warning: {message}")

    if errors:
        print(f"\nMANIFEST INVALID - {len(errors)} error(s):")
        for message in errors:
            print(f"  error: {message}")
        return 1

    movie_count = len(sources.get("movie_servers", [])) if sources else 0
    anime_count = len(sources.get("anime_servers", [])) if sources else 0
    print(f"\nManifest OK - version {sources.get('version')} ({movie_count} movie, {anime_count} anime)"
          + (f", {len(warnings)} warning(s)" if warnings else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
