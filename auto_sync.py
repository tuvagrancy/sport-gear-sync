#!/usr/bin/env python3
"""Automatic gear sync: Strava -> Garmin Connect + Stryd + Runalyze + Intervals.icu.

First-time setup:
  python -I auto_sync.py --setup

Subsequent runs (manual or via Task Scheduler):
  python -I auto_sync.py           # dry run: show what would change
  python -I auto_sync.py --apply   # write to Garmin + Stryd

The script only processes activities created since the last successful run,
so it is safe to run frequently (e.g. every hour).
"""
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import argparse
import getpass
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import csv
import io
import logging

import requests
from garminconnect import Garmin
from runalyze_client import RunalyzeClient, save_credentials
from runalyze_form import (
    parse_form, has_equipment_field, current_equipment,
    equipment_options, equipment_field_name, build_body,
)

logging.basicConfig(level=logging.WARNING)

# ── Runalyze config ───────────────────────────────────────────────────────────
# Category IDs for this account (from /_internal/data/equipment/combined)
RUNALYZE_SHOE_CAT = 0   # filled in at setup from the API
RUNALYZE_BIKE_CAT = 0   # filled in at setup from the API

# Garmin gear name -> Runalyze equipment id (int).
# Populated at runtime from /_internal/data/equipment/combined.
_runalyze_gear_map: dict[str, int] = {}

# Display name used by this script -> actual equipment name in Runalyze.
# Only consulted when the exact name is not found in Runalyze.
RUNALYZE_NAME_ALIASES: dict[str, str] = {
    "Nike Vomero Plus": "Nike Voremo Plus",
}
_runalyze_activity_map: dict[int, int] = {}   # garmin_activity_id -> runalyze_activity_id

# ── Intervals.icu config ───────────────────────────────────────────────────────
INTERVALS_BASE = "https://intervals.icu/api/v1"
# Strava gear name -> Intervals.icu gear id string.
# Populated at runtime from /athlete/{id}/gear.
_intervals_gear_map: dict[str, str] = {}     # garmin_display_name -> intervals gear id
_intervals_activity_map: dict[int, str] = {} # garmin_activity_id  -> intervals activity id

# ── Paths ─────────────────────────────────────────────────────────────────────
import os as _os
DIR           = Path(__file__).parent
# In Docker these are overridden via environment variables so state lives on a
# mounted volume and survives container restarts.
STATE_FILE    = Path(_os.environ.get("STATE_FILE",    str(DIR / "auto_sync_state.json")))
GARMIN_TOKENS = _os.environ.get("GARMIN_TOKENS", str(Path.home() / ".garminconnect"))

# ── Strava ────────────────────────────────────────────────────────────────────
STRAVA_BASE        = "https://www.strava.com/api/v3"
STRAVA_TOKEN_URL   = "https://www.strava.com/oauth/token"
STRAVA_AUTH_URL    = "https://www.strava.com/oauth/authorize"
STRAVA_SCOPE       = "read,activity:read_all"

# ── Garmin gear maps (kept in sync with sync_gear.py) ────────────────────────
BIKE_NAME_MAP = {
    "Scultura 4000": "Daredevil",
}
BIKE_CREATE = {
    "共享单车": ("共享单车", "共享单车"),
}
SHOE_MAP = {
    "ASICS Nimbus24":                   ("ASICS",         "Nimbus24"),
    "ASICS NovaBlast 3":                ("ASICS",         "NovaBlast 3"),
    "Adidas Adizero Evo SL":            ("Adidas",        "Adizero Evo SL"),
    "Altra Olympus 5 Vettle":           ("Altra",         "Olympus 5 Vettle"),
    "Brooks Glycerin 21":               ("Brooks",        "Glycerin 21"),
    "Brooks Hyperion Max":              ("Brooks",        "Hyperion Max"),
    "Dynafish 大鲶 安东鲵":              ("Dynafish",      "大鲶"),
    "Dynafish 小鲶":                     ("Dynafish",      "小鲶"),
    "HOKA Clifton 9 后备箱里的黑户":     ("HOKA",          "Clifton 9"),
    "HOKA Mafate Speed 4":              ("HOKA",          "Mafate Speed 4"),
    "Li-ning 赤兔9U":                    ("Li-ning",       "赤兔9U"),
    "New Balance FuelCell SC Elite V4": ("New Balance",   "FuelCell SC Elite V4"),
    "New Balance Rebel v5":             ("New Balance",   "Rebel v5"),
    "New Balance rebel v4":             ("New Balance",   "Rebel v4"),
    "Nike Infinity Run 4":              ("Nike",          "Infinity Run 4"),
    "Nike Kiger 10":                    ("Nike",          "Kiger 10"),
    "Nike Vomero Plus":                 ("Nike",          "Vomero Plus"),
    "On Cloud X":                       ("On",            "Cloud X"),
    "On Cloudboom Echo3":               ("On",            "Cloudboom Echo3"),
    "On Cloudsurfer":                   ("On",            "Cloudsurfer"),
    "Saucony Endorphin Speed 3 小三":   ("Saucony",       "Endorphin Speed 3"),
    "Saucony Endorphin Speed 4 Berlin": ("Saucony",       "Endorphin Speed 4 Berlin"),
    "Saucony Triumph 20":               ("Saucony",       "Triumph 20"),
    "Saucony Triumph20":                ("Saucony",       "Triumph 20"),
    "Saucony Trumph 21SE":              ("Saucony",       "Triumph 21SE"),
}

