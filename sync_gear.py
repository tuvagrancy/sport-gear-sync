#!/usr/bin/env python3
"""Strava gear -> Garmin Connect gear sync.

Usage:
  python -I sync_gear.py --csv <path to activities.csv>            # dry run (default)
  python -I sync_gear.py --csv <path to activities.csv> --apply    # create shoes + link gear

Credentials are entered interactively in your own terminal; tokens are cached in ~/.garminconnect.
"""
import argparse
import csv
import getpass
import sys
from pathlib import Path

# ---- Config ---------------------------------------------------------------
# Strava bike name -> Garmin gear name (names not listed are assumed identical)
BIKE_NAME_MAP = {
    "Scultura 4000": "Daredevil",
}
# Bikes missing in Garmin that should be created: Garmin name -> (brand, model)
BIKE_CREATE = {
    "共享单车": ("共享单车", "共享单车"),
}
# Strava shoe name -> (brand, model). Garmin gear name = "brand model"; no nicknames.
SHOE_MAP = {
    "ASICS Nimbus24": ("ASICS", "Nimbus24"),
    "ASICS NovaBlast 3": ("ASICS", "NovaBlast 3"),
    "Adidas Adizero Evo SL": ("Adidas", "Adizero Evo SL"),
    "Altra Olympus 5 Vettle": ("Altra", "Olympus 5 Vettle"),
    "Brooks Glycerin 21": ("Brooks", "Glycerin 21"),
    "Brooks Hyperion Max": ("Brooks", "Hyperion Max"),
    "Dynafish 大鲶 安东鲵": ("Dynafish", "大鲶"),
    "Dynafish 小鲶": ("Dynafish", "小鲶"),
    "HOKA Clifton 9 后备箱里的黑户": ("HOKA", "Clifton 9"),
    "HOKA Mafate Speed 4": ("HOKA", "Mafate Speed 4"),
    "Li-ning 赤兔9U": ("Li-ning", "赤兔9U"),
    "New Balance FuelCell SC Elite V4": ("New Balance", "FuelCell SC Elite V4"),
    "New Balance Rebel v5": ("New Balance", "Rebel v5"),
    "New Balance rebel v4": ("New Balance", "Rebel v4"),
    "Nike Infinity Run 4": ("Nike", "Infinity Run 4"),
    "Nike Kiger 10": ("Nike", "Kiger 10"),
    "Nike Vomero Plus": ("Nike", "Vomero Plus"),
    "On Cloud X": ("On", "Cloud X"),
    "On Cloudboom Echo3": ("On", "Cloudboom Echo3"),
    "On Cloudsurfer": ("On", "Cloudsurfer"),
    "Saucony Endorphin Speed 3 小三": ("Saucony", "Endorphin Speed 3"),
    "Saucony Endorphin Speed 4 Berlin": ("Saucony", "Endorphin Speed 4 Berlin"),
    "Saucony Triumph 20": ("Saucony", "Triumph 20"),
    "Saucony Triumph20": ("Saucony", "Triumph 20"),
    "Saucony Trumph 21SE": ("Saucony", "Triumph 21SE"),
}
TOKENSTORE = str(Path.home() / ".garminconnect")

# ---- SECTION: Strava CSV parsing -------------------------------------------
from datetime import datetime

COLUMN_ALIASES = {
    "date": ["Activity Date", "活动日期"],
    "name": ["Activity Name", "活动名称"],
    "type": ["Activity Type", "活动类型"],
    "gear": ["Activity Gear", "活动装备", "Gear"],
}
DATE_FORMATS = ["%b %d, %Y, %I:%M:%S %p", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"]


def _find_col(header, keys):
    for k in keys:
        if k in header:
            return k
    return None


def _distance_km(row, header):
    # Strava exports two 'Distance' columns: the first is km (rounded), the later one is meters.
    idxs = [i for i, h in enumerate(header) if h in ("Distance", "距离")]
    for i in reversed(idxs):
        try:
            v = float(str(row[i]).replace(",", ""))
        except ValueError:
            continue
        # the last Distance column is meters; with a single Distance column assume km
        return v / 1000 if (len(idxs) > 1 and i == idxs[-1]) else v
    return None


def _parse_date(s):
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            pass
    raise ValueError(f"Unrecognized date format: {s!r}")


def load_strava(csv_path):
    """Return list of dicts: start (UTC naive), name, type, gear, km. Only rows with gear are kept."""
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    header, body = rows[0], rows[1:]
    cols = {k: _find_col(header, v) for k, v in COLUMN_ALIASES.items()}
    missing = [k for k, v in cols.items() if v is None]
    if missing:
        sys.exit(f"Missing columns {missing}. Header found: {header}")
    idx = {k: header.index(v) for k, v in cols.items()}
    out = []
    for r in body:
        if len(r) <= max(idx.values()) or not r[idx["gear"]].strip():
            continue
        out.append({
            "start": _parse_date(r[idx["date"]]),
            "name": r[idx["name"]],
            "type": r[idx["type"]],
            "gear": r[idx["gear"]].strip(),
            "km": _distance_km(r, header),
        })
    return out

# ---- SECTION: Garmin login -------------------------------------------------
from garminconnect import Garmin


def garmin_login():
    """Try cached tokens first; otherwise prompt for credentials in this terminal (international region)."""
    try:
        g = Garmin(is_cn=False)
        g.login(TOKENSTORE)
        return g
    except Exception:
        pass
    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password: ")
    g = Garmin(email=email, password=password, is_cn=False,
               prompt_mfa=lambda: input("Garmin MFA code: ").strip())
    g.login(TOKENSTORE)
    return g

# ---- SECTION: Matching -----------------------------------------------------
from datetime import timedelta, timezone

TIME_TOL = timedelta(minutes=3)
DIST_TOL = 0.03  # 3%


def fetch_garmin_activities(g, start, end):
    """Fetch Garmin activities between two dates (inclusive), in pages."""
    return g.get_activities_by_date(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))


