#!/usr/bin/env python3
"""
Ayumovies Autonomous Health Scout & Source Maintenance Engine
============================================================
Runs via GitHub Actions on a 12-hour schedule.
1. Probes all active servers with benchmark media IDs using genuine browser headers.
2. Follows HTTP 301/302 redirects to auto-detect provider domain migrations.
3. Auto-disables uncontactable servers after repeated failures.
4. Auto-promotes verified backup providers from standby_pool.json if active servers drop.
5. Dynamically syncs newly discovered domains to ad_whitelist.
6. Bumps sources.json version for instant zero-rebuild app updates.
"""

import os
import sys
import json
import datetime
from urllib.parse import urlparse, quote

try:
    import requests
except ImportError:
    print("requests library required. Run: pip install requests")
    sys.exit(1)

BENCHMARK = {
    "movie": {
        "imdb": "tt1375666",  # Inception
        "tmdb": "27205"
    },
    "tv": {
        "imdb": "tt0944947",  # Game of Thrones
        "tmdb": "1399",
        "season": "1",
        "episode": "1"
    },
    "anime": {
        "anilist": "16498",    # Attack on Titan
        "episode": "1",
        "lang": "sub"
    }
}

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "iframe",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Referer": "https://google.com/"
}

def format_probe_url(server, is_anime=False):
    req_id = server.get("required_id", "tmdb")
    if is_anime:
        template = server.get("url", "")
        chosen_id = BENCHMARK["anime"]["anilist"] if req_id == "anilist" else (BENCHMARK["tv"]["tmdb"] if req_id == "tmdb" else BENCHMARK["tv"]["imdb"])
        ep = BENCHMARK["anime"]["episode"]
        lang = BENCHMARK["anime"]["lang"]
        return template.replace("{id}", chosen_id)\
                       .replace("{anilist_id}", BENCHMARK["anime"]["anilist"])\
                       .replace("{tmdb_id}", BENCHMARK["tv"]["tmdb"])\
                       .replace("{imdb_id}", BENCHMARK["tv"]["imdb"])\
                       .replace("{season}", "1")\
                       .replace("{episode}", ep)\
                       .replace("{ep}", ep)\
                       .replace("{lang}", lang)
    else:
        template = server.get("url_movie", "")
        chosen_id = BENCHMARK["movie"]["tmdb"] if req_id == "tmdb" else BENCHMARK["movie"]["imdb"]
        return template.replace("{id}", chosen_id)\
                       .replace("{tmdb_id}", BENCHMARK["movie"]["tmdb"])\
                       .replace("{imdb_id}", BENCHMARK["movie"]["imdb"])

def probe_endpoint(server, is_anime=False):
    probe_url = format_probe_url(server, is_anime)
    if not probe_url:
        return False, None, "No URL template defined"

    orig_domain = server.get("domain", "").lower()

    try:
        resp = requests.get(probe_url, headers=DEFAULT_HEADERS, timeout=9, allow_redirects=True)
        final_domain = urlparse(resp.url).netloc.lower()

        # Check for 301/302 domain migration
        migrated_domain = None
        if final_domain and final_domain != orig_domain:
            # Verify final_domain is a real mirror and not an ad-block wall or parking page
            if not any(bad in final_domain for bad in ["cloudflare", "parking", "domain-for-sale", "adsterra", "google"]):
                migrated_domain = final_domain

        # Healthy if HTTP 200 and has valid player content
        if resp.status_code == 200:
            bad_indicators = ["profiton-domain-verification", "domain-for-sale", "cloudflare-challenge", "pwa/icon-192.png"]
            text_lower = resp.text.lower()
            if any(b in text_lower for b in bad_indicators):
                return False, migrated_domain, "Detected ad-wall or non-embeddable page"

            if len(resp.content) > 150:
                return True, migrated_domain, f"HTTP 200 OK ({len(resp.content)} bytes)"
            else:
                return False, migrated_domain, f"Response too short ({len(resp.content)} bytes)"
        elif resp.status_code in [301, 302, 307, 308]:
            return True, migrated_domain, f"HTTP Redirect ({resp.status_code})"
        else:
            return False, migrated_domain, f"HTTP {resp.status_code}"

    except requests.exceptions.Timeout:
        return False, None, "Connection timeout (>9s)"
    except requests.exceptions.SSLError as ssl_err:
        return False, None, f"SSL Error: {ssl_err}"
    except Exception as ex:
        return False, None, f"Probe error: {ex}"