# ── Stryd ─────────────────────────────────────────────────────────────────────
STRYD_BASE    = "https://api.stryd.com/b/api/v1"
STRYD_USER_ID = "8f3c39b2-7e0c-5c22-5c99-0efc0c250fc5"
SHOE_ID_MAP: dict[str, int | None] = {
    "ASICS Nimbus24":                    6750464048529408,
    "ASICS NovaBlast 3":                 5591950710177792,
    "Adidas Adizero Evo SL":             5168329307095040,
    "Altra Olympus 5 Vettle":            6159116576653312,
    "Brooks Glycerin 21":                5966387905200128,
    "Brooks Hyperion Max":               4847624824717312,
    "Dynafish 大鲶 安东鲵":               4914568399585280,
    "Dynafish 小鲶":                      5483836182790144,
    "HOKA Clifton 9 后备箱里的黑户":       4741199595208704,
    "HOKA Mafate Speed 4":               5068361816768512,
    "Li-ning 赤兔9U":                     6021763925180416,
    "New Balance FuelCell SC Elite V4":  6180972457263104,
    "New Balance Rebel v5":              5167757673791488,
    "New Balance rebel v4":              4691908436590592,
    "Nike Infinity Run 4":               6343623137198080,
    "Nike Kiger 10":                     4843868473163776,
    "Nike Vomero Plus":                  5776178701828096,
    "On Cloud X":                        5178021693521920,
    "On Cloudboom Echo3":                4876987368636416,
    "On Cloudsurfer":                    6286462302453760,
    "Saucony Endorphin Speed 3 小三":    5156469023342592,
    "Saucony Endorphin Speed 4 Berlin":  4800514536407040,
    "Saucony Triumph 20":                4927135205457920,
    "Saucony Triumph20":                 4927135205457920,
    "Saucony Trumph 21SE":               6107661660487680,
}

BIKE_TYPES = {"Ride", "Virtual Ride", "VirtualRide", "E-Bike Ride", "EBikeRide"}
RUN_TYPES  = {"Run", "TrailRun", "Hike", "Walk"}
TIME_TOL   = timedelta(minutes=3)
DIST_TOL   = 0.03

# ── State ─────────────────────────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

# ── Strava auth ───────────────────────────────────────────────────────────────
def strava_refresh(state: dict) -> str:
    """Return a valid Strava access token, refreshing if expired."""
    now = int(time.time())
    if state.get("strava_expires_at", 0) > now + 60:
        return state["strava_access_token"]
    r = requests.post(STRAVA_TOKEN_URL, data={
        "client_id":     state["strava_client_id"],
        "client_secret": state["strava_client_secret"],
        "refresh_token": state["strava_refresh_token"],
        "grant_type":    "refresh_token",
    }, timeout=30)
    if r.status_code != 200:
        sys.exit(f"Strava token refresh failed: {r.status_code} {r.text[:200]}")
    t = r.json()
    state["strava_access_token"]  = t["access_token"]
    state["strava_refresh_token"] = t["refresh_token"]
    state["strava_expires_at"]    = t["expires_at"]
    save_state(state)
    return t["access_token"]

def strava_get(path: str, token: str, params: dict | None = None):
    r = requests.get(f"{STRAVA_BASE}{path}",
                     headers={"Authorization": f"Bearer {token}"},
                     params=params, timeout=30)
    if r.status_code != 200:
        sys.exit(f"Strava GET {path} -> {r.status_code}: {r.text[:200]}")
    return r.json()

def fetch_strava_activities(token: str, after_epoch: int) -> list[dict]:
    """Fetch all activities created after `after_epoch` (unix seconds)."""
    out, page = [], 1
    while True:
        batch = strava_get("/athlete/activities", token,
                           {"after": after_epoch, "per_page": 100, "page": page})
        if not batch:
            break
        out.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return out

_gear_cache: dict[str, dict] = {}
def fetch_strava_gear(gear_id: str, token: str) -> dict:
    if gear_id not in _gear_cache:
        _gear_cache[gear_id] = strava_get(f"/gear/{gear_id}", token)
    return _gear_cache[gear_id]

# ── Garmin helpers ────────────────────────────────────────────────────────────
RETIRED_NAMES: set[str] = set()

def _norm_uuid(u):
    return u.replace("-", "").lower() if isinstance(u, str) else None

def garmin_login() -> Garmin:
    try:
        g = Garmin(is_cn=False)
        g.login(GARMIN_TOKENS)
        return g
    except Exception:
        pass
    email    = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password: ")
    g = Garmin(email=email, password=password, is_cn=False,
               prompt_mfa=lambda: input("Garmin MFA code: ").strip())
    g.login(GARMIN_TOKENS)
    return g

def load_garmin_gear(g: Garmin) -> dict[str, str]:
    """Return {gear_name: uuid} for all Garmin gear (active + retired)."""
    out: dict[str, str] = {}
    for kw in ({}, {"gear_statuses": ["RETIRED"]}):
        start = 0
        while True:
            page = g.get_gear_list(start=start, limit=50, **kw)
            if not page:
                break
            for item in page:
                name = item.get("name") or item.get("brand") or item.get("displayName")
                if name and name not in out:
                    out[name] = item.get("uuid")
                    if item.get("status") == "RETIRED":
                        RETIRED_NAMES.add(name)
            if len(page) < 50:
                break
            start += 50
    return out

