"""Strava gear -> Stryd shoe sync.

Usage:
  python -I sync_stryd.py --csv <path>              # dry run: show plan
  python -I sync_stryd.py --csv <path> --apply      # create links

Token: set env var STRYD_TOKEN before running.
"""
import argparse
import csv
import os
import sys
from datetime import datetime, timezone, timedelta

import requests

# ── Config ────────────────────────────────────────────────────────────────────
USER_ID = ""  # resolved dynamically from /users/me

def resolve_user_id(token: str, cached: str = "") -> str:
    if cached:
        return cached
    r = requests.get(f"{BASE_URL}/users/me",
                     headers=build_headers(token), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["id"]
BASE_URL = "https://api.stryd.com/b/api/v1"
TIMEOUT  = 30

# Strava activity types that go into Stryd (running only)
RUN_TYPES = {"Run", "TrailRun", "Hike", "Walk"}

# Time and distance match tolerances (same as Garmin script)
TIME_TOL = timedelta(minutes=3)
DIST_TOL = 0.03   # 3 %

# Strava shoe name -> Stryd shoe ID.
# Fill in AFTER running: python -I sync_stryd.py --csv ... --list-shoes
# Leave unknown shoes as None; the script will report them as unresolved.
# Run create_stryd_shoes.py --apply to populate these IDs for your account.
SHOE_ID_MAP: dict[str, int | None] = {
    "ASICS Nimbus24":                    None,
    "ASICS NovaBlast 3":                 None,
    "Adidas Adizero Evo SL":             None,
    "Altra Olympus 5 Vettle":            None,
    "Brooks Glycerin 21":                None,
    "Brooks Hyperion Max":               None,
    "Dynafish 大鲶 安东鲵":               None,
    "Dynafish 小鲶":                      None,
    "HOKA Clifton 9 后备箱里的黑户":       None,
    "HOKA Mafate Speed 4":               None,
    "Li-ning 赤兔9U":                     None,
    "New Balance FuelCell SC Elite V4":  None,
    "New Balance Rebel v5":              None,
    "New Balance rebel v4":              None,
    "Nike Infinity Run 4":               None,
    "Nike Kiger 10":                     None,
    "Nike Vomero Plus":                  None,
    "On Cloud X":                        None,
    "On Cloudboom Echo3":                None,
    "On Cloudsurfer":                    None,
    "Saucony Endorphin Speed 3 小三":    None,
    "Saucony Endorphin Speed 4 Berlin":  None,
    "Saucony Triumph 20":                None,
    "Saucony Triumph20":                 None,
    "Saucony Trumph 21SE":               None,
}

# ── HTTP helpers ──────────────────────────────────────────────────────────────
def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Accept":        "*/*",
        "User-Agent":    "Stryd/81545 CFNetwork/3860.100.1 Darwin/25.0.0",
    }

def _get(path: str, token: str, params: dict | None = None):
    r = requests.get(f"{BASE_URL}{path}", headers=_headers(token),
                     params=params, timeout=TIMEOUT)
    if r.status_code != 200:
        sys.exit(f"GET {path} -> {r.status_code}: {r.text[:300]}")
    return r.json()

def _put(path: str, token: str, body: dict):
    r = requests.put(f"{BASE_URL}{path}", headers=_headers(token),
                     json=body, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"PUT {path} -> {r.status_code}: {r.text[:300]}")
    return r.json()

# ── Strava CSV ────────────────────────────────────────────────────────────────
DATE_FORMATS = ["%b %d, %Y, %I:%M:%S %p", "%Y-%m-%d %H:%M:%S"]

def _parse_date(s: str) -> datetime:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            pass
    raise ValueError(f"Unrecognized date: {s!r}")

def _distance_km(row, header):
    idxs = [i for i, h in enumerate(header) if h in ("Distance", "距离")]
    for i in reversed(idxs):
        try:
            v = float(str(row[i]).replace(",", ""))
        except ValueError:
            continue
        return v / 1000 if (len(idxs) > 1 and i == idxs[-1]) else v
    return None

def load_strava(csv_path: str) -> list[dict]:
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    header, body = rows[0], rows[1:]
    try:
        di = header.index("Activity Date")
        ti = header.index("Activity Type")
        gi = header.index("Activity Gear")
    except ValueError as e:
        sys.exit(f"Missing column: {e}\nHeader: {header}")
    out = []
    for r in body:
        if len(r) <= max(di, ti, gi):
            continue
        atype = r[ti].strip()
        gear  = r[gi].strip()
        if atype not in RUN_TYPES or not gear:
            continue
        out.append({
            "start": _parse_date(r[di]),
            "type":  atype,
            "gear":  gear,
            "km":    _distance_km(r, header),
        })
    return out

# ── Stryd calendar ────────────────────────────────────────────────────────────
def fetch_stryd_activities(token: str, t_from: datetime, t_to: datetime) -> list[dict]:
    params = {
        "from":            int(t_from.replace(tzinfo=timezone.utc).timestamp()),
        "to":              int(t_to.replace(tzinfo=timezone.utc).timestamp()),
        "include_deleted": "false",
    }
    data = _get(f"/users/{resolve_user_id(token)}/calendar", token, params)
    # Calendar returns a dict keyed by date string; flatten all activities.
    if isinstance(data, dict):
        acts = []
        for v in data.values():
            if isinstance(v, list):
                acts.extend(v)
            elif isinstance(v, dict) and "id" in v:
                acts.append(v)
        return acts
    if isinstance(data, list):
        return data
    return []

def _stryd_start(a: dict) -> datetime | None:
    ts = a.get("timestamp") or a.get("start_time") or a.get("started_at")
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None

def _stryd_km(a: dict) -> float | None:
    d = a.get("distance")
    if d is None:
        return None
    return d / 1000 if d > 1000 else d

# ── Matching ──────────────────────────────────────────────────────────────────
def match(strava: list[dict], stryd: list[dict]):
    matched, unmatched, ambiguous = [], [], []
    for s in strava:
        cands = []
        for a in stryd:
            st = _stryd_start(a)
            if st is None or abs(st - s["start"]) > TIME_TOL:
                continue
            skm = _stryd_km(a)
            if s["km"] and skm and abs(skm - s["km"]) / s["km"] > DIST_TOL:
                continue
            cands.append(a)
        if len(cands) == 1:
            matched.append((s, cands[0]))
        elif not cands:
            unmatched.append(s)
        else:
            ambiguous.append((s, cands))
    return matched, unmatched, ambiguous

# ── Shoe helpers ──────────────────────────────────────────────────────────────
def fetch_shoes(token: str) -> list[dict]:
    return _get(f"/users/{resolve_user_id(token)}/apparel", token) or []

def print_shoes(shoes: list[dict]) -> None:
    print(f"{'ID':<20} {'退役':<5} {'里程':>12}  名称")
    for sh in shoes:
        m     = sh.get("apparel_model") or {}
        brand = m.get("brand") or ""
        name  = m.get("name") or ""
        nick  = sh.get("nickname") or ""
        label = f"{brand} {name}".strip()
        if nick:
            label = f"{nick}（{label}）"
        used  = (sh.get("lifetime_distance") or 0) / 1000
        lim   = ((sh.get("distance_limit") or 0) + (sh.get("additional_distance") or 0)) / 1000
        dist  = f"{used:.0f}/{lim:.0f} km" if lim else f"{used:.0f} km"
        print(f"{sh['id']:<20} {'是' if sh.get('retired') else '-':<5} {dist:>12}  {label}")

def target_shoe_id(gear: str) -> int | None:
    return SHOE_ID_MAP.get(gear)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv",        required=True, help="Strava activities.csv path")
    ap.add_argument("--apply",      action="store_true", help="Write to Stryd (default: dry run)")
    ap.add_argument("--list-shoes", action="store_true", help="Just list Stryd shoes and exit")
    args = ap.parse_args()

    token = os.environ.get("STRYD_TOKEN", "").strip()
    if not token:
        sys.exit("Set environment variable STRYD_TOKEN before running.")

    shoes = fetch_shoes(token)
    if args.list_shoes:
        print_shoes(shoes)
        return
    shoe_by_id = {sh["id"]: sh for sh in shoes}

    rows = load_strava(args.csv)
    print(f"Strava run activities with gear: {len(rows)}")

    t_from = min(r["start"] for r in rows) - timedelta(days=1)
    t_to   = max(r["start"] for r in rows) + timedelta(days=1)
    stryd_acts = fetch_stryd_activities(token, t_from, t_to)
    print(f"Stryd activities in range: {len(stryd_acts)}")

    # Debug: print one activity's keys if something looks wrong
    if stryd_acts:
        sample = stryd_acts[0]
        ts = _stryd_start(sample)
        if ts is None:
            print(f"WARNING: cannot parse timestamp from sample activity keys: {list(sample.keys())[:15]}")

    matched, unmatched, ambiguous = match(rows, stryd_acts)
    print(f"Matched: {len(matched)}  Unmatched: {len(unmatched)}  Ambiguous: {len(ambiguous)}")

    # Plan
    plan = []          # (strava_row, stryd_act, target_shoe_id, old_shoe_ids)
    already_ok = 0
    no_target  = 0
    unresolved_shoes = set()

    for s, a in matched:
        tid = target_shoe_id(s["gear"])
        if tid is None:
            unresolved_shoes.add(s["gear"])
            no_target += 1
            continue
        cur = a.get("apparel_ids") or []
        if cur == [tid]:
            already_ok += 1
            continue
        old = [x for x in cur if x != tid]
        plan.append((s, a, tid, old))

    print(f"\n== Plan ==")
    print(f"  already correct:  {already_ok}")
    print(f"  will change:      {len(plan)}")
    print(f"  shoe ID unknown:  {no_target}")
    for g in sorted(unresolved_shoes):
        print(f"    unresolved: {g}")
    for s in unmatched[:30]:
        print(f"  unmatched: {s['start']} {s['km']}km {s['gear']}")
    for s, c in ambiguous[:10]:
        print(f"  ambiguous: {s['start']} {s['km']}km ({len(c)} candidates)")

    if unresolved_shoes:
        print("\nFill in SHOE_ID_MAP in this script, then re-run.")
        print("Run with --list-shoes to see your Stryd shoe IDs.")

    if not args.apply:
        print("\nDry run. Add --apply to write.")
        return

    done = failed = 0
    for s, a, tid, old in plan:
        aid = a["id"]
        new_ids = [tid]
        try:
            _put(f"/activities/{aid}", token, {"apparel_ids": new_ids})
            done += 1
        except Exception as e:
            failed += 1
            print(f"  failed {aid}: {e}")

    print(f"\nDone: {done}  Failed: {failed}  Already correct (untouched): {already_ok}")

if __name__ == "__main__":
    main()