def run_scout():
    config_path = os.path.join(os.path.dirname(__file__), "sources.json")
    standby_path = os.path.join(os.path.dirname(__file__), "standby_pool.json")

    if not os.path.exists(config_path):
        print(f"[FATAL] sources.json not found at {config_path}")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        sources = json.load(f)

    standby = {"movie_servers": [], "anime_servers": []}
    if os.path.exists(standby_path):
        try:
            with open(standby_path, "r", encoding="utf-8") as f:
                standby = json.load(f)
        except Exception as e:
            print(f"[WARN] Error loading standby pool: {e}")

    changed = False
    print("=" * 65)
    print(f" Ayumovies Health Scout Engine - Running Probe {datetime.datetime.now(datetime.timezone.utc)}")
    print("=" * 65)

    whitelist = set(sources.get("ad_whitelist", []))

    # 1. Probe Movie & TV Servers
    print("\n[1/3] Probing Movie/TV Servers...")
    for srv in sources.get("movie_servers", []):
        sid = srv.get("id")
        name = srv.get("name")
        ok, new_domain, reason = probe_endpoint(srv, is_anime=False)
        print(f"  • {name} ({srv.get('domain')}): {'✅ HEALTHY' if ok else '❌ FAIL'} - {reason}")

        if new_domain:
            print(f"    🔄 MIGRATION DETECTED: {srv.get('domain')} -> {new_domain}")
            old_domain = srv.get("domain")
            srv["domain"] = new_domain
            if "url_movie" in srv:
                srv["url_movie"] = srv["url_movie"].replace(old_domain, new_domain)
            if "url_tv" in srv:
                srv["url_tv"] = srv["url_tv"].replace(old_domain, new_domain)
            whitelist.add(new_domain)
            changed = True

        if ok:
            srv["fail_count"] = 0
            if srv.get("enabled") is False:
                srv["enabled"] = True
                print(f"    ⚡ Server recovered, re-enabled: {name}")
                changed = True
        else:
            fc = srv.get("fail_count", 0) + 1
            srv["fail_count"] = fc
            if fc >= 2 and srv.get("enabled") is not False:
                srv["enabled"] = False
                print(f"    ⚠️ Server marked DISABLED after {fc} failures: {name}")
                changed = True

    # 2. Probe Anime Servers
    print("\n[2/3] Probing Anime Servers...")
    for srv in sources.get("anime_servers", []):
        sid = srv.get("id")
        name = srv.get("name")
        ok, new_domain, reason = probe_endpoint(srv, is_anime=True)
        print(f"  • {name} ({srv.get('domain')}): {'✅ HEALTHY' if ok else '❌ FAIL'} - {reason}")

        if new_domain:
            print(f"    🔄 MIGRATION DETECTED: {srv.get('domain')} -> {new_domain}")
            old_domain = srv.get("domain")
            srv["domain"] = new_domain
            if "url" in srv:
                srv["url"] = srv["url"].replace(old_domain, new_domain)
            whitelist.add(new_domain)
            changed = True

        if ok:
            srv["fail_count"] = 0
            if srv.get("enabled") is False:
                srv["enabled"] = True
                print(f"    ⚡ Server recovered, re-enabled: {name}")
                changed = True
        else:
            fc = srv.get("fail_count", 0) + 1
            srv["fail_count"] = fc
            if fc >= 2 and srv.get("enabled") is not False:
                srv["enabled"] = False
                print(f"    ⚠️ Server marked DISABLED after {fc} failures: {name}")
                changed = True

    # 3. Check Capacity & Auto-Promote Standby Servers if Needed
    print("\n[3/3] Checking Server Fleet Capacity...")
    active_movies = [s for s in sources.get("movie_servers", []) if s.get("enabled") is not False]
    active_anime = [s for s in sources.get("anime_servers", []) if s.get("enabled") is not False]

    target_movie_count = 5
    if len(active_movies) < target_movie_count:
        needed = target_movie_count - len(active_movies)
        print(f"  Movie servers below target capacity ({len(active_movies)}/{target_movie_count}). Seeking {needed} standby...")
        for candidate in standby.get("movie_servers", []):
            if any(s.get("id") == candidate.get("id") for s in sources["movie_servers"]):
                continue
            if candidate.get("id") in sources.get("deprecated_servers", []):
                continue
            ok, _, _ = probe_endpoint(candidate, is_anime=False)
            if ok:
                sources["movie_servers"].append(candidate)
                whitelist.add(candidate.get("domain"))
                print(f"    🚀 PROMOTED STANDBY: {candidate.get('name')} -> Active Movie Servers")
                changed = True
                needed -= 1
                if needed <= 0:
                    break

    target_anime_count = 4
    if len(active_anime) < target_anime_count:
        needed = target_anime_count - len(active_anime)
        print(f"  Anime servers below target capacity ({len(active_anime)}/{target_anime_count}). Seeking {needed} standby...")
        for candidate in standby.get("anime_servers", []):
            if any(s.get("id") == candidate.get("id") for s in sources["anime_servers"]):
                continue
            ok, _, _ = probe_endpoint(candidate, is_anime=True)
            if ok:
                sources["anime_servers"].append(candidate)
                whitelist.add(candidate.get("domain"))
                print(f"    🚀 PROMOTED STANDBY: {candidate.get('name')} -> Active Anime Servers")
                changed = True
                needed -= 1
                if needed <= 0:
                    break

    sources["ad_whitelist"] = sorted(list(whitelist))

    # 4. Save and Bump Version If Changes Occurred
    if changed:
        old_ver = sources.get("version", 1)
        sources["version"] = old_ver + 1
        sources["updated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(sources, f, indent=2)

        print("\n" + "=" * 65)
        print(f" ✨ MANIFEST UPDATED: Bumped version {old_ver} -> {sources['version']}")
        print(f" 📅 Updated Date: {sources['updated']}")
        print("=" * 65)
        # Output flag for GitHub Actions to commit
        with open("scout_result.txt", "w") as rf:
            rf.write("CHANGED=true\n")
    else:
        print("\n" + "=" * 65)
        print(" ✅ ALL SERVERS HEALTHY: No manifest updates required.")
        print("=" * 65)
        with open("scout_result.txt", "w") as rf:
            rf.write("CHANGED=false\n")

if __name__ == "__main__":
    run_scout()