def garmin_gear_name(activity_type: str, strava_gear_name: str) -> str:
    if activity_type in BIKE_TYPES:
        return BIKE_NAME_MAP.get(strava_gear_name, strava_gear_name)
    if strava_gear_name in SHOE_MAP:
        brand, model = SHOE_MAP[strava_gear_name]
        return f"{brand} {model}"
    return strava_gear_name

def current_gear_uuids(g: Garmin, activity_id) -> set[str]:
    r = g.get_activity_gear(activity_id)
    found: set[str] = set()
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k.lower() in ("uuid", "gearuuid") and isinstance(v, str):
                    found.add(_norm_uuid(v))
                else:
                    walk(v)
        elif isinstance(x, list):
            for i in x:
                walk(i)
    walk(r)
    return found

def sync_garmin_activity(g: Garmin, garmin_act: dict,
                         target_name: str, gear_map: dict[str, str],
                         apply: bool) -> str:
    """Link correct gear to one Garmin activity. Returns status string."""
    if target_name in RETIRED_NAMES:
        return "retired-skip"
    target_uuid = gear_map.get(target_name)
    if not target_uuid:
        return f"no-uuid:{target_name}"
    aid = garmin_act["activityId"]
    cur = current_gear_uuids(g, aid)
    t   = _norm_uuid(target_uuid)
    if t in cur and len(cur) == 1:
        return "already-correct"
    if not apply:
        old_names = [n for n, u in gear_map.items() if _norm_uuid(u) in cur - {t}]
        return f"dry-run:would-set:{target_name}" + (f"(replacing {','.join(old_names)})" if old_names else "")
    old = cur - {t}
    g.add_gear_to_activity(target_uuid, aid)
    for u in old:
        try:
            g.remove_gear_from_activity(u, aid)
        except Exception as e:
            print(f"  warn: remove old gear {u} from {aid}: {e}")
    return "linked"

# ── Stryd helpers ─────────────────────────────────────────────────────────────
STRYD_AUTH_BASE = "https://auth.stryd.com"       # new system (Cognito)
STRYD_OLD_AUTH  = f"{STRYD_BASE}/auth/token"     # old system (Stryd backend, iss="stryd")

