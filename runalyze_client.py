"""Runalyze session management.

Credentials stored in ~/.runalyze/credentials.json (0600).
Session cookie cached in ~/.runalyze/session.json (0600).
Logs never print credential or cookie values.
"""
import json
import logging
import re
import stat
import time
from pathlib import Path

import requests

BASE        = "https://runalyze.com"
LOGIN_URL   = f"{BASE}/login"
CRED_DIR    = Path.home() / ".runalyze"
CRED_FILE   = CRED_DIR / "credentials.json"
SESSION_FILE= CRED_DIR / "session.json"
TIMEOUT     = 30

log = logging.getLogger("runalyze")


# ── credential helpers ────────────────────────────────────────────────────────
def _secure_write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def load_credentials() -> dict | None:
    if CRED_FILE.exists():
        return json.loads(CRED_FILE.read_text(encoding="utf-8"))
    return None


def save_credentials(username: str, password: str):
    _secure_write(CRED_FILE, {"username": username, "password": password})


# ── session helpers ───────────────────────────────────────────────────────────
def _load_session() -> dict | None:
    if SESSION_FILE.exists():
        return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    return None


def _save_session(cookies: dict):
    _secure_write(SESSION_FILE, {"cookies": cookies, "saved_at": int(time.time())})


def _make_session(cookies: dict) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/125.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    for k, v in cookies.items():
        s.cookies.set(k, v, domain="runalyze.com")
    return s


def _do_login(username: str, password: str) -> requests.Session:
    """Perform a fresh Symfony form login; return an authenticated session."""
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    r = s.get(LOGIN_URL, timeout=TIMEOUT)
    r.raise_for_status()
    m = re.search(r'name="_csrf_token"\s+value="([^"]+)"', r.text)
    if not m:
        raise RuntimeError("Could not extract _csrf_token from login page")
    csrf = m.group(1)
    resp = s.post(LOGIN_URL, data={
        "_username":    username,
        "_password":    password,
        "_csrf_token":  csrf,
        "_remember_me": "on",
        "submit":       "",
    }, headers={"Referer": LOGIN_URL}, allow_redirects=True, timeout=TIMEOUT)
    if "/login" in resp.url:
        raise RuntimeError("Runalyze login failed — check credentials")
    log.debug("Runalyze login successful")
    _save_session(dict(s.cookies))
    return s


def _session_alive(s: requests.Session, probe_activity_id: int | None) -> bool:
    """Check whether the session is still valid."""
    if probe_activity_id is None:
        # Can't probe without an activity id; assume alive
        return True
    try:
        r = s.get(f"{BASE}/activity/{probe_activity_id}/edit",
                  timeout=TIMEOUT, allow_redirects=False)
        return r.status_code == 200 and "activity[equipment]" in r.text
    except Exception:
        return False


class RunalyzeClient:
    """Thin HTTP client for Runalyze with auto-relogin."""

    def __init__(self, probe_activity_id: int | None = None):
        self._probe = probe_activity_id
        self._session: requests.Session | None = None

    def _ensure_session(self):
        # Try cached session first
        if self._session is None:
            cached = _load_session()
            if cached:
                s = _make_session(cached["cookies"])
                if _session_alive(s, self._probe):
                    log.debug("Reusing cached Runalyze session")
                    self._session = s
                    return
        if self._session is not None and _session_alive(self._session, self._probe):
            return
        # Need fresh login
        creds = load_credentials()
        if not creds:
            raise RuntimeError(
                "No Runalyze credentials. Run --setup to configure."
            )
        self._session = _do_login(creds["username"], creds["password"])

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Execute a request with up to 3 retries on session expiry."""
        for attempt in range(3):
            self._ensure_session()
            r = self._session.request(
                method, f"{BASE}{path}",
                timeout=TIMEOUT, **kwargs
            )
            # Redirect to login = session expired
            if "/login" in r.url or r.status_code in (401, 403):
                log.debug("Session expired on attempt %d, re-logging in", attempt + 1)
                self._session = None
                time.sleep(2 * (attempt + 1))
                continue
            return r
        raise RuntimeError("Runalyze: could not establish a valid session after 3 attempts")

    def get(self, path: str, **kwargs) -> requests.Response:
        return self._request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> requests.Response:
        return self._request("POST", path, **kwargs)

    def get_json(self, path: str) -> dict | list:
        r = self.get(path, headers={"Accept": "application/json"})
        r.raise_for_status()
        return r.json()

    def get_text(self, path: str) -> str:
        r = self.get(path)
        r.raise_for_status()
        return r.text

    def get_equipment(self) -> dict:
        """Return parsed combined equipment data."""
        return self.get_json("/_internal/data/equipment/combined")

    def get_activities_csv(self) -> str:
        """Return raw CSV of all activities."""
        return self.get_text("/_internal/data/activities/all")
