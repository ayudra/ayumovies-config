# Ayumovies Dynamic Sources Configuration

Centralized streaming-sources manifest for Ayumovies (Android + Linux Desktop).
Both apps fetch it at startup, cache the last good copy, and fall back to a
bundled default if every endpoint is unreachable.

## Endpoints

- **CDN (jsDelivr)**: `https://cdn.jsdelivr.net/gh/ayudra/ayumovies-config@main/sources.json`
- **Fallback (raw GitHub)**: `https://raw.githubusercontent.com/ayudra/ayumovies-config/main/sources.json`
- **Cache purge**: `https://purge.jsdelivr.net/gh/ayudra/ayumovies-config@main/sources.json`

`version` is bumped on every real change; clients only use it for cache
invalidation, so fields must be added, never renamed or removed.

## `required_id` — read this before editing a server

Every server declares which identifier the mirror expects:

| `required_id` | Send | Servers |
|---|---|---|
| `tmdb` | numeric TMDB id (`27205`) | VidLink, PrimeSrc |
| `imdb` | `tt`-prefixed IMDb id (`tt1375666`) | VidSrc family, 2Embed |
| `anilist` | numeric AniList id | AniXo, MegaVid |

Sending the wrong one produces a valid-looking URL that always fails to play,
which is why it is enforced in both the player path (`{id}` substitution) and
the download path. If the app has no IMDb id it falls back to the TMDB id rather
than refusing to try, so a missing `imdb_id` degrades instead of breaking.

## Health fields

The scout writes these per server; clients ignore unknown fields.

- `fail_count` — consecutive failed probes, reset to 0 on success
- `last_ok` — ISO-8601 UTC timestamp of the last successful probe
- `last_checked` — ISO-8601 UTC timestamp of the last probe
- `latency_ms` — HTTP round-trip of the last successful probe
- `enabled` — `false` removes the server from both apps

Health metadata is persisted only when the run changes something real, so the
bot does not commit twice a day just to refresh timestamps.

## How the bot decides

`scout.py` runs twice daily (`0 0,12 * * *` UTC).

1. Probe each server: HTTP status, iframe headers (`CSP frame-ancestors`,
   `X-Frame-Options`), ad-wall/parking signatures, body size, and — when the
   embed already contains an `.m3u8` URL — that the stream hint answers.
2. Classify the failure:
   - **hard** (404/410, iframe blocked, ad-wall page) → disable immediately
   - **soft** (timeout, 5xx, network/SSL error) → disable only after
     `SOFT_FAIL_LIMIT` (3) consecutive failing runs, so a Cloudflare 520 or a
     slow origin does not bench a healthy mirror
3. If the fleet is below target capacity (5 movies / 4 anime), probe standby
   candidates and promote the first healthy one.
4. `validate_manifest.py` gates the commit. If the manifest is malformed the job
   fails and nothing is published.

Use `python3 scout.py --dry-run` to probe and report without touching any file.

## Standby pool

`standby_pool.json` lists mirrors that are vetted by hand but not active. The bot
promotes one only after probing it, so a dead candidate costs nothing.

Two rules:

- An entry **already active** in `sources.json` can never be promoted and is
  rejected by the validator — that is how the pool silently stopped healing
  before.
- The pool is a manual trust decision. Do not add a domain that has not been
  checked in a browser; auto-promoting an unvetted host would push a new
  third-party iframe into every install.

**Current state: the movie pool is empty**, so a movie mirror that dies is not
replaced automatically — the app just falls back to the remaining four. Add a
vetted candidate here to restore self-healing.

## Known limitation: the manifest is unsigned

Clients load these URLs into a WebView with `webSecurity: false`, so whoever can
push to this repo controls third-party iframe hosts in every installed app. The
ad_whitelist limits which *ad* hosts load, not which servers can be referenced.

Until the manifest carries a signature verified against a key pinned in the app,
treat push access to this repository as equivalent to app signing access: enable
2FA, review every change, and never merge an automated PR from a fork.
