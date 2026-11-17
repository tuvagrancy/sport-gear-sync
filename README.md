# Sport Gear Auto Sync

Automatically sync your gear/shoe data from **Strava** to **Garmin Connect**, **Stryd**, **Runalyze**, and **Intervals.icu** — after each activity, run one command and all platforms stay in sync.

## Features

- **Multi-platform sync**: one source of truth (Strava) → four platforms
- **Incremental**: only processes activities since the last run, safe to run hourly
- **Dry run by default**: preview every change before writing
- **Auto token refresh**: Stryd tokens refresh automatically (no more manual copy-paste)
- **Historical backfill**: catch up all past activities with `--reset-since N`
- **Intervals.icu bulk backfill**: dedicated script to populate all historical gear data
- **Docker-ready**: run on a NAS or any server, with volume-mounted state

## Supported Platforms

| Platform | Auth | Activity matching |
|----------|------|-------------------|
| Garmin Connect | Username + password (cached) | Time + distance |
| Stryd | OAuth token (auto-refresh) | Time + distance |
| Runalyze | Username + password (cached) | Garmin activity ID |
| Intervals.icu | API Key (permanent) | Garmin activity ID |

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. First-time setup

```bash
python -I auto_sync.py --setup
```

The wizard walks you through each platform. Press **Enter** to skip any item you want to configure later — existing values are preserved.

**What you need:**

| Platform | Where to get credentials |
|----------|--------------------------|
| Strava | [strava.com/settings/api](https://www.strava.com/settings/api) — create an app, set Callback Domain to `localhost` |
| Stryd | Browser DevTools → Application → Local Storage → `https://www.stryd.com` → copy `token`, `refreshToken`, `refreshClientId` |
| Runalyze | Your runalyze.com username and password |
| Intervals.icu | [intervals.icu/settings](https://intervals.icu/settings) → Developer → API Key |
| Garmin | Your Garmin Connect email and password (prompted on first use) |

### 3. Add your gear mapping

Before syncing, you need to tell the script which Strava gear name maps to which platform gear:

**Strava → Garmin/Runalyze/Intervals.icu**: edit `SHOE_MAP` and `BIKE_NAME_MAP` in `auto_sync.py`.

**Strava → Stryd**: Stryd uses numeric gear IDs. Run this to create your shoes in Stryd and get the mapping:

```bash
python -I create_stryd_shoes.py              # preview
python -I create_stryd_shoes.py --apply      # create + print SHOE_ID_MAP
```

Paste the printed `SHOE_ID_MAP` into `auto_sync.py` and `sync_stryd.py`.

### 4. Backfill historical data

```bash
# Garmin + Stryd + Runalyze + Intervals.icu (uses Strava CSV export)
python -I auto_sync.py --reset-since 365 --apply

# Intervals.icu only (faster, uses Strava CSV export directly)
python -I backfill_intervals.py --csv path/to/activities.csv              # preview
python -I backfill_intervals.py --csv path/to/activities.csv --apply      # write
```

To get the Strava CSV: go to [strava.com/athlete/delete_your_account](https://www.strava.com/athlete/delete_your_account) → "Request Your Archive" → download and extract.

### 5. Daily sync

```bash
python -I auto_sync.py           # dry run
python -I auto_sync.py --apply   # write
```

## Docker Deployment (NAS / Server)

### Build

```bash
docker build -t gear-sync:latest .
```

### First-time setup (interactive)

```bash
docker run -it --rm \
  -v /opt/docker/gear-sync/data:/app/data \
  -v /opt/docker/gear-sync/garminconnect:/app/garminconnect \
  -e STATE_FILE=/app/data/auto_sync_state.json \
  -e GARMIN_TOKENS=/app/garminconnect \
  gear-sync:latest \
  python -I auto_sync.py --setup
```

### Sync

```bash
docker run --rm \
  -v /opt/docker/gear-sync/data:/app/data \
  -v /opt/docker/gear-sync/garminconnect:/app/garminconnect \
  -e STATE_FILE=/app/data/auto_sync_state.json \
  -e GARMIN_TOKENS=/app/garminconnect \
  gear-sync:latest \
  python -I auto_sync.py --apply
```

### Scheduled sync (cron / Synology Task Scheduler)

Add a job that runs every hour:

```bash
docker run --rm \
  -v /opt/docker/gear-sync/data:/app/data \
  -v /opt/docker/gear-sync/garminconnect:/app/garminconnect \
  -e STATE_FILE=/app/data/auto_sync_state.json \
  -e GARMIN_TOKENS=/app/garminconnect \
  gear-sync:latest \
  python -I auto_sync.py --apply
```

On Synology DSM: Control Panel → Task Scheduler → User-defined script → run as `root`.

## Command Reference

| Command | Description |
|---------|-------------|
| `auto_sync.py --setup` | Configure credentials (re-run any time to update one platform) |
| `auto_sync.py` | Dry run: show what would change |
| `auto_sync.py --apply` | Sync new activities since last run |
| `auto_sync.py --reset-since 30` | Re-process last 30 days |
| `backfill_intervals.py --csv <path>` | Preview Intervals.icu backfill |
| `backfill_intervals.py --csv <path> --apply` | Write Intervals.icu backfill |
| `create_stryd_shoes.py` | Preview Stryd shoe creation |
| `create_stryd_shoes.py --apply` | Create shoes in Stryd + print ID mapping |
| `sync_stryd.py --list-shoes` | List Stryd shoe IDs |

## File Overview

| File | Purpose |
|------|---------|
| `auto_sync.py` | Main sync script |
| `backfill_intervals.py` | Bulk backfill for Intervals.icu |
| `create_stryd_shoes.py` | Create shoes in Stryd from a list |
| `sync_gear.py` | One-shot Garmin backfill (CSV-based) |
| `sync_stryd.py` | One-shot Stryd backfill (CSV-based) |
| `runalyze_client.py` | Runalyze session management |
| `runalyze_form.py` | Runalyze HTML form parser |
| `requirements.txt` | Python dependencies |
| `Dockerfile` | Container image |
| `docker-compose.yml` | Docker Compose config |

## Notes

- **Stryd token**: refreshes automatically using `refreshToken` + `refreshClientId` from Local Storage. If it ever stops working, re-run `--setup` and paste fresh values.
- **Runalyze**: uses form submission (no public write API). Walk/Hike activities have no gear field in Runalyze — they are silently skipped.
- **Intervals.icu**: gear is matched by display name. Make sure the gear name in Intervals.icu exactly matches what `auto_sync.py` would generate from `SHOE_MAP`.
- **Garmin MFA**: enter the code when prompted. The session is cached for ~30 days.

## License

MIT
