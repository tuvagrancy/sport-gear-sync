"""Create Stryd shoes from the SHOE_LIST below, then print ID mapping for sync_stryd.py.

Usage:
  $env:STRYD_TOKEN = "your token"
  python -I create_stryd_shoes.py              # dry run: show what would be created
  python -I create_stryd_shoes.py --apply      # actually create shoes
"""
import argparse
import json
import os
import sys
import time

import requests

USER_ID = ""  # resolved dynamically from /users/me
BASE_URL = "https://api.stryd.com/b/api/v1"
TIMEOUT  = 30

# (brand, model, distance_limit_meters)  — 800 km default; set 0 for no limit
SHOE_LIST = [
    ("ASICS",         "Nimbus 24",                  800000),
    ("ASICS",         "NovaBlast 3",                800000),
    ("adidas",        "Adizero Evo SL",             800000),
    ("Altra",         "Olympus 5",                  800000),
    ("Brooks",        "Glycerin 21",                800000),
    ("Brooks",        "Hyperion Max",               800000),
    ("Dynafish",      "大鲶",                        800000),
    ("Dynafish",      "小鲶",                        800000),
    ("HOKA",          "Clifton 9",                  800000),
    ("HOKA",          "Mafate Speed 4",             800000),
    ("Li-Ning",       "赤兔9U",                      800000),
    ("New Balance",   "FuelCell SC Elite V4",       400000),
    ("New Balance",   "Rebel v4",                   800000),
    ("New Balance",   "Rebel v5",                   800000),
    ("Nike",          "Infinity Run 4",             800000),
    ("Nike",          "Kiger 10",                   800000),
    ("Nike",          "Vomero Plus",                800000),
    ("On",            "Cloud X",                    800000),
    ("On",            "Cloudboom Echo 3",            400000),
    ("On",            "Cloudsurfer",                800000),
    ("Saucony",       "Endorphin Speed 3",          800000),
    ("Saucony",       "Endorphin Speed 4 Berlin",   800000),
    ("Saucony",       "Triumph 20",                 1000000),
    ("Saucony",       "Triumph 21SE",               1000000),
]

