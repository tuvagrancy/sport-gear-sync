"""Backfill Intervals.icu gear from Strava activities.csv.

Usage:
  python -I backfill_intervals.py --csv E:/ClaudeSpace/activities.csv           # dry run
  python -I backfill_intervals.py --csv E:/ClaudeSpace/activities.csv --apply   # write

Intervals.icu API key and athlete ID are prompted interactively.
"""
import argparse
import base64
import csv
import getpass
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

BASE    = "https://intervals.icu/api/v1"
TIMEOUT = 30

# ── Same maps as auto_sync.py ─────────────────────────────────────────────────
BIKE_TYPES = {"Ride", "Virtual Ride", "VirtualRide", "E-Bike Ride", "EBikeRide"}
BIKE_NAME_MAP = {
    "Scultura 4000": "Daredevil",
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

DATE_FORMATS = ["%b %d, %Y, %I:%M:%S %p", "%Y-%m-%d %H:%M:%S"]


def _auth(key: str) -> dict:
    token = base64.b64encode(f"API_KEY:{key}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Accept": "application/json",
            "Content-Type": "application/json"}


def gear_display_name(strava_name: str, activity_type: str) -> str:
    if activity_type in BIKE_TYPES:
        return BIKE_NAME_MAP.get(strava_name, strava_name)
    if strava_name in SHOE_MAP:
        b, m = SHOE_MAP[strava_name]
        return f"{b} {m}"
    return strava_name


def load_strava(csv_path: str) -> list[dict]:
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    header, body = rows[0], rows[1:]
    try:
        di = header.index("Activity Date")
        ti = header.index("Activity Type")
        gi = header.index("Activity Gear")
    except ValueError as e:
        sys.exit(f"Missing column: {e}")
    out = []
    for r in body:
        if len(r) <= max(di, ti, gi) or not r[gi].strip():
            continue
        for fmt in DATE_FORMATS:
            try:
                start = datetime.strptime(r[di].strip(), fmt)
                break
            except ValueError:
                continue
        else:
            continue
        out.append({"start": start, "type": r[ti].strip(), "gear": r[gi].strip()})
    return out


def fetch_all_intervals_activities(ath: str, h: dict) -> dict[int, dict]:
    """Return {garmin_activity_id: intervals_activity} for all activities, fetched year by year."""
    out: dict[int, dict] = {}
    current_year = datetime.now().year
    for year in range(2020, current_year + 1):
        oldest = f"{year}-01-01"
        newest = f"{year}-12-31"
        page = 1
        while True:
            try:
                r = requests.get(
                    f"{BASE}/athlete/{ath}/activities",
                    headers=h,
                    params={"oldest": oldest, "newest": newest,
                            "limit": 100, "page": page},
                    timeout=TIMEOUT,
                    stream=False,
                )
            except Exception as e:
                print(f"  {year} page {page}: {e} — retrying once")
                time.sleep(2)
                try:
                    r = requests.get(
                        f"{BASE}/athlete/{ath}/activities",
                        headers=h,
                        params={"oldest": oldest, "newest": newest,
                                "limit": 100, "page": page},
                        timeout=TIMEOUT,
                    )
                except Exception as e2:
                    print(f"  {year} page {page}: retry failed: {e2}")
                    break
            if r.status_code != 200:
                print(f"  {year} page {page}: HTTP {r.status_code}")
                break
            batch = r.json() or []
            for a in batch:
                ext = str(a.get("external_id") or "")
                if ext.startswith("garmin:"):
                    try:
                        out[int(ext[7:])] = a
                    except ValueError:
                        pass
                elif ext.isdigit():
                    out[int(ext)] = a
            if len(batch) < 100:
                break
            page += 1
            time.sleep(0.3)
        print(f"  {year}: {sum(1 for a in out.values() if str(a.get('start_date_local',''))[:4] == str(year))} activities")
    return out


def fetch_gear(ath: str, h: dict) -> dict[str, str]:
    """Return {display_name: gear_id}."""
    r = requests.get(f"{BASE}/athlete/{ath}/gear", headers=h, timeout=TIMEOUT)
    if r.status_code != 200:
        sys.exit(f"Gear list: {r.status_code} {r.text[:200]}")
    return {g["name"]: g["id"] for g in (r.json() or []) if g.get("name")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv",   required=True, help="Path to Strava activities.csv")
    ap.add_argument("--apply", action="store_true", help="Write to Intervals.icu (default: dry run)")
    args = ap.parse_args()

    # Load credentials from state file or prompt
    state_file = Path(__file__).parent / "auto_sync_state.json"
    state = {}
    if state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))

    ath = state.get("intervals_athlete_id", "").strip()
    key = state.get("intervals_api_key",    "").strip()

    if not ath:
        ath = input("Intervals.icu Athlete ID: ").strip()
    if not key:
        key = getpass.getpass("Intervals.icu API Key: ")

    if not ath or not key:
        sys.exit("Athlete ID and API Key are required.")

    h = _auth(key)

    # Save for future use
    state["intervals_athlete_id"] = ath
    state["intervals_api_key"]    = key
    if state_file.exists():
        state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    print("Loading Strava CSV ...")
    strava = load_strava(args.csv)
    print(f"  {len(strava)} Strava activities with gear")

    print("Loading Intervals.icu activities ...")
    icu_acts = fetch_all_intervals_activities(ath, h)
    print(f"  {len(icu_acts)} Intervals.icu activities indexed by Garmin ID")

    print("Loading Intervals.icu gear list ...")
    gear_map = fetch_gear(ath, h)
    print(f"  {len(gear_map)} gear items:")
    for name, gid in gear_map.items():
        print(f"    {gid}  {name}")

    # We need to match Strava activities to Intervals.icu via Garmin activity ID.
    # Since we don't have Garmin IDs in the CSV directly, we match by start time
    # and distance using the Garmin activity list already fetched in icu_acts
    # (intervals stores start_date_local or start_date).
    # Build a list of (start_dt_utc, garmin_id) from Intervals side:
    icu_time_index: list[tuple[datetime, int, dict]] = []
    for gid, a in icu_acts.items():
        sd = a.get("start_date_local") or a.get("start_date") or ""
        if not sd:
            continue
        try:
            dt = datetime.fromisoformat(sd.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            continue
        icu_time_index.append((dt, gid, a))

    TIME_TOL = timedelta(minutes=5)
    DIST_TOL = 0.03

    linked = skipped = no_match = no_gear = already = failed = 0
    unmatched = []

    print(f"\n{'DRY RUN' if not args.apply else 'APPLYING'} ...")

    for s in strava:
        target_name = gear_display_name(s["gear"], s["type"])
        target_id   = gear_map.get(target_name)
        if not target_id:
            no_gear += 1
            continue

        # Match by start time (allow up to 5 min; Intervals may store local time)
        hits = []
        for dt, gid, a in icu_time_index:
            diff = abs(dt - s["start"])
            # Try both UTC and +8 (CST) offsets
            diff2 = abs(dt - s["start"] - timedelta(hours=8))
            if min(diff, diff2) <= TIME_TOL:
                hits.append((gid, a))

        if not hits:
            no_match += 1
            unmatched.append(s)
            continue
        if len(hits) > 1:
            # Pick closest
            hits.sort(key=lambda x: min(
                abs(datetime.fromisoformat((x[1].get("start_date_local") or x[1].get("start_date","")).replace("Z","+00:00")).replace(tzinfo=None) - s["start"]),
                abs(datetime.fromisoformat((x[1].get("start_date_local") or x[1].get("start_date","")).replace("Z","+00:00")).replace(tzinfo=None) - s["start"] - timedelta(hours=8))
            ))

        garmin_id, icu_act = hits[0]
        icu_id = str(icu_act["id"])

        cur_gear = str((icu_act.get("gear") or {}).get("id") or "")
        if cur_gear == str(target_id):
            already += 1
            continue

        if not args.apply:
            skipped += 1
            continue

        r = requests.put(
            f"{BASE}/activity/{icu_id}",
            headers=h,
            json={"gear": {"id": str(target_id)}},
            timeout=TIMEOUT,
        )
        time.sleep(0.3)
        if r.status_code not in (200, 204):
            failed += 1
            print(f"  FAILED {s['start']:%Y-%m-%d %H:%M} [{target_name}]: {r.status_code} {r.text[:80]}")
            continue

        # Verify
        r2 = requests.get(f"{BASE}/activity/{icu_id}", headers=h, timeout=TIMEOUT)
        if r2.status_code == 200 and str((r2.json().get("gear") or {}).get("id")) == str(target_id):
            linked += 1
        else:
            failed += 1
            print(f"  VERIFY FAIL {s['start']:%Y-%m-%d %H:%M} [{target_name}]")

    print(f"\n== Result ==")
    print(f"  linked:         {linked}")
    print(f"  already correct:{already}")
    print(f"  dry-run skipped:{skipped}")
    print(f"  no gear in map: {no_gear}")
    print(f"  no match:       {no_match}")
    print(f"  failed:         {failed}")

    if unmatched:
        print(f"\nFirst 20 unmatched Strava activities (not found in Intervals.icu):")
        for s in unmatched[:20]:
            print(f"  {s['start']:%Y-%m-%d %H:%M}  {s['type']:<12}  {s['gear']}")

    if not args.apply and (skipped + linked) > 0:
        print(f"\nDry run done. Add --apply to write {skipped + linked} activities.")


if __name__ == "__main__":
    main()