def _garmin_start_utc(a):
    s = a.get("startTimeGMT")
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S") if s else None


def match_activities(strava_rows, garmin_acts):
    """Return (matched, unmatched, ambiguous). matched: list of (strava_row, garmin_activity)."""
    matched, unmatched, ambiguous = [], [], []
    for s in strava_rows:
        cands = []
        for a in garmin_acts:
            gs = _garmin_start_utc(a)
            if gs is None or abs(gs - s["start"]) > TIME_TOL:
                continue
            gkm = (a.get("distance") or 0) / 1000
            if s["km"] and gkm and abs(gkm - s["km"]) / s["km"] > DIST_TOL:
                continue
            cands.append(a)
        if len(cands) == 1:
            matched.append((s, cands[0]))
        elif not cands:
            unmatched.append(s)
        else:
            ambiguous.append((s, cands))
    return matched, unmatched, ambiguous

# ---- SECTION: Dry-run report / apply ---------------------------------------
BIKE_TYPES = {"Ride", "Virtual Ride", "VirtualRide", "E-Bike Ride", "EBikeRide", "骑行", "虚拟骑行"}


def garmin_gear_name(s):
    if s["type"] in BIKE_TYPES:
        return BIKE_NAME_MAP.get(s["gear"], s["gear"])
    if s["gear"] in SHOE_MAP:
        brand, model = SHOE_MAP[s["gear"]]
        return f"{brand} {model}"
    return s["gear"]


def load_garmin_gear(g):
    """Return {name: uuid} for existing Garmin gear (all statuses the API returns)."""
    out = {}
    # Default listing returns active gear only; retired gear needs an explicit status filter.
    for kw in ({}, {"gear_statuses": ["RETIRED"]}):
        start = 0
        while True:
            page = g.get_gear_list(start=start, limit=50, **kw)
            if not page:
                break
            for item in page:
                # Some gear (e.g. shoes created in the app) has no 'name'; the name is stored in 'brand'.
                name = item.get("name") or item.get("brand") or item.get("displayName")
                if name and name not in out:
                    out[name] = item.get("uuid")
                    if item.get("status") == "RETIRED":
                        RETIRED_NAMES.add(name)
            if len(page) < 50:
                break
            start += 50
    return out


RETIRED_NAMES = set()


import re


def _norm_uuid(u):
    return u.replace('-', '').lower() if isinstance(u, str) else None