def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Accept":        "*/*",
        "User-Agent":    "Stryd/81545 CFNetwork/3860.100.1 Darwin/25.0.0",
    }

def _resolve_uid(token: str) -> str:
    r = requests.get(f"{BASE_URL}/users/me",
                     headers=_headers(token), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["id"]

def fetch_existing(token):
    r = requests.get(f"{BASE_URL}/users/{_resolve_uid(g)}/apparel",
                     headers=_headers(token), timeout=TIMEOUT)
    if r.status_code != 200:
        sys.exit(f"GET apparel -> {r.status_code}: {r.text[:300]}")
    shoes = r.json() or []
    # key: (brand.lower, name.lower) -> shoe
    idx = {}
    for s in shoes:
        m = s.get("apparel_model") or {}
        key = (m.get("brand","").lower(), m.get("name","").lower())
        idx[key] = s
    return shoes, idx

def create_shoe(token, brand, name, limit):
    payload = {
        "apparel_model": {"brand": brand, "name": name, "type": "shoe"},
        "distance_limit": limit,
        "nickname": "",
    }
    r = requests.post(f"{BASE_URL}/users/{_resolve_uid(g)}/apparel",
                      headers=_headers(token), json=payload, timeout=TIMEOUT)
    return r.status_code, r.text

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Actually create shoes")
    args = ap.parse_args()

    token = os.environ.get("STRYD_TOKEN","").strip()
    if not token:
        sys.exit("Set STRYD_TOKEN first.")

    existing_shoes, existing_idx = fetch_existing(token)
    print(f"Existing shoes: {len(existing_shoes)}")

    created = []
    skipped = []
    failed  = []

    for brand, name, limit in SHOE_LIST:
        key = (brand.lower(), name.lower())
        if key in existing_idx:
            sid = existing_idx[key]["id"]
            skipped.append((brand, name, sid))
            continue
        if not args.apply:
            print(f"  DRY RUN would create: {brand} {name}  limit={limit//1000}km")
            continue
        status, body = create_shoe(token, brand, name, limit)
        time.sleep(0.3)   # be gentle
        if status == 200:
            try:
                sid = json.loads(body).get("id")
            except Exception:
                sid = "?"
            created.append((brand, name, sid))
            print(f"  created: {brand} {name} -> id={sid}")
        else:
            failed.append((brand, name, status, body[:200]))
            print(f"  FAILED:  {brand} {name} -> {status} {body[:200]}")

    if skipped:
        print(f"\nAlready exist ({len(skipped)}):")
        for b, n, sid in skipped:
            print(f"  {sid}  {b} {n}")

    if args.apply and created:
        print(f"\nCreated ({len(created)}):")
        for b, n, sid in created:
            print(f"  {sid}  {b} {n}")

    if failed:
        print(f"\nFailed ({len(failed)}):")
        for b, n, st, body in failed:
            print(f"  {b} {n} -> {st} {body}")

    if not args.apply:
        print("\nDry run done. Add --apply to create.")
        return

    # Re-fetch and print full ID mapping for sync_stryd.py
    _, new_idx = fetch_existing(token)
    print("\n# Paste this into SHOE_ID_MAP in sync_stryd.py:")
    # Strava name -> (brand, model) mapping (same as sync_gear.py SHOE_MAP)
    strava_to_model = {
        "ASICS Nimbus24":                   ("asics",       "nimbus 24"),
        "ASICS NovaBlast 3":                ("asics",       "novablast 3"),
        "Adidas Adizero Evo SL":            ("adidas",      "adizero evo sl"),
        "Altra Olympus 5 Vettle":           ("altra",       "olympus 5"),
        "Brooks Glycerin 21":               ("brooks",      "glycerin 21"),
        "Brooks Hyperion Max":              ("brooks",      "hyperion max"),
        "Dynafish 大鲶 安东鲵":              ("dynafish",    "大鲶"),
        "Dynafish 小鲶":                     ("dynafish",    "小鲶"),
        "HOKA Clifton 9 后备箱里的黑户":     ("hoka",        "clifton 9"),
        "HOKA Mafate Speed 4":              ("hoka",        "mafate speed 4"),
        "Li-ning 赤兔9U":                    ("li-ning",     "赤兔9u"),
        "New Balance FuelCell SC Elite V4": ("new balance", "fuelcell sc elite v4"),
        "New Balance Rebel v5":             ("new balance", "rebel v5"),
        "New Balance rebel v4":             ("new balance", "rebel v4"),
        "Nike Infinity Run 4":              ("nike",        "infinity run 4"),
        "Nike Kiger 10":                    ("nike",        "kiger 10"),
        "Nike Vomero Plus":                 ("nike",        "vomero plus"),
        "On Cloud X":                       ("on",          "cloud x"),
        "On Cloudboom Echo3":               ("on",          "cloudboom echo 3"),
        "On Cloudsurfer":                   ("on",          "cloudsurfer"),
        "Saucony Endorphin Speed 3 小三":   ("saucony",     "endorphin speed 3"),
        "Saucony Endorphin Speed 4 Berlin": ("saucony",     "endorphin speed 4 berlin"),
        "Saucony Triumph 20":               ("saucony",     "triumph 20"),
        "Saucony Triumph20":                ("saucony",     "triumph 20"),
        "Saucony Trumph 21SE":              ("saucony",     "triumph 21se"),
    }
    print("SHOE_ID_MAP: dict[str, int | None] = {")
    for strava_name, (b, n) in strava_to_model.items():
        shoe = new_idx.get((b, n))
        sid  = shoe["id"] if shoe else None
        print(f'    {strava_name!r:<50}: {sid},')
    print("}")

if __name__ == "__main__":
    main()