def _decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload without verification."""
    try:
        import base64 as _b
        payload = token.split(".")[1] + "=="
        return json.loads(_b.urlsafe_b64decode(payload.encode()))
    except Exception:
        return {}

def _stryd_is_old_auth(token: str) -> bool:
    """Return True if the token was issued by the old Stryd backend (not Cognito)."""
    return _decode_jwt_payload(token).get("iss") == "stryd"

def stryd_ensure_token(state: dict) -> str | None:
    """Return a valid Stryd access token, refreshing automatically if expired."""
    token      = state.get("stryd_token", "")
    expires_at = state.get("stryd_expires_at", 0)
    now        = int(time.time())

    # Token still valid (5 min buffer)
    if token and expires_at and expires_at > now + 300:
        return token

    refresh_token  = state.get("stryd_refresh_token", "")
    refresh_client = state.get("stryd_refresh_client_id", "")

    if not refresh_token:
        # No refresh info at all — return existing token and let caller handle 401
        return token or None

    old_auth = _stryd_is_old_auth(token) if token else bool(refresh_client and "-" in refresh_client)

    if old_auth:
        # Legacy Stryd backend (iss=stryd): POST /b/token/refresh.
        # refresh_token and Client-ID rotate on every refresh and MUST be written back.
        jwt_exp = int(_decode_jwt_payload(token).get("exp", 0)) if token else 0
        if token and jwt_exp > now + 300:
            return token
        try:
            r = requests.post(
                "https://api.stryd.com/b/token/refresh",
                headers={"Content-Type": "application/json", "Client-ID": refresh_client},
                json={"refresh_token": refresh_token, "user_id": STRYD_USER_ID},
                timeout=30,
            )
        except requests.RequestException as e:
            print(f"  Stryd token refresh network error: {type(e).__name__}, using existing token.")
            return token or None
        if r.status_code == 403:
            print("  Stryd session dead (refresh returned 403). Log in to PowerCenter again, then re-run --setup with the new refreshToken.")
            return token or None
        if r.status_code != 200:
            print(f"  Stryd token refresh failed ({r.status_code}), using existing token.")
            return token or None
        try:
            d = r.json()
            d = d.get("data") if isinstance(d.get("data"), dict) else d
            rt = d.get("refresh_token") or {}
            new_token   = d["access_token"]
            new_refresh = rt["token"]
            new_client  = rt["client"]["id"]
        except (ValueError, KeyError, TypeError, AttributeError):
            print("  Stryd refresh response malformed, using existing token.")
            return token or None
        state["stryd_token"]             = new_token
        state["stryd_expires_at"]        = int(_decode_jwt_payload(new_token).get("exp", 0)) or now + 43200
        state["stryd_refresh_token"]     = new_refresh
        state["stryd_refresh_client_id"] = str(new_client)
        save_state(state)
        print("  Stryd token refreshed (legacy auth, rotated refresh pair saved)")
        return new_token
    else:
        # New Cognito system
        r = requests.post(
            f"{STRYD_AUTH_BASE}/oauth2/token",
            data={
                "grant_type":    "refresh_token",
                "client_id":     refresh_client,
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )

    if r.status_code != 200:
        print(f"  Stryd token refresh failed ({r.status_code}), using existing token.")
        return token or None

    data       = r.json()
    new_token  = data.get("access_token") or data.get("token") or token
    expires_in = data.get("expires_in") or data.get("expiresIn") or 43200
    state["stryd_token"]      = new_token
    state["stryd_expires_at"] = now + int(expires_in)
    if "refresh_token" in data:
        state["stryd_refresh_token"] = data["refresh_token"]
    save_state(state)
    print(f"  Stryd token refreshed (expires in {int(expires_in)//3600:.0f}h)")
    return new_token

def stryd_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Accept":        "*/*",
        "User-Agent":    "Stryd/81545 CFNetwork/3860.100.1 Darwin/25.0.0",
    }

def sync_stryd_activity(stryd_act: dict, strava_gear: str,
                        stryd_token: str, apply: bool) -> str:
    tid = SHOE_ID_MAP.get(strava_gear)
    if tid is None:
        return f"no-id:{strava_gear}"
    cur = stryd_act.get("apparel_ids") or []
    if cur == [tid]:
        return "already-correct"
    if not apply:
        return f"dry-run:would-set:{tid}"
    r = requests.put(f"{STRYD_BASE}/activities/{stryd_act['id']}",
                     headers=stryd_headers(stryd_token),
                     json={"apparel_ids": [tid]}, timeout=30)
    if r.status_code != 200:
        return f"failed:{r.status_code}:{r.text[:100]}"
    return "linked"

# ── Activity matching ─────────────────────────────────────────────────────────
def _strava_start_utc(a: dict) -> datetime:
    s = a.get("start_date") or ""          # already UTC ISO-8601
    return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)

def _strava_km(a: dict) -> float:
    return (a.get("distance") or 0) / 1000

def find_match(strava_start: datetime, strava_km: float,
               candidates: list[dict], start_fn, km_fn) -> dict | None:
    hits = []
    for c in candidates:
        cs = start_fn(c)
        if cs is None or abs(cs - strava_start) > TIME_TOL:
            continue
        ckm = km_fn(c)
        if strava_km and ckm and abs(ckm - strava_km) / max(strava_km, 0.001) > DIST_TOL:
            continue
        hits.append(c)
    return hits[0] if len(hits) == 1 else None

# ── Intervals.icu helpers ─────────────────────────────────────────────────────
import base64 as _b64

def _intervals_auth(state: dict) -> dict:
    key = state.get("intervals_api_key", "")
    if not key:
        return {}
    token = _b64.b64encode(f"API_KEY:{key}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Accept": "application/json"}

def _load_intervals_maps(state: dict):
    """Populate global gear and activity maps from Intervals.icu."""
    ath = state.get("intervals_athlete_id", "")
    if not ath:
        return
    h = _intervals_auth(state)
    if not h:
        return
    # Gear list
    r = requests.get(f"{INTERVALS_BASE}/athlete/{ath}/gear", headers=h, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Intervals gear list: {r.status_code} {r.text[:100]}")
    for g in r.json() or []:
        name = (g.get("name") or "").strip()
        if name:
            _intervals_gear_map[name] = g["id"]

def _load_intervals_activities(state: dict, oldest: str, newest: str):
    """Populate garmin_activity_id -> intervals activity id for the date range."""
    ath = state.get("intervals_athlete_id", "")
    if not ath:
        return
    h = _intervals_auth(state)
    r = requests.get(
        f"{INTERVALS_BASE}/athlete/{ath}/activities",
        headers=h,
        params={"oldest": oldest, "newest": newest},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Intervals activities: {r.status_code} {r.text[:100]}")
    for a in r.json() or []:
        ext = a.get("external_id") or ""
        aid = a.get("id", "")
        # external_id is typically "garmin:<garmin_activity_id>"
        if ext.startswith("garmin:"):
            try:
                _intervals_activity_map[int(ext[7:])] = str(aid)
            except ValueError:
                pass
        elif ext.isdigit():
            _intervals_activity_map[int(ext)] = str(aid)

def _intervals_gear_name_for(strava_gear: str, activity_type: str) -> str | None:
    """Return the Intervals.icu gear display name matching this Strava gear."""
    if activity_type in BIKE_TYPES:
        candidate = BIKE_NAME_MAP.get(strava_gear, strava_gear)
    else:
        if strava_gear in SHOE_MAP:
            brand, model = SHOE_MAP[strava_gear]
            candidate = f"{brand} {model}"
        else:
            candidate = strava_gear
    return candidate if candidate in _intervals_gear_map else None

def sync_intervals_activity(state: dict,
                            garmin_activity_id: int,
                            strava_gear: str,
                            activity_type: str,
                            apply: bool) -> str:
    """Set gear on one Intervals.icu activity. Returns status string."""
    intervals_id = _intervals_activity_map.get(garmin_activity_id)
    if intervals_id is None:
        return "not-on-intervals"

    gear_name = _intervals_gear_name_for(strava_gear, activity_type)
    if not gear_name:
        return f"no-intervals-gear:{strava_gear}"
    target_id = _intervals_gear_map[gear_name]

    ath = state.get("intervals_athlete_id", "")
    h   = _intervals_auth(state)
    h["Content-Type"] = "application/json"

    # Intervals.icu stores gear as an object ({"id": ...}) and updates go to /activity/{id}
    url = f"{INTERVALS_BASE}/activity/{intervals_id}"

    # Read current gear
    r = requests.get(url, headers=h, timeout=30)
    if r.status_code != 200:
        return f"failed:read:{r.status_code}"
    cur_gear = str(((r.json() or {}).get("gear") or {}).get("id") or "")
    if cur_gear == str(target_id):
        return "already-correct"

    if not apply:
        return f"dry-run:would-set:{gear_name}"

    r2 = requests.put(url, headers=h, json={"gear": {"id": str(target_id)}}, timeout=30)
    if r2.status_code == 422 and "Strava" in r2.text:
        # Activities imported directly from Strava cannot be edited via the API
        return "skipped:strava-source"
    if r2.status_code not in (200, 204):
        return f"failed:write:{r2.status_code}:{r2.text[:80]}"

    # Verify
    r3 = requests.get(url, headers=h, timeout=30)
    if r3.status_code == 200 and str(((r3.json() or {}).get("gear") or {}).get("id")) == str(target_id):
        return "linked"
    return "failed:verify-mismatch"


def _load_runalyze_maps(client: RunalyzeClient, state: dict):
    """Populate global gear and activity maps from Runalyze."""
    global RUNALYZE_SHOE_CAT, RUNALYZE_BIKE_CAT

    combined = client.get_equipment()
    cats = {c["name"].lower(): c["id"] for c in combined.get("categories", [])}
    # Try common category name patterns
    for k, v in cats.items():
        if "shoe" in k or "lauf" in k or "鞋" in k:
            RUNALYZE_SHOE_CAT = v
        elif "bike" in k or "rad" in k or "车" in k or "cycl" in k:
            RUNALYZE_BIKE_CAT = v
    # Fall back to saved values from state
    if not RUNALYZE_SHOE_CAT:
        RUNALYZE_SHOE_CAT = state.get("runalyze_shoe_cat", 0)
    if not RUNALYZE_BIKE_CAT:
        RUNALYZE_BIKE_CAT = state.get("runalyze_bike_cat", 0)

    for eq in combined.get("equipment", []):
        name = (eq.get("name") or "").replace("‌", "").replace("﻿", "").strip()
        if name:
            _runalyze_gear_map[name] = int(eq["id"])

    # Build Garmin activity id -> Runalyze activity id map from CSV
    csv_text = client.get_activities_csv()
    reader   = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        ext = row.get("externalId", "").strip()
        rid = row.get("id", "").strip()
        if ext and rid:
            try:
                _runalyze_activity_map[int(ext)] = int(rid)
            except ValueError:
                pass


def _runalyze_cat_for_type(activity_type: str) -> int:
    if activity_type in BIKE_TYPES:
        return RUNALYZE_BIKE_CAT
    return RUNALYZE_SHOE_CAT


def sync_runalyze_activity(client: RunalyzeClient,
                           garmin_activity_id: int,
                           strava_gear_name: str,
                           activity_type: str,
                           apply: bool) -> str:
    """Bind equipment to one Runalyze activity. Returns status string."""
    runalyze_id = _runalyze_activity_map.get(garmin_activity_id)
    if runalyze_id is None:
        return "not-on-runalyze"

    # Resolve target Runalyze equipment id
    # For Garmin we used SHOE_MAP; for Runalyze we look up by the display name
    if activity_type in BIKE_TYPES:
        target_name = BIKE_NAME_MAP.get(strava_gear_name, strava_gear_name)
    else:
        if strava_gear_name in SHOE_MAP:
            brand, model = SHOE_MAP[strava_gear_name]
            target_name = f"{brand} {model}"
        else:
            target_name = strava_gear_name
    target_eq_id = (_runalyze_gear_map.get(target_name)
                    or _runalyze_gear_map.get(RUNALYZE_NAME_ALIASES.get(target_name, "")))
    if not target_eq_id:
        return f"no-runalyze-equipment:{target_name}"

    cat_id = _runalyze_cat_for_type(activity_type)
    if not cat_id:
        return "no-category-id"

    # GET edit page
    html = client.get_text(f"/activity/{runalyze_id}/edit")
    parsed = parse_form(html)

    if not has_equipment_field(parsed, cat_id):
        return "no_field"

    cur = current_equipment(parsed, cat_id)
    target_str = str(target_eq_id)
    if cur == target_str:
        return "already-correct"

    opts = equipment_options(parsed, cat_id)
    if target_str not in opts:
        return f"error:target-not-in-options:{target_name}"

    if not apply:
        return f"dry-run:would-set:{target_name}"

    overrides = {equipment_field_name(cat_id): target_str}
    body = build_body(parsed, overrides)
    resp = client.post(
        f"/activity/{runalyze_id}/edit",
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": f"https://runalyze.com/activity/{runalyze_id}/edit",
        },
    )
    if resp.status_code not in (200, 302):
        return f"failed:http{resp.status_code}"

    # Verify by re-reading
    time.sleep(0.4)
    html2  = client.get_text(f"/activity/{runalyze_id}/edit")
    parsed2 = parse_form(html2)
    if current_equipment(parsed2, cat_id) == target_str:
        return "linked"
    return "failed:verify-mismatch"

def cmd_setup():
    print("=== auto_sync setup (Enter to keep existing value) ===")
    state = load_state()

    def _prompt(label: str, key: str, secret: bool = False) -> str:
        cur = state.get(key, "")
        display = "****" if (secret and cur) else (cur[:40] + "…" if len(cur) > 40 else cur)
        hint = f" [{display}]" if cur else ""
        val = (getpass.getpass(f"  {label}{hint}: ") if secret
               else input(f"  {label}{hint}: ").strip())
        if val:
            state[key] = val
        return state.get(key, "")

    # ── Strava ────────────────────────────────────────────────────────────────
    print("\n-- Strava --")
    already_configured = bool(state.get("strava_refresh_token"))
    if already_configured:
        print("  Already configured. Press Enter to keep, or enter new values to re-authorize.")
    else:
        print("  Go to https://www.strava.com/settings/api, create an app,")
        print("  set 'Authorization Callback Domain' to 'localhost'.")

    new_id  = input(f"  Client ID [{state.get('strava_client_id','')}]: ").strip()
    new_sec = input(f"  Client Secret [{'****' if state.get('strava_client_secret') else ''}]: ").strip()
    if new_id:  state["strava_client_id"]     = new_id
    if new_sec: state["strava_client_secret"] = new_sec

    # Only re-do OAuth if credentials changed or not yet authorized
    need_oauth = new_id or new_sec or not state.get("strava_refresh_token")
    if need_oauth and state.get("strava_client_id") and state.get("strava_client_secret"):
        auth_url = (
            f"{STRAVA_AUTH_URL}?client_id={state['strava_client_id']}"
            f"&redirect_uri=http://localhost&response_type=code&scope={STRAVA_SCOPE}"
            f"&approval_prompt=force"
        )
        print(f"\n  Open this URL in your browser and authorize:")
        print(f"  {auth_url}")
        print("  Paste the full redirect URL here (Enter to skip OAuth):")
        redirect = input("  > ").strip()
        if redirect:
            code = ""
            for part in redirect.split("&"):
                if part.startswith("code=") or "?code=" in part:
                    code = part.split("=", 1)[1]
                    break
            if code:
                r = requests.post(STRAVA_TOKEN_URL, data={
                    "client_id":     state["strava_client_id"],
                    "client_secret": state["strava_client_secret"],
                    "code":          code,
                    "grant_type":    "authorization_code",
                }, timeout=30)
                if r.status_code == 200:
                    t = r.json()
                    state["strava_access_token"]  = t["access_token"]
                    state["strava_refresh_token"] = t["refresh_token"]
                    state["strava_expires_at"]    = t["expires_at"]
                    print(f"  Connected as: {t.get('athlete',{}).get('firstname','')} "
                          f"{t.get('athlete',{}).get('lastname','')}")
                else:
                    print(f"  Token exchange failed: {r.status_code} — keeping existing token.")
            else:
                print("  Could not extract code — keeping existing token.")
        else:
            print("  Skipped OAuth — keeping existing token.")
    elif already_configured:
        print("  Keeping existing Strava authorization.")

    # ── Stryd ─────────────────────────────────────────────────────────────────
    print("\n-- Stryd --")
    print("  DevTools → Application → Local Storage → https://www.stryd.com")
    print("  Copy: token, refreshToken, refreshClientId  (Enter to keep existing)")
    new_tok = input(f"  token [{'****' if state.get('stryd_token') else ''}]: ").strip()
    if new_tok:
        state["stryd_token"]     = new_tok
        state["stryd_expires_at"] = 0
    new_ref = input(f"  refreshToken [{'****' if state.get('stryd_refresh_token') else ''}]: ").strip()
    if new_ref: state["stryd_refresh_token"] = new_ref
    new_cid = input(f"  refreshClientId [{state.get('stryd_refresh_client_id','')}]: ").strip()
    if new_cid: state["stryd_refresh_client_id"] = new_cid
    if any([new_tok, new_ref, new_cid]):
        print("  Verifying Stryd token ...")
        verified = stryd_ensure_token(state)
        print("  OK." if verified else "  Could not verify — will retry at next sync.")
    else:
        print("  Keeping existing Stryd credentials.")

    # ── Runalyze ──────────────────────────────────────────────────────────────
    print("\n-- Runalyze --")
    print("  Stored in ~/.runalyze/credentials.json  (Enter to keep existing)")
    ru_user = input("  Username/email: ").strip()
    if ru_user:
        ru_pass = getpass.getpass("  Password: ")
        save_credentials(ru_user, ru_pass)
        print("  Verifying ...")
        try:
            rc = RunalyzeClient()
            rc._ensure_session()
            combined = rc.get_equipment()
            cats = {c["name"]: c["id"] for c in combined.get("categories", [])}
            print(f"  Login OK. Categories: {cats}")
            for name, cid in cats.items():
                nl = name.lower()
                if "shoe" in nl or "lauf" in nl or "鞋" in nl:
                    state["runalyze_shoe_cat"] = cid
                elif "bike" in nl or "rad" in nl or "车" in nl or "cycl" in nl:
                    state["runalyze_bike_cat"] = cid
            if not state.get("runalyze_shoe_cat"):
                cid = input("  Shoe category id: ").strip()
                state["runalyze_shoe_cat"] = int(cid)
            if not state.get("runalyze_bike_cat"):
                cid = input("  Bike category id: ").strip()
                state["runalyze_bike_cat"] = int(cid)
        except Exception as e:
            print(f"  Warning: {e}")
    else:
        print("  Keeping existing Runalyze credentials.")

    # ── Intervals.icu ─────────────────────────────────────────────────────────
    print("\n-- Intervals.icu --")
    print("  https://intervals.icu/settings → API section  (Enter to keep existing)")
    new_ath = input(f"  Athlete ID [{state.get('intervals_athlete_id','')}]: ").strip()
    new_key = input(f"  API Key [{'****' if state.get('intervals_api_key') else ''}]: ").strip()
    if new_ath: state["intervals_athlete_id"] = new_ath
    if new_key: state["intervals_api_key"]    = new_key
    if (new_ath or new_key) and state.get("intervals_athlete_id"):
        print("  Verifying ...")
        try:
            _load_intervals_maps(state)
            print(f"  OK. {len(_intervals_gear_map)} gear items:")
            for name, gid in list(_intervals_gear_map.items())[:8]:
                print(f"    {gid}  {name}")
        except Exception as e:
            print(f"  Warning: {e}")
    elif state.get("intervals_athlete_id"):
        print("  Keeping existing Intervals.icu credentials.")
    else:
        print("  Skipped.")

    # ── Garmin ────────────────────────────────────────────────────────────────
    print("\n-- Garmin --")
    print("  Credentials cached in ~/.garminconnect — prompted on first use.")

    # Only reset last_sync_epoch on truly first-time setup
    if not state.get("last_sync_epoch"):
        state["last_sync_epoch"] = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())

    save_state(state)
    print(f"\nSetup complete. State saved to {STATE_FILE}")
    print("Run 'python -I auto_sync.py' for a dry run.")

# ── Main sync ─────────────────────────────────────────────────────────────────
def cmd_sync(apply: bool):
    state = load_state()
    if not state.get("strava_client_id"):
        sys.exit("Not configured. Run: python -I auto_sync.py --setup")

    # ── Step 1: fetch Strava activities in the window ──────────────────────────
    strava_token = strava_refresh(state)
    after_epoch  = state.get("last_sync_epoch", 0)
    after_dt     = datetime.fromtimestamp(after_epoch, tz=timezone.utc)
    print(f"Fetching activities since {after_dt.strftime('%Y-%m-%d %H:%M')} UTC ...")
    strava_acts  = fetch_strava_activities(strava_token, after_epoch)
    print(f"  Strava: {len(strava_acts)} new activities")

    if not strava_acts:
        print("Nothing to sync.")
        if apply:
            state["last_sync_epoch"] = int(time.time())
            save_state(state)
        return

    # Resolve Strava gear IDs to gear names (only for activities that have gear)
    strava_gear_names: dict[str, str] = {}
    for a in strava_acts:
        gid = a.get("gear_id")
        if gid and gid not in strava_gear_names:
            strava_gear_names[gid] = fetch_strava_gear(gid, strava_token).get("name", gid)

    # ── Step 2: fetch each platform's activities for the same window ───────────
    start_date  = (after_dt - timedelta(hours=1)).strftime("%Y-%m-%d")
    end_date    = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print("\n-- Collecting platform activity data --")

    # Garmin
    garmin      = garmin_login()
    gear_map    = load_garmin_gear(garmin)
    garmin_acts = garmin.get_activities_by_date(start_date, end_date)
    print(f"  Garmin:  {len(garmin_acts)} activities")

    def garmin_start(a):
        s = a.get("startTimeGMT")
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S") if s else None
    def garmin_km(a):
        return (a.get("distance") or 0) / 1000

    # Stryd
    stryd_token = stryd_ensure_token(state)
    stryd_acts  = []
    if stryd_token:
        t_from = int((after_dt - timedelta(hours=1)).timestamp())
        t_to   = int(time.time()) + 86400
        resp = requests.get(f"{STRYD_BASE}/users/{STRYD_USER_ID}/calendar",
                            headers=stryd_headers(stryd_token),
                            params={"from": t_from, "to": t_to, "include_deleted": "false"},
                            timeout=30)
        if resp.status_code == 401:
            print("  Stryd: token still expired — skipping. Re-run --setup.")
            stryd_token = None
        elif resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                for v in data.values():
                    if isinstance(v, list):   stryd_acts.extend(v)
                    elif isinstance(v, dict) and "id" in v: stryd_acts.append(v)
            elif isinstance(data, list):
                stryd_acts = data
            print(f"  Stryd:   {len(stryd_acts)} activities")

    def stryd_start(a):
        ts = a.get("timestamp") or a.get("start_time")
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
        return None
    def stryd_km(a):
        d = a.get("distance")
        return d / 1000 if d and d > 1000 else d

    # Runalyze
    from runalyze_client import RunalyzeClient, load_credentials
    runalyze_ok = False
    rc = None
    if load_credentials():
        try:
            rc = RunalyzeClient()
            _load_runalyze_maps(rc, state)
            runalyze_ok = True
            print(f"  Runalyze: {len(_runalyze_activity_map)} activities indexed")
        except Exception as e:
            print(f"  Runalyze: unavailable ({e})")

    # Intervals.icu
    intervals_ok = False
    if state.get("intervals_athlete_id") and state.get("intervals_api_key"):
        try:
            _load_intervals_maps(state)
            _load_intervals_activities(state, start_date, end_date)
            intervals_ok = True
            print(f"  Intervals: {len(_intervals_gear_map)} gear, {len(_intervals_activity_map)} activities indexed")
        except Exception as e:
            print(f"  Intervals: unavailable ({e})")

    # ── Step 3: for each Strava activity, determine gear source and sync ───────
    print("\n-- Syncing --")
    stats = {p: {"linked": 0, "already-correct": 0, "no-match": 0, "other": 0}
             for p in ("garmin", "stryd", "runalyze", "intervals")}

    for sa in strava_acts:
        s_start = _strava_start_utc(sa)
        s_km    = _strava_km(sa)
        atype   = sa.get("type", "")

        # Determine gear name: prefer Strava (explicit user input), else skip
        gear_name = None
        if sa.get("gear_id"):
            gear_name = strava_gear_names.get(sa["gear_id"])

        # Match this activity on each platform
        gm = find_match(s_start, s_km, garmin_acts, garmin_start, garmin_km)
        sm = find_match(s_start, s_km, stryd_acts,  stryd_start,  stryd_km) \
             if atype in RUN_TYPES and stryd_token else None

        # If Strava has no gear, check if any platform already has gear set
        # (use that as the source of truth — maybe the user set it directly)
        if not gear_name:
            # Check Garmin
            if gm:
                try:
                    cur_uuids = current_gear_uuids(garmin, gm["activityId"])
                    if cur_uuids:
                        uuid_to_name = {_norm_uuid(u): n for n, u in gear_map.items() if u}
                        for u in cur_uuids:
                            garmin_name = uuid_to_name.get(u)
                            if garmin_name:
                                # Reverse-lookup Strava name from Garmin name
                                for sn, gn in [(k, f"{v[0]} {v[1]}") for k, v in SHOE_MAP.items()]:
                                    if gn == garmin_name:
                                        gear_name = sn
                                        break
                                break
                except Exception:
                    pass
            if not gear_name:
                continue   # no gear info on any platform, skip

        # ── Garmin sync ──
        if gm:
            target_name = garmin_gear_name(atype, gear_name)
            status = sync_garmin_activity(garmin, gm, target_name, gear_map, apply)
            key = status.split(":")[0]
            stats["garmin"][key if key in stats["garmin"] else "other"] += 1
            if key not in ("already-correct", "linked"):
                print(f"  Garmin [{s_start:%Y-%m-%d %H:%M}] {status}")
        else:
            stats["garmin"]["no-match"] += 1

        # ── Stryd sync ──
        if atype in RUN_TYPES:
            if not stryd_token:
                pass
            elif sm:
                status = sync_stryd_activity(sm, gear_name, stryd_token, apply)
                key = status.split(":")[0]
                stats["stryd"][key if key in stats["stryd"] else "other"] += 1
                if key not in ("already-correct", "linked"):
                    print(f"  Stryd  [{s_start:%Y-%m-%d %H:%M}] {status}")
            else:
                stats["stryd"]["no-match"] += 1

        # ── Runalyze sync ──
        if runalyze_ok and rc and gm:
            status = sync_runalyze_activity(rc, gm["activityId"], gear_name, atype, apply)
            time.sleep(0.4)
            key = status.split(":")[0]
            stats["runalyze"][key if key in stats["runalyze"] else "other"] += 1
            if key not in ("already-correct", "linked", "not-on-runalyze", "no_field"):
                print(f"  Runalyze [{s_start:%Y-%m-%d %H:%M}] {status}")

        # ── Intervals.icu sync ──
        if intervals_ok and gm:
            status = sync_intervals_activity(state, gm["activityId"], gear_name, atype, apply)
            key = status.split(":")[0]
            bucket = {"not-on-intervals": "no-match"}.get(key, key)
            stats["intervals"][bucket if bucket in stats["intervals"] else "other"] += 1
            if key not in ("already-correct", "linked", "not-on-intervals"):
                print(f"  Intervals [{s_start:%Y-%m-%d %H:%M}] {status}")

    # ── Summary ────────────────────────────────────────────────────────────────
    print("\n== Summary ==")
    for plat, s in stats.items():
        print(f"  {plat:<10} linked={s['linked']}  already-correct={s['already-correct']}"
              f"  no-match={s['no-match']}  other={s.get('other',0)}")

    if apply:
        state["last_sync_epoch"] = int(time.time())
        save_state(state)
        print(f"\nDone. Next run processes activities after {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC.")
    else:
        print("\nDry run. Add --apply to write.")

# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Auto sync Strava gear -> Garmin + Stryd")
    ap.add_argument("--setup", action="store_true", help="First-time configuration wizard")
    ap.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")
    ap.add_argument("--reset-since", metavar="DAYS",  type=int,
                    help="Re-process activities from N days ago")
    args = ap.parse_args()

    if args.setup:
        cmd_setup()
        return

    if args.reset_since:
        state = load_state()
        state["last_sync_epoch"] = int(
            (datetime.now(timezone.utc) - timedelta(days=args.reset_since)).timestamp()
        )
        save_state(state)
        print(f"Reset: will re-process activities from {args.reset_since} days ago.")

    cmd_sync(apply=args.apply)

if __name__ == "__main__":
    main()