def current_gear_uuids(g, activity_id):
    # Return the set of normalized UUIDs of gear currently linked to the activity.
    # The response shape is not documented, so collect every 'uuid' value recursively.
    # Exceptions propagate on purpose: if we cannot tell what is linked, we must not relink.
    r = g.get_activity_gear(activity_id)
    found = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k.lower() in ('uuid', 'gearuuid') and isinstance(v, str):
                    found.add(_norm_uuid(v))
                else:
                    walk(v)
        elif isinstance(x, list):
            for i in x:
                walk(i)

    walk(r)
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="Path to Strava activities.csv")
    ap.add_argument("--apply", action="store_true", help="Actually create gear and link it (default: dry run)")
    args = ap.parse_args()

    rows = load_strava(args.csv)
    if not rows:
        sys.exit("No Strava activities with gear found in the CSV.")
    print(f"Strava activities with gear: {len(rows)}")

    g = garmin_login()
    first = min(r["start"] for r in rows) - timedelta(days=1)
    last = max(r["start"] for r in rows) + timedelta(days=1)
    garmin_acts = fetch_garmin_activities(g, first, last)
    print(f"Garmin activities in range: {len(garmin_acts)}")

    matched, unmatched, ambiguous = match_activities(rows, garmin_acts)
    gear = load_garmin_gear(g)

    needed = {}
    for s, _ in matched:
        needed.setdefault(garmin_gear_name(s), []).append(s)
    missing_bikes = [n for n, ss in needed.items() if n not in gear and ss[0]["type"] in BIKE_TYPES and n in BIKE_CREATE]
    unknown_bikes = [n for n, ss in needed.items() if n not in gear and ss[0]["type"] in BIKE_TYPES and n not in BIKE_CREATE]
    missing_shoes = [n for n, ss in needed.items() if n not in gear and ss[0]["type"] not in BIKE_TYPES]
    will_create = set(missing_bikes) | set(missing_shoes)

    print("\n== Gear summary ==")
    for n, ss in sorted(needed.items()):
        status = "exists" if n in gear else ("WILL CREATE" if n in will_create else "MISSING (bike, not created)")
        print(f"  {n}: {len(ss)} activities [{status}]")
    print(f"\nMatched: {len(matched)}  Unmatched: {len(unmatched)}  Ambiguous: {len(ambiguous)}")
    for s in unmatched[:50]:
        print(f"  unmatched: {s['start']} {s['km']}km {s['gear']}")
    for s, c in ambiguous[:50]:
        print(f"  ambiguous: {s['start']} {s['km']}km {s['gear']} ({len(c)} candidates)")

    # Rule 6: plan per activity by comparing current gear with the Strava target.
    uuid_to_name = {_norm_uuid(u): n for n, u in gear.items() if u}
    plan = []  # (strava_row, garmin_activity, target_uuid, old_uuids)
    ok = add = replace = read_fail = no_target = 0
    replaced_from = {}
    retired_skipped = {}
    for s, a in matched:
        gname = garmin_gear_name(s)
        if gname in RETIRED_NAMES:
            retired_skipped[gname] = retired_skipped.get(gname, 0) + 1
            continue
        target = gear.get(gname)
        if not target and gname in will_create:
            target = "PENDING:" + gname  # real UUID is filled in after creation (--apply)
        if not target:
            no_target += 1
            continue
        try:
            cur = current_gear_uuids(g, a["activityId"])
        except Exception as e:
            read_fail += 1
            print(f"  cannot read current gear of {a['activityId']}: {e}")
            continue
        t = _norm_uuid(target)
        old = cur - {t}
        if t in cur and not old:
            ok += 1
            continue
        if old:
            replace += 1
            for u in old:
                label = uuid_to_name.get(u, u)
                replaced_from[label] = replaced_from.get(label, 0) + 1
        else:
            add += 1
        plan.append((s, a, target, old))
    print(f"\n== Rule 6 plan ==\n  already correct: {ok}\n  will add (no gear now): {add}\n  will replace: {replace}\n  cannot read current gear: {read_fail}\n  target gear missing: {no_target}")
    for label, n in sorted(replaced_from.items(), key=lambda x: -x[1]):
        print(f"    replacing {label}: {n} activities")
    for label, n in retired_skipped.items():
        print(f"  target gear RETIRED, left untouched: {label}: {n} activities")

    if not args.apply:
        print("\nDry run only. Re-run with --apply to write to Garmin.")
        return

    for n in sorted(will_create):
        ss = needed[n]
        first_use = min(x["start"] for x in ss).strftime("%Y-%m-%d")
        try:
            if n in BIKE_CREATE:
                brand, model = BIKE_CREATE[n]
                g.create_gear("BIKE", brand, model, n, first_use, notes="Imported from Strava")
                kind = "bike"
            else:
                brand, model = SHOE_MAP[ss[0]["gear"]]
                g.create_gear("SHOES", brand, model, n, first_use, notes="Imported from Strava")
                kind = "shoe"
            print(f"created {kind}: {n} first_use={first_use}")
        except Exception as e:
            print(f"FAILED to create {n}: {e}")

    # Re-read Garmin gear to get the real UUIDs, then resolve PENDING targets in the plan.
    gear = load_garmin_gear(g)
    resolved = []
    for s, a, target, old in plan:
        if target.startswith("PENDING:"):
            real = gear.get(target[len("PENDING:"):])
            if not real:
                print(f"skip {a['activityId']}: gear {target[8:]} was not created")
                continue
            target = real
        resolved.append((s, a, target, old))
    plan = resolved

    # Apply rule 6: link the correct gear first, then unlink the old one(s).
    linked = replaced = failed = unlink_failed = 0
    for s, a, target, old in plan:
        aid = a["activityId"]
        try:
            g.add_gear_to_activity(target, aid)
        except Exception as e:
            failed += 1
            print(f"failed to link {aid}: {e}")
            continue
        if not old:
            linked += 1
            continue
        ok_unlink = True
        for u in old:
            try:
                g.remove_gear_from_activity(u, aid)
            except Exception as e:
                ok_unlink = False
                unlink_failed += 1
                print(f"linked {aid} but failed to unlink old gear {u}: {e}")
        if ok_unlink:
            replaced += 1
    print(f"\nAdded: {linked}  Replaced: {replaced}  Link failed: {failed}  Unlink failed: {unlink_failed}  Already correct (untouched): {ok}")


if __name__ == "__main__":
    main()
