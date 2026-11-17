# hypixel-tracker-api

A read-only FastAPI service that turns the `site/data.json` published on GitHub Pages by
[hypixel_api_tracker](https://github.com/xiaowangdeqilin/hypixel_api_tracker) into a REST API
plus a lightweight dashboard. No Rust toolchain needed — install the Python package and run.

- Distribution: `hypixel-tracker-api` (import name `hypixel_tracker_api`)
- Version: 3.0.0, Python 3.9+
- Default source:
  `https://raw.githubusercontent.com/xiaowangdeqilin/hypixel_api_tracker/main/site/data.json`
- Default bind: `http://127.0.0.1:8000`

## Install

### From this repository

```bash
cd fastapi_app
python3 -m venv .venv
.venv/bin/pip install .          # use -e '.[test]' for development
```

### From a wheel

Release assets include `hypixel_tracker_api-3.0.0-py3-none-any.whl`:

```bash
python3 -m pip install --upgrade hypixel_tracker_api-3.0.0-py3-none-any.whl
```

### Platform notes

- **Windows**: `py -m venv .venv`, then `.venv\Scripts\pip install .`, and start with
  `.venv\Scripts\hypixel-tracker-api.exe`.
- **macOS / Linux**: if pip reports `externally-managed-environment`, use a virtualenv
  instead of forcing a global install.
- **Termux / containers**: `uvicorn[standard]` pulls optional native dependencies. If one
  fails to build, run `pip install . --no-deps` and install `fastapi httpx jinja2 uvicorn`
  separately.

## Run

```bash
hypixel-tracker-api                       # same as serve, 127.0.0.1:8000
hypixel-tracker-api serve --host 0.0.0.0 --port 9000
hypixel-tracker-api serve --reload        # development
python3 -m hypixel_tracker_api            # equivalent
```

Open <http://127.0.0.1:8000/> for the dashboard. All JSON endpoints live under `/api/*`.

### Commands

| Command | Description |
| --- | --- |
| `hypixel-tracker-api` / `serve` | Start the service; `--host` defaults to `127.0.0.1`, `--port` to `8000`, `--reload` for hot reload |
| `--version` | Print the version |
| `--check-update` | Ask GitHub Releases for a newer version and show the `pip` upgrade command |
| `--print-crash ID` | Print a full crash report |
| `--clear-cache` | Delete data and locale caches, keep crash reports |
| `-h` / `--help` | Help in zh/en/ja/de/es/fr/ru/pt based on `LANG` / `LC_ALL` |

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `HYPIXEL_DATA_URL` | `site/data.json` on raw.githubusercontent.com | Primary data source |
| `HYPIXEL_DATA_FILE` | empty | Path to a local `data.json`; takes priority over the network (portable bundle mode) |
| `HYPIXEL_FALLBACK_URL` | jsDelivr mirror | Used when the primary source fails |
| `HYPIXEL_CACHE_DIR` | `~/.cache/hypixel-tracker-api` | Cache and crash report root |
| `HYPIXEL_DATA_TTL` | `300` | Data cache TTL in seconds |
| `HYPIXEL_LOCALE_TTL` | `86400` | Locale cache TTL in seconds |
| `HYPIXEL_HTTP_TIMEOUT` | `10` | Per-request timeout in seconds; one retry |

## HTTP API

Every `/api/*` response carries `Cache-Control: no-store` and allows any origin. Time fields
are **Beijing time** strings (`YYYY-MM-DD HH:MM:SS`); the server never converts them, the
dashboard converts them in the browser for the selected time zone.

| Method | Path | Description |
| --- | --- | --- |
| GET | `/` | Dashboard: version bar, 7 endpoint cards, recent changes with diff colours, language and time zone selectors |
| GET | `/api/health` | `{status, version, data_age_seconds, stale}`; `status=unavailable` without data |
| GET | `/api/version` | `{app, version, dashboard_version, build, generated_at, time_basis, repo}` |
| GET | `/api/summary` | `{endpoint, status, summary, fetched_at}` per endpoint plus overall `stale` |
| GET | `/api/changes?limit=&endpoint=` | Change array; `limit` defaults to 200 and is clamped to 1..2000 |
| GET | `/api/data/{name}` | Whitelisted dataset; endpoint names resolve inside `endpoints`, other names are top-level keys |
| GET | `/api/languages` | `[{code, name}]` from the built-in language table |
| GET | `/api/i18n/{lang}` | `{lang, dir, fallback, pack}`; `zh-CN` returns an empty pack meaning source text |
| GET | `/api/crash/recent?limit=` | Recent crash report summaries (max 50) |
| GET | `/api/update/check` | `{current, latest, available, page}`, cached for 10 minutes |

Allowed `/api/data/{name}` values: `collections` `skills` `items` `bazaar` `packs` `bingo`
`firesales` `pack` `pack_changes` `version_info` `endpoints`. Anything else returns 404.

## Degradation and offline behaviour

1. A warm in-memory cache is returned until `HYPIXEL_DATA_TTL` expires.
2. After expiry the primary URL is fetched; on failure the jsDelivr mirror is tried, with a
   10 s timeout and one retry per request.
3. If all network paths fail, the on-disk cache is served and `stale: true` is reported by
   `/api/health`, `/api/summary` and the dashboard.
4. If even the cache is missing, the API returns HTTP 503 with
   `{"error": "data unavailable", ...}` — no traceback is ever sent to clients.

Locale packs follow the same chain: remote → disk → bundled `en.json` (`zh-CN` shows source
text directly).

## Crash reports

Unhandled exceptions are written to:

```
~/.cache/hypixel-tracker-api/crash/crash-YYYYmmdd-HHMMSS.json
```

(On Windows: `%USERPROFILE%\.cache\hypixel-tracker-api\crash\`, or under
`HYPIXEL_CACHE_DIR` when set.) At most 20 reports are kept; the oldest are pruned.

Clients only receive:

```json
{"error": "internal server error", "crash_report": "20261117-203845", "request_id": "..."}
```

Troubleshooting:

1. Print the full report by id: `hypixel-tracker-api --print-crash 20261117-203845`.
2. The report contains `version / python / platform / path / method / message / traceback /
   request_id`; please attach it to bug reports (it may contain local paths).
3. `GET /api/crash/recent` lists recent summaries.

## Updating

```bash
hypixel-tracker-api --check-update
python3 -m pip install --upgrade hypixel-tracker-api
```

The program never replaces its own installed files at runtime; upgrades stay with `pip` so
package state remains consistent.

## FAQ

| Symptom | Action |
| --- | --- |
| "Cached data, may be stale" banner | Upstream is unreachable and the disk cache is being served; refresh later |
| Every endpoint returns 503 | No successful fetch yet; check access to `raw.githubusercontent.com` or set `HYPIXEL_DATA_URL` |
| Port already in use | Pick another `--port` or free the current one |
| Times are not converted | Conversion needs JavaScript; without it raw Beijing time is shown |
| UI falls back to English | The locale pack is unreachable and not cached; English is the designed fallback |

## Tests and linting

```bash
cd fastapi_app
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q
.venv/bin/ruff check src tests
```

Tests inject fake data sources and never touch the network.
