"""Weather dashboard: collects Netatmo readings and serves the charts.

Two modes, one program:
- local (default, on a PC): one person, no account, optional password;
- online (METEO_INVITE_CODE set, on a server): accounts, invitations and administration.
"""
import argparse
import base64
import bisect
import collections
import contextlib
import datetime
import http.cookies
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import accounts as account_tools
import alerts
import barometer
import forecast
import neighbours
import netatmo
import notify
import webpush
import stations as station_tools
import stats
import wind_estimate

ROOT = Path(__file__).resolve().parent
# In a container, METEO_DATA holds everything that changes, config.json included.
DATA = Path(os.environ.get("METEO_DATA") or ROOT / "data")
WEB = ROOT / "web"
CONFIG = DATA / "config.json" if os.environ.get("METEO_DATA") else ROOT / "config.json"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
# The local mode has a single person and no account table entry: the stations and choices belong to id 0.
LOCAL_USER = {"id": 0, "name": "Moi"}
# Paths that only make sense with accounts: the local mode does not answer them.
ONLINE_ONLY = (
    "/login.js", "/admin", "/admin.js", "/entrer", "/api/login", "/api/register", "/api/logout",
    "/api/password", "/api/invite", "/api/admin/", "/api/portail/",
)
AUTH_DELAY = 1  # seconds lost on a refused login, password or invitation code
COOKIE = "meteo_session"
HTML = "text/html; charset=utf-8"
JAVASCRIPT = "text/javascript; charset=utf-8"
PUBLIC_FILES = {
    "/login": ("login.html", HTML),
    "/login.js": ("login.js", JAVASCRIPT),
    "/theme.js": ("theme.js", JAVASCRIPT),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    # Installation comme application (téléphone, PC)
    "/manifest.json": ("manifest.json", "application/manifest+json"),
    "/sw.js": ("sw.js", JAVASCRIPT),
    "/appli.js": ("appli.js", JAVASCRIPT),
    "/ciel.js": ("ciel.js", JAVASCRIPT),
    "/icone-192.png": ("icone-192.png", "image/png"),
    "/icone-512.png": ("icone-512.png", "image/png"),
    "/favicon.ico": ("icone-192.png", "image/png"),
}
# Connexion depuis un portail externe (facultatif : seulement si portail.key existe) : le portail vérifie le mot de passe par le réseau Docker
# interne avec une clé secrète, reçoit un code à usage unique, et le navigateur l'échange sur /entrer.
PORTAL_KEY_FILE = DATA / "portail.key"
PORTAL_CODE_SECONDS = 60
portal_codes = {}
portal_lock = threading.Lock()


def portal_key():
    try:
        return PORTAL_KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def new_portal_code(user_id):
    code = secrets.token_urlsafe(24)
    now = time.time()
    with portal_lock:
        for old in [k for k, (_, until) in portal_codes.items() if until < now]:
            del portal_codes[old]
        portal_codes[code] = (user_id, now + PORTAL_CODE_SECONDS)
    return code


def take_portal_code(code):
    with portal_lock:
        user_id, until = portal_codes.pop(code, (None, 0))
    return user_id if until >= time.time() else None
PRIVATE_FILES = {
    "/": ("index.html", HTML),
    "/guide": ("guide.html", HTML),
    "/app.js": ("app.js", JAVASCRIPT),
}
ADMIN_FILES = {
    "/admin": ("admin.html", HTML),
    "/admin.js": ("admin.js", JAVASCRIPT),
}
MAX_POINTS = 1500
MAX_STATIONS = 8  # per user: one distinct colour per station
MAX_TOTAL_STATIONS = 30  # for the whole server, to protect the collection
MAX_BODY = 4096
STATION_GAP = 5  # seconds between the live readings of two stations
MIN_WAIT = 60  # shortest pause between two polls, in seconds
HISTORY_EVERY = 3600  # seconds between two checks for missing history
HISTORY_NEAR_LIVE = netatmo.HISTORY_STEP // 2  # a live reading already covers this span
DEFAULT_HISTORY_DAYS = 730  # two years, so that a period can be compared with last year
RAIN_RECHECK = 3 * 86400  # recent daily rain totals can still change
DAY = 86400
DAILY_MIN_SPAN = 7 * DAY  # shorter periods always show the detailed measures
MAX_NAME = 40
# A wide area is useless: Netatmo returns a limited sample of its stations, so a
# station disappears from large requests. Several small neighbouring areas find it.
SEARCH_STEP = 0.1  # degrees between two areas: one area covers 0.1 degree
SEARCH_RINGS = 2
MAX_SEARCH_AREAS = 25
SEARCH_PAUSE = 1  # seconds between two areas
COLUMNS = netatmo.FIELDS
# Peaks must survive when old data is grouped into larger time buckets.
AGGREGATES = {"rain_60min": "MAX", "rain_24h": "MAX", "gust_strength": "MAX"}
RANGE_FIELDS = ("temperature", "humidity", "pressure", "wind_strength")
WIND_ARCHIVE_DELAY = 6 * DAY  # the archive service stops a few days before today
WIND_RECENT_DAYS = 7  # covers that delay on the first fetch
WIND_RECHECK_DAYS = 2
WIND_MARGIN = 2 * DAY  # older data is only requested when more than this is missing
ALERTS_EVERY = 9 * 60  # seconds between two checks of the official warnings
NEIGHBOURS_KEEP = 2 * 3600  # stop fetching neighbours this long after the option is left off
SETTING_KEY = re.compile(r"^meteo-[a-z-]{1,30}$")
MAX_SETTINGS = 30
MAX_SETTING_LENGTH = 300
INVITE_BYTES = 9  # 12 characters, impossible to guess
APP_VERSION = "2.4"
NOTIFY_TEST_EVERY = 60  # seconds between two test messages of one person
NOTIFY_MAX_PER_POLL = 5  # messages per person and per collection, at most
MAX_DEVICES = 10  # devices with notifications, per person
PUSH_SHOWN = 30 * 60  # a device that wakes up shows the messages of the last half hour
DEMO_DAYS = 800
DEMO_DETAILED_DAYS = 14
DEMO_STATIONS = [
    {"id": "demo:1", "name": "Valence", "lat": 44.93, "lon": 4.89, "place": "Démo"},
    {"id": "demo:2", "name": "Boulot", "lat": 44.95, "lon": 4.91, "place": "Démo"},
]
MAX_LOG = 200  # lines kept in the error log
SHOWN_LOG = 50
NETATMO_HOURLY_LIMIT = 500  # order of magnitude of Netatmo's limit, only shown as a gauge
LIMIT_RANGES = {
    "total_stations": ("Stations au total", 1, 100),
    "user_stations": ("Stations par personne", 1, MAX_STATIONS),
    "poll_minutes": ("Minutes entre deux collectes", 5, 60),
}
MAX_ANNOUNCEMENT = 300
ANNOUNCEMENT_HOURS = (0, 1, 6, 24, 72, 168)  # 0: shown until the administrator removes it
# The display choices of web/app.js with the same defaults, to count who uses what.
DISPLAY_OPTIONS = (
    ("meteo-hours", "Météo sur les cartes", False),
    ("meteo-forecast", "Prévisions", True),
    ("meteo-forecast-open", "Prévisions ouvertes", False),
    ("meteo-alerts", "Alertes météo", False),
    ("meteo-barometer", "Baromètre", True),
    ("meteo-stats", "Stats", False),
    ("meteo-nearby", "Stations voisines", False),
    ("meteo-sky", "Soleil et lune", True),
    ("meteo-daily", "Min/max par jour", True),
    ("meteo-compare", "Comparer à l’an dernier", False),
)
THEME_NAMES = {"auto": "Automatique", "clair": "Clair", "sombre": "Sombre", "ciel": "Ciel"}
PERIOD_NAMES = {
    "24": "24 h", "72": "3 jours", "168": "7 jours", "360": "15 jours",
    "720": "30 jours", "8760": "1 an", "0": "Tout",
}
DEFAULT_PERIOD = "24"
# Loaded before everything else, so that the page starts with the choices of the account.
SETTINGS_SCRIPT = """'use strict';
(function (saved) {
  if (!saved) return; // an account that never chose anything keeps what this browser has
  try {
    Object.keys(localStorage).filter((key) => key.startsWith('meteo-')).forEach((key) => localStorage.removeItem(key));
    Object.entries(saved).forEach(([key, value]) => localStorage.setItem(key, value));
  } catch {
    /* the page then simply starts with its defaults */
  }
}(%s));
"""


class StationError(Exception):
    """A station cannot be added; the message is shown to the user."""


class InviteError(Exception):
    """An invitation code is refused; the message is shown to the user."""

    def __init__(self, message="Code d'invitation incorrect ou déjà utilisé"):
        super().__init__(message)


def is_configured(station):
    return bool(
        station_tools.MAC.match(station.get("id") or "")
        and isinstance(station.get("lat"), (int, float))
        and isinstance(station.get("lon"), (int, float))
    )


def search_areas(places):
    """Return the areas to search: every place first, then rings around them."""
    areas = list(places)
    for ring in range(1, SEARCH_RINGS + 1):
        for place in places:
            for north in range(-ring, ring + 1):
                for east in range(-ring, ring + 1):
                    if max(abs(north), abs(east)) == ring:
                        areas.append({
                            **place,
                            "lat": place["lat"] + north * SEARCH_STEP,
                            "lon": place["lon"] + east * SEARCH_STEP,
                        })
    return areas[:MAX_SEARCH_AREAS]


def free_slot(stations):
    used = {station.get("slot") for station in stations}
    return next(slot for slot in range(MAX_STATIONS) if slot not in used)


def demo_reading(index, ts):
    """Simulated values, only used with --demo."""
    day = (ts % 86400) / 86400
    base = 9 + index * 1.5
    temperature = (
        base + 5 * math.sin(2 * math.pi * (day - 0.35))
        + 2.5 * math.sin(ts / 267000 + index)
        + 0.25 * math.sin(ts / 2300)
    )
    rain = max(0.0, math.sin(ts / 133000 + index * 0.7) - 0.9) * 22
    reading = dict.fromkeys(COLUMNS)
    reading.update(
        ts=ts,
        temperature=round(temperature, 1),
        humidity=round(min(98, max(35, 76 - 3 * (temperature - base) + rain * 3))),
        pressure=round(1016 + 7 * math.sin(ts / 198000 + index * 0.4), 1),
        rain_60min=round(rain, 1),
        rain_24h=round(rain * 3, 1),
    )
    if index == 1:
        wind = abs(14 * math.sin(ts / 90000)) + 3
        reading.update(
            wind_strength=round(wind),
            gust_strength=round(wind * 1.7),
            wind_angle=round((ts / 900) % 360),
            gust_angle=round((ts / 900) % 360),
        )
    return reading


def demo_forecast(index):
    """Simulated forecast, only used with --demo."""
    today = datetime.date.today()
    codes = (2, 3, 61, 80, 95, 71, 0)
    days = [
        {
            "date": (today + datetime.timedelta(days=day)).isoformat(),
            "code": codes[(day + index) % len(codes)],
            "max": 16 - day + index,
            "min": 6 - day / 2,
            "rain": (day % 3) * 2.4,
            "rain_chance": (day % 3) * 35,
            "wind": 12 + day * 3,
            "gust": 22 + day * 5,
            # Roughly a November day in the south of France, a little shorter each day.
            "sunrise": f"{(today + datetime.timedelta(days=day)).isoformat()}T07:{50 + day // 2:02d}",
            "sunset": f"{(today + datetime.timedelta(days=day)).isoformat()}T17:{27 - day // 2:02d}",
            "daylight": 34620 - day * 125,
        }
        for day in range(forecast.DAYS)
    ]
    start = datetime.datetime.now().replace(minute=0, second=0, microsecond=0)
    hours = [
        {
            "time": (start + datetime.timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%M"),
            "code": codes[(hour // 3 + index) % len(codes)],
            "temperature": 12 + 4 * math.sin(hour / 4),
            "is_day": 1 if 7 <= (start.hour + hour) % 24 < 18 else 0,
            "rain_chance": (hour % 5) * 15,
            "wind": 10 + hour,
        }
        for hour in range(forecast.HOURS)
    ]
    return {"code": codes[index], "is_day": True, "days": days, "hours": hours, "fetched": int(time.time())}


def demo_alerts(index):
    """Simulated warnings, only used with --demo."""
    if index:
        return []
    now = int(time.time())
    return [
        {"area": "Démo", "level": "orange", "hazard": "pluie-inondation", "icon": "🌧️", "start": now - 3600, "end": now + 9 * 3600},
        {"area": "Démo", "level": "yellow", "hazard": "vent", "icon": "💨", "start": now + 3600, "end": now + 6 * 3600},
    ]


def demo_comparison(index, radius):
    """Simulated comparison with the neighbours, only used with --demo."""
    return {
        "radius": radius, "count": 4 * radius + index,
        "gaps": {"temperature": 1.2 - index, "humidity": -6, "pressure": 6.4 if index else 0.3},
        "low": 6.1, "high": 11.8, "warmer_than": 72 - 30 * index,
    }


def demo_neighbours(index, radius):
    """Simulated medians, only used with --demo."""
    return {
        "radius": radius, "count": 4 * radius + index,
        "temperature": 8.4 + index, "humidity": 86, "pressure": 1016.2,
    }


class Dashboard:
    def __init__(self, config, local=False, demo=False):
        self.local = local or demo
        self.demo = demo
        self.config = config
        self.default_poll_minutes = max(5, int(config.get("poll_minutes", 10)))
        self.started = int(time.time())
        self.history_days = max(1, int(config.get("history_days", DEFAULT_HISTORY_DAYS)))
        self.history_errors = {}
        self.history_wait = {}
        self.wind_errors = {}
        self.wind_wait = {}
        self.modules = {}
        self.forecasts = {}
        self.forecast_errors = {}
        self.forecast_wait = {}
        self.alerts = {}
        self.alert_wait = 0
        self.logged = {}  # the problems already written in the error log
        self.first_code = ""  # the code of the installation: the administrator's first one
        self.invite_lock = threading.Lock()
        self.departments = {}
        self.neighbours = {}
        self.neighbours_wanted = {}
        self.neighbours_wait = {}
        self.edit_lock = threading.Lock()
        self.db_path = DATA / ("demo.db" if demo else "meteo.db")
        self.tokens = netatmo.Tokens(
            DATA / "token.json",
            config.get("client_id", ""),
            config.get("client_secret", ""),
            config.get("refresh_token", ""),
        )
        self.oauth_state = None  # local mode: the Netatmo connection is made from the page
        self.smtp = notify.smtp_settings(config, os.environ, self.saved_mailbox())
        self._push_keys = None
        self.test_sent = {}  # user id -> time of the last test message
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.status = {
            "last_poll": None, "next_poll": None, "error": None, "stations": {},
            "history": {}, "history_running": None, "history_rows": 0, "wind": {}, "forecast": {},
            "alerts_error": None, "notify_error": None,
        }
        with self.connect() as db:
            columns = ", ".join(f"{name} REAL" for name in COLUMNS)
            db.execute(
                f"CREATE TABLE IF NOT EXISTS measures (station_id TEXT NOT NULL, "
                f"ts INTEGER NOT NULL, {columns}, PRIMARY KEY (station_id, ts)) WITHOUT ROWID"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS history_progress "
                "(station_id TEXT PRIMARY KEY, done_until INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS history_start "
                "(station_id TEXT PRIMARY KEY, start_ts INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS rain_days (station_id TEXT NOT NULL, "
                "day TEXT NOT NULL, rain REAL NOT NULL, PRIMARY KEY (station_id, day)) WITHOUT ROWID"
            )
            columns = ", ".join(f"{name} REAL" for name in wind_estimate.FIELDS)
            db.execute(
                f"CREATE TABLE IF NOT EXISTS wind_estimates (station_id TEXT NOT NULL, "
                f"ts INTEGER NOT NULL, {columns}, PRIMARY KEY (station_id, ts)) WITHOUT ROWID"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS stations (id TEXT PRIMARY KEY, lat REAL NOT NULL, "
                "lon REAL NOT NULL, box REAL, place TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS user_stations (user_id INTEGER NOT NULL, "
                "station_id TEXT NOT NULL, name TEXT NOT NULL, slot INTEGER NOT NULL, "
                "PRIMARY KEY (user_id, station_id)) WITHOUT ROWID"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS user_settings (user_id INTEGER PRIMARY KEY, settings TEXT NOT NULL)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS invites (user_id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE)")
            db.execute("CREATE TABLE IF NOT EXISTS options (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS notify_settings (user_id INTEGER PRIMARY KEY, settings TEXT NOT NULL)"
            )
            # The events already sent and not over yet, so that each one is sent once.
            db.execute(
                "CREATE TABLE IF NOT EXISTS notify_state (user_id INTEGER NOT NULL, station_id TEXT NOT NULL, "
                "key TEXT NOT NULL, since INTEGER NOT NULL, PRIMARY KEY (user_id, station_id, key)) WITHOUT ROWID"
            )
            # Devices where a person allowed the site's notifications, and the texts they fetch.
            db.execute(
                "CREATE TABLE IF NOT EXISTS push_subscriptions (endpoint TEXT PRIMARY KEY, "
                "user_id INTEGER NOT NULL, origin TEXT NOT NULL, created INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS push_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "user_id INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, ts INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS error_log (ts INTEGER NOT NULL, kind TEXT NOT NULL, "
                "station_id TEXT NOT NULL, message TEXT NOT NULL)"
            )
            columns = [row["name"] for row in db.execute("PRAGMA table_info(user_stations)")]
            if "position" not in columns:  # databases created before stations could be reordered
                db.execute("ALTER TABLE user_stations ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
        self.accounts = account_tools.Accounts(self.connect)
        if demo:
            self.seed_demo()
        elif not self.local:
            # A database prepared on a PC (preparer_vps.py) holds the PC's list: no account owns it here.
            with self.connect() as db:
                db.execute("DELETE FROM user_stations WHERE user_id = ?", (LOCAL_USER["id"],))
        elif not self.option("local_imported"):
            # The stations of a version 1.x are in config.json: they move to the database once.
            self.import_config_stations(LOCAL_USER["id"])
            self.set_option("local_imported", "1")

    @property
    def stations(self):
        """The stations at least one user follows: only these are collected."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT id, lat, lon, box, place FROM stations "
                "WHERE id IN (SELECT station_id FROM user_stations) ORDER BY id"
            ).fetchall()
        return [{key: row[key] for key in row.keys() if row[key] is not None} for row in rows]

    def user_stations(self, user_id):
        with self.connect() as db:
            rows = db.execute(
                "SELECT stations.id, stations.lat, stations.lon, stations.place, user_stations.name, user_stations.slot "
                "FROM user_stations JOIN stations ON stations.id = user_stations.station_id "
                "WHERE user_stations.user_id = ? ORDER BY user_stations.position, user_stations.slot",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def register(self, name, password, invited_by=None):
        """Create an account; the very first one receives the stations of config.json."""
        is_first = self.accounts.count() == 0
        user_id = self.accounts.register(name, password, invited_by)
        if is_first:
            self.import_config_stations(user_id)
        return user_id

    def is_admin(self, user):
        return self.accounts.is_admin(user["id"])

    def is_owner(self, user):
        return user["id"] == self.accounts.admin_id()

    def admin_users(self):
        users = self.accounts.list_users()
        names = {user["id"]: user["name"] for user in users}
        with self.connect() as db:
            codes = {row["user_id"]: row["code"] for row in db.execute("SELECT user_id, code FROM invites")}
        owner = self.accounts.admin_id()
        for user in users:
            user["role"] = "owner" if user["id"] == owner else "admin" if user.pop("is_admin") else "user"
            user["code"] = codes.get(user["id"])
            if user["invited_by"]:
                user["invited_by"] = names.get(user["invited_by"], "compte supprimé")
            user["stations"] = [
                {"name": station["name"], "url": station_tools.weathermap_url(station["id"])}
                for station in self.user_stations(user["id"])
            ]
        return users

    def delete_user(self, user_id):
        """Remove an account and its choices; shared measures stay in the database."""
        with self.edit_lock, self.connect() as db:
            db.execute("DELETE FROM user_stations WHERE user_id = ?", (user_id,))
            db.execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))
            db.execute("DELETE FROM invites WHERE user_id = ?", (user_id,))
            db.execute("DELETE FROM notify_settings WHERE user_id = ?", (user_id,))
            db.execute("DELETE FROM notify_state WHERE user_id = ?", (user_id,))
            db.execute("DELETE FROM push_subscriptions WHERE user_id = ?", (user_id,))
            db.execute("DELETE FROM push_messages WHERE user_id = ?", (user_id,))
        self.accounts.delete_user(user_id)

    @property
    def poll_seconds(self):
        return self.limits()["poll_minutes"] * 60

    def option(self, name):
        with self.connect() as db:
            row = db.execute("SELECT value FROM options WHERE name = ?", (name,)).fetchone()
        return None if row is None else row["value"]

    def set_option(self, name, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO options (name, value) VALUES (?, ?)", (name, value))

    def announcement(self, now=None):
        """Return the message for everyone and its end (None: until it is removed); nothing once it is over."""
        text = self.option("announcement") or ""
        until = self.option("announcement_until")
        until = int(until) if until else None
        if until is not None and (now or time.time()) >= until:
            return "", None
        return text, until

    def set_announcement(self, text, hours):
        """hours = 0 keeps the message until the administrator removes it."""
        until = int(time.time()) + hours * 3600 if text and hours else None
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO options (name, value) VALUES ('announcement', ?)", (text,))
            db.execute(
                "INSERT OR REPLACE INTO options (name, value) VALUES ('announcement_until', ?)",
                ("" if until is None else str(until),),
            )

    def limits(self):
        """The limits chosen by the administrator, or the defaults."""
        defaults = {
            "total_stations": MAX_TOTAL_STATIONS,
            "user_stations": MAX_STATIONS,
            "poll_minutes": self.default_poll_minutes,
        }
        with self.connect() as db:
            saved = {row["name"]: row["value"] for row in db.execute("SELECT name, value FROM options")}
        limits = {name: int(saved.get(name, default)) for name, default in defaults.items()}
        limits["neighbours_allowed"] = saved.get("neighbours_allowed", "1") == "1"
        return limits

    def save_limits(self, form):
        values = {}
        for name, (label, low, high) in LIMIT_RANGES.items():
            value = form.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{label} : un nombre entier de {low} à {high}")
            values[name] = str(value)
        values["neighbours_allowed"] = "1" if form.get("neighbours_allowed") else "0"
        for name, value in values.items():
            self.set_option(name, value)
        self.wake.set()  # a new pace of collection applies right away

    def option_usage(self):
        """How many people use each display choice, counting the defaults of those who changed nothing."""
        with self.connect() as db:
            saved = {
                row["user_id"]: json.loads(row["settings"])
                for row in db.execute("SELECT user_id, settings FROM user_settings")
            }
            people = [row["id"] for row in db.execute("SELECT id FROM users")]
        choices = [saved.get(user_id, {}) for user_id in people]

        def counted(key, names, default):
            counts = collections.Counter(choice.get(key, default) for choice in choices)
            return [{"label": label, "count": counts[value]} for value, label in names.items() if counts[value]]

        return {
            "people": len(people),
            "options": [
                {"label": label, "count": sum((choice.get(key, "1" if default else "0") == "1") for choice in choices)}
                for key, label, default in DISPLAY_OPTIONS
            ],
            "themes": counted("meteo-theme", THEME_NAMES, "auto"),
            "periods": counted("meteo-period", PERIOD_NAMES, DEFAULT_PERIOD),
        }

    def overview(self):
        """What the administrator needs to watch over the server."""
        now = int(time.time())
        with self.lock:
            status = dict(self.status)
        with self.connect() as db:
            rows = db.execute(
                "SELECT stations.id, stations.place, COUNT(*) AS followers, MIN(user_stations.name) AS name "
                "FROM stations JOIN user_stations ON user_stations.station_id = stations.id "
                "GROUP BY stations.id ORDER BY name"
            ).fetchall()
            stations = [
                {
                    "name": row["name"],
                    "place": row["place"],
                    "followers": row["followers"],
                    "url": station_tools.weathermap_url(row["id"]),
                    "last": db.execute("SELECT MAX(ts) FROM measures WHERE station_id = ?", (row["id"],)).fetchone()[0],
                    "error": status["stations"].get(row["id"]) or status["history"].get(row["id"]),
                }
                for row in rows
            ]
        with self.connect() as db:
            names = {
                row["id"]: row["name"] or row["place"] or row["id"] for row in db.execute(
                    "SELECT stations.id, stations.place, MIN(user_stations.name) AS name FROM stations "
                    "LEFT JOIN user_stations ON user_stations.station_id = stations.id GROUP BY stations.id"
                )
            }
            log = [
                {"ts": row["ts"], "kind": row["kind"], "station": names.get(row["station_id"], row["station_id"]), "message": row["message"]}
                for row in db.execute(
                    "SELECT ts, kind, station_id, message FROM error_log ORDER BY ts DESC, rowid DESC LIMIT ?", (SHOWN_LOG,)
                )
            ]
            abandoned = [
                {
                    "id": row["id"],
                    "place": row["place"],
                    "measures": row["measures"],
                    "last": row["last"],
                }
                for row in db.execute(
                    "SELECT stations.id, stations.place, COUNT(measures.ts) AS measures, MAX(measures.ts) AS last "
                    "FROM stations LEFT JOIN measures ON measures.station_id = stations.id "
                    "WHERE stations.id NOT IN (SELECT station_id FROM user_stations) GROUP BY stations.id"
                )
            ]
        files = (self.db_path, Path(f"{self.db_path}-wal"))
        disk = shutil.disk_usage(DATA)
        return {
            "version": APP_VERSION,
            "now": now,
            "started": self.started,
            "setup": self.setup,
            "last_poll": status["last_poll"],
            "next_poll": status["next_poll"],
            "error": status["error"],
            "alerts_error": status["alerts_error"],
            "notify_error": status["notify_error"],
            "email_available": bool(self.smtp),
            "mailbox": {"address": self.smtp["user"] if self.smtp else None, "from_page": self.saved_mailbox() is not None},
            "notify": self.notify_usage(),
            "netatmo_calls": netatmo.calls_last_hour(),
            "netatmo_limit": NETATMO_HOURLY_LIMIT,
            "database_bytes": sum(path.stat().st_size for path in files if path.exists()),
            "disk_free": disk.free,
            "disk_total": disk.total,
            "users": self.accounts.count(),
            "online": self.accounts.online_count(),
            "usage": self.option_usage(),
            "limits": self.limits(),
            "announcement": self.announcement(now)[0],
            "announcement_until": self.announcement(now)[1],
            "stations": stations,
            "log": log,
            "abandoned": abandoned,
        }

    def invites_open(self):
        """Whether everyone may invite; otherwise only the administrator's code works."""
        with self.connect() as db:
            row = db.execute("SELECT value FROM options WHERE name = 'invites_open'").fetchone()
        return row is None or row["value"] == "1"

    def set_invites_open(self, is_open):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO options (name, value) VALUES ('invites_open', ?)",
                ("1" if is_open else "0",),
            )

    def store_code(self, db, user_id, code=None):
        code = code or secrets.token_urlsafe(INVITE_BYTES)
        db.execute("INSERT OR REPLACE INTO invites (user_id, code) VALUES (?, ?)", (user_id, code))
        return code

    def invite_code(self, user_id):
        """Return the code a person can share, created the first time it is asked for."""
        with self.edit_lock, self.connect() as db:
            row = db.execute("SELECT code FROM invites WHERE user_id = ?", (user_id,)).fetchone()
            if row:
                return row["code"]
            is_first = (
                user_id == self.accounts.admin_id() and self.first_code
                and not db.execute("SELECT 1 FROM invites WHERE code = ?", (self.first_code,)).fetchone()
            )
            return self.store_code(db, user_id, self.first_code if is_first else None)

    def renew_code(self, user_id):
        with self.edit_lock, self.connect() as db:
            return self.store_code(db, user_id)

    def code_owner(self, code):
        """Return who owns a code, None for the very first account, or refuse it."""
        if not code:
            raise InviteError()
        admin = self.accounts.admin_id()
        with self.connect() as db:
            row = db.execute("SELECT user_id FROM invites WHERE code = ?", (code,)).fetchone()
            if row:
                return row["user_id"]
            admin_has_code = admin is not None and db.execute("SELECT 1 FROM invites WHERE user_id = ?", (admin,)).fetchone()
        if self.first_code and not admin_has_code and secrets.compare_digest(code.encode(), self.first_code.encode()):
            return admin
        raise InviteError()

    def register_with_code(self, code, name, password):
        """Create an account; the code used is replaced, so that it only works once."""
        with self.invite_lock:  # one registration at a time, so that a code serves once
            owner = self.code_owner(code)
            if owner is not None and not self.accounts.is_admin(owner) and not self.invites_open():
                raise InviteError("Les invitations sont fermées pour le moment")
            user_id = self.register(name, password, owner)
            if owner is not None:
                with self.connect() as db:
                    self.store_code(db, owner)
        return user_id

    def user_settings(self, user_id):
        """Return the display choices of an account, or None if it never made any."""
        with self.connect() as db:
            row = db.execute("SELECT settings FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
        return json.loads(row["settings"]) if row else None

    def save_settings(self, user_id, settings):
        is_valid = isinstance(settings, dict) and len(settings) <= MAX_SETTINGS and all(
            SETTING_KEY.match(key) and isinstance(value, str) and len(value) <= MAX_SETTING_LENGTH
            for key, value in settings.items()
        )
        if not is_valid:
            raise ValueError("Réglages refusés")
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO user_settings (user_id, settings) VALUES (?, ?)",
                (user_id, json.dumps(settings)),
            )

    def notification_settings(self, user_id):
        """The notification choices of a person, or the defaults."""
        with self.connect() as db:
            row = db.execute("SELECT settings FROM notify_settings WHERE user_id = ?", (user_id,)).fetchone()
            if row:
                return {**notify.default_settings(), **json.loads(row["settings"])}
            settings = notify.default_settings()
            db.execute(
                "INSERT INTO notify_settings (user_id, settings) VALUES (?, ?)", (user_id, json.dumps(settings)),
            )
            return settings

    def devices(self, user_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT endpoint, origin FROM push_subscriptions WHERE user_id = ?", (user_id,),
            )]

    def notifications_page(self, user_id, may_set_mailbox):
        return {
            **self.notification_settings(user_id),
            "email_available": bool(self.smtp),
            "devices": len(self.devices(user_id)),
            "stations_known": [{"id": s["id"], "name": s["name"]} for s in self.user_stations(user_id)],
            "push_key": self.push_keys.application_key,
            "mailbox": {
                "address": self.smtp["user"] if self.smtp else None,
                "may_change": may_set_mailbox,
                "from_page": self.saved_mailbox() is not None,
            },
            "events_known": [
                {"key": key, "label": label, "unit": unit, "limits": notify.LIMITS.get(key)}
                for key, (label, _, unit) in notify.EVENTS.items()
            ],
        }

    def save_notifications(self, user_id, form):
        settings = notify.clean_settings(form, self.notification_settings(user_id))
        mine = {station["id"] for station in self.user_stations(user_id)}
        settings["stations"] = [station_id for station_id in settings["stations"] if station_id in mine]
        if settings["email"] and not self.smtp:
            raise notify.NotifyError("L'envoi d'e-mails n'est pas configuré sur ce serveur")
        with self.connect() as db:
            db.execute(
                "UPDATE notify_settings SET settings = ? WHERE user_id = ?", (json.dumps(settings), user_id),
            )
            # Events left aside are forgotten: switched on again, they are sent at once if still going on.
            for key in set(notify.EVENTS) - set(settings["events"]):
                pattern = "vigilance:%" if key == "vigilance" else key
                db.execute("DELETE FROM notify_state WHERE user_id = ? AND key LIKE ?", (user_id, pattern))
        return settings

    @property
    def push_keys(self):
        """The site's own key for its notifications, created the first time it is needed."""
        if self._push_keys is None:
            self._push_keys = webpush.Keys(DATA / "vapid.json")
        return self._push_keys

    def subscribe(self, user_id, endpoint, origin):
        """Remember a device (phone, PC) where this person allowed the site's notifications."""
        if not webpush.is_push_address(endpoint):
            raise notify.NotifyError("Ce navigateur n'est pas pris en charge")
        with self.connect() as db:
            if db.execute("SELECT COUNT(*) FROM push_subscriptions WHERE user_id = ?", (user_id,)).fetchone()[0] >= MAX_DEVICES:
                raise notify.NotifyError(f"{MAX_DEVICES} appareils au plus : désactivez-en un d'abord")
            db.execute(
                "INSERT OR REPLACE INTO push_subscriptions (endpoint, user_id, origin, created) VALUES (?, ?, ?, ?)",
                (endpoint, user_id, origin, int(time.time())),
            )

    def unsubscribe(self, user_id, endpoint):
        with self.connect() as db:
            db.execute("DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?", (user_id, endpoint))

    def pending_messages(self, user_id, now=None):
        """The texts the service worker shows after an empty push: the most recent ones."""
        now = int(now or time.time())
        with self.connect() as db:
            db.execute("DELETE FROM push_messages WHERE ts < ?", (now - DAY,))
            rows = db.execute(
                "SELECT id, title, body, ts FROM push_messages WHERE user_id = ? AND ts >= ? ORDER BY id DESC LIMIT 5",
                (user_id, now - PUSH_SHOWN),
            ).fetchall()
        return [dict(row) for row in rows]

    def push(self, user_id, title, message, urgent):
        """Keep the text for the service worker, then wake up each device. Return an error or None."""
        devices = self.devices(user_id)
        with self.connect() as db:
            db.execute(
                "INSERT INTO push_messages (user_id, title, body, ts) VALUES (?, ?, ?, ?)",
                (user_id, title, message, int(time.time())),
            )
        problems, delivered = [], 0
        for device in devices:
            try:
                webpush.send(self.push_keys, device["endpoint"], device["origin"], urgent)
                delivered += 1
            except webpush.Expired:
                self.unsubscribe(user_id, device["endpoint"])  # the browser gave it up
            except webpush.PushError as err:
                problems.append(str(err))
        if delivered:
            return None
        return problems[0] if problems else "Plus aucun appareil : réactivez les notifications sur votre appareil"

    def deliver(self, user_id, settings, title, message, urgent=False):
        """Send on every channel the person uses; return {channel: error or None}."""
        results = {}
        if self.devices(user_id):
            results["push"] = self.push(user_id, title, message, urgent)
        if settings["email"]:
            try:
                notify.send_email(self.smtp, settings["address"], title, message)
                results["email"] = None
            except notify.NotifyError as err:
                results["email"] = str(err)
        return results

    def send_test(self, user_id):
        now = time.time()
        if now - self.test_sent.get(user_id, 0) < NOTIFY_TEST_EVERY:
            raise notify.NotifyError("Un test par minute au plus : patientez un peu")
        settings = self.notification_settings(user_id)
        if not (self.devices(user_id) or settings["email"]):
            raise notify.NotifyError("Activez d'abord les notifications sur cet appareil, ou l'e-mail")
        self.test_sent[user_id] = now
        return self.deliver(user_id, settings, "Test : notifications météo", "Les notifications arrivent bien ici.")

    def notify_usage(self):
        """How many people chose alerts, and on how many devices: for the administration."""
        with self.connect() as db:
            rows = db.execute("SELECT settings FROM notify_settings").fetchall()
            devices = db.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0]
        chosen = [json.loads(row["settings"]) for row in rows]
        return {
            "people": sum(bool(item.get("events")) and bool(item.get("stations")) for item in chosen),
            "email": sum(bool(item.get("email")) for item in chosen),
            "devices": devices,
        }

    def test_mailbox(self, address):
        """Send a test email from the mailbox of the site: one a minute at most."""
        now = time.time()
        if now - self.test_sent.get("mailbox", 0) < NOTIFY_TEST_EVERY:
            raise notify.NotifyError("Un test par minute au plus : patientez un peu")
        if not notify.ADDRESS.match(address.strip()):
            raise notify.NotifyError("Adresse e-mail incorrecte")
        self.test_sent["mailbox"] = now
        notify.send_email(self.smtp, address.strip(), "Test : e-mails météo",
                          "La boîte d'envoi du site marche : les alertes par e-mail peuvent partir.")

    def saved_mailbox(self):
        try:
            return json.loads((DATA / "smtp.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save_mailbox(self, user, password):
        """Check the Gmail app password, then keep it in the data folder (never shown again)."""
        mailbox = notify.check_mailbox(user, password)
        path = DATA / "smtp.json"
        path.write_text(json.dumps(mailbox), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        self.smtp = notify.smtp_settings(self.config, os.environ, mailbox)

    def remove_mailbox(self):
        (DATA / "smtp.json").unlink(missing_ok=True)
        self.smtp = notify.smtp_settings(self.config, os.environ)

    def check_notifications(self, now=None):
        """Send what has just started for each person; called after each collection."""
        now = int(now or time.time())
        with self.connect() as db:
            rows = db.execute("SELECT user_id, settings FROM notify_settings").fetchall()
        problems = []
        for row in rows:
            user_id = row["user_id"]
            if self.local != (user_id == LOCAL_USER["id"]):
                continue  # the list of the other mode, in a database copied from a PC
            settings = {**notify.default_settings(), **json.loads(row["settings"])}
            if not (self.devices(user_id) or settings["email"]) or not settings["events"]:
                continue
            sent = 0
            chosen = set(settings["stations"])
            for station in self.user_stations(user_id):
                if station["id"] not in chosen:
                    continue
                with self.connect() as db:
                    latest = db.execute(
                        "SELECT * FROM measures WHERE station_id = ? ORDER BY ts DESC LIMIT 1", (station["id"],),
                    ).fetchone()
                    active = {
                        item["key"] for item in db.execute(
                            "SELECT key FROM notify_state WHERE user_id = ? AND station_id = ?",
                            (user_id, station["id"]),
                        )
                    }
                events = notify.weather_events(
                    settings, station, dict(latest) if latest else None, self.alerts.get(station["id"], []), now, active,
                )
                with self.connect() as db:
                    for key in active - set(events):  # over: it may be sent again next time
                        db.execute(
                            "DELETE FROM notify_state WHERE user_id = ? AND station_id = ? AND key = ?",
                            (user_id, station["id"], key),
                        )
                for key, (title, message) in events.items():
                    if key in active or sent >= NOTIFY_MAX_PER_POLL:
                        continue
                    results = self.deliver(user_id, settings, title, message, urgent=key.startswith("vigilance"))
                    problems += [error for error in results.values() if error]
                    if any(error is None for error in results.values()):
                        sent += 1
                        with self.connect() as db:
                            db.execute(
                                "INSERT OR REPLACE INTO notify_state (user_id, station_id, key, since) VALUES (?, ?, ?, ?)",
                                (user_id, station["id"], key, now),
                            )
        with self.lock:
            self.status["notify_error"] = problems[0][:200] if problems else None

    def import_config_stations(self, user_id, wanted=None):
        """Give an account the stations of config.json, in their order and with their colours."""
        if wanted is None:
            wanted = [s for s in self.config.get("stations", []) if is_configured(s)]
        wanted = wanted[:MAX_STATIONS]
        given = []
        for station in wanted:
            slot = station.get("slot")
            is_free = slot in range(MAX_STATIONS) and all(other["slot"] != slot for other in given)
            given.append({**station, "slot": slot if is_free else None})
        for station in given:
            if station["slot"] is None:
                station["slot"] = free_slot(given)
        with self.edit_lock, self.connect() as db:
            for position, station in enumerate(given):
                db.execute(
                    "INSERT OR IGNORE INTO stations (id, lat, lon, box, place) VALUES (?, ?, ?, ?, ?)",
                    (station["id"], station["lat"], station["lon"], station.get("box"), station.get("place")),
                )
                db.execute(
                    "INSERT OR IGNORE INTO user_stations (user_id, station_id, name, slot, position) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (user_id, station["id"], station.get("name") or station["id"], station["slot"], position),
                )
        self.wake.set()

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def insert(self, db, station_id, reading):
        names = ", ".join(COLUMNS)
        marks = ", ".join("?" for _ in COLUMNS)
        db.execute(
            f"INSERT OR IGNORE INTO measures (station_id, ts, {names}) VALUES (?, ?, {marks})",
            (station_id, reading["ts"], *(reading[name] for name in COLUMNS)),
        )

    def store_rain(self, db, station_id, days):
        db.executemany(
            "INSERT OR REPLACE INTO rain_days (station_id, day, rain) VALUES (?, ?, ?)",
            [(station_id, day, mm) for day, mm in days.items()],
        )

    def store_wind(self, db, station_id, rows):
        names = ", ".join(wind_estimate.FIELDS)
        db.executemany(
            f"INSERT OR REPLACE INTO wind_estimates (station_id, ts, {names}) VALUES (?, ?, ?, ?, ?)",
            [(station_id, ts, *(values[name] for name in wind_estimate.FIELDS)) for ts, values in rows.items()],
        )

    def seed_demo(self):
        """Simulated stations and two years of measures, written once in demo.db."""
        self.forecasts = {station["id"]: demo_forecast(i) for i, station in enumerate(DEMO_STATIONS)}
        self.alerts = {station["id"]: demo_alerts(i) for i, station in enumerate(DEMO_STATIONS)}
        if not self.user_stations(LOCAL_USER["id"]):
            self.import_config_stations(LOCAL_USER["id"], DEMO_STATIONS)
        with self.connect() as db:
            if db.execute("SELECT 1 FROM measures LIMIT 1").fetchone():
                return
            start = int(time.time()) // 3600 * 3600
            self.store_wind(db, DEMO_STATIONS[0]["id"], {
                ts: {name: demo_reading(1, ts)[name] for name in wind_estimate.FIELDS}
                for ts in range(start - DEMO_DAYS * DAY, start + 1, 3600)
            })
            now = int(time.time()) // 600 * 600
            detailed = now - DEMO_DETAILED_DAYS * DAY
            gap = range(now - 3 * DAY, now - 3 * DAY + 5 * 3600)
            stamps = [*range(now - DEMO_DAYS * DAY, detailed, netatmo.HISTORY_STEP), *range(detailed, now + 1, 600)]
            for index, station in enumerate(DEMO_STATIONS):
                rain = {}
                for ts in stamps:
                    if index == 1 and ts in gap:
                        continue
                    reading = demo_reading(index, ts)
                    self.insert(db, station["id"], reading)
                    day = datetime.date.fromtimestamp(ts).isoformat()
                    step = netatmo.HISTORY_STEP if ts < detailed else 600
                    rain[day] = rain.get(day, 0) + reading["rain_60min"] * step / 3600
                self.store_rain(db, station["id"], {day: round(mm, 1) for day, mm in rain.items()})

    def refuse_in_demo(self, action):
        if self.demo:
            raise StationError(f"{action} indisponible en mode démonstration")

    @property
    def setup(self):
        if self.demo:
            return "ok"
        if not (self.config.get("client_id") and self.config.get("client_secret")):
            return "config"
        return "ok" if self.tokens.connected else "auth"

    def store_history(self, station_id, rows):
        """Add history rows, leaving the periods covered by live readings alone."""
        step = netatmo.HISTORY_STEP
        stored = 0
        with self.connect() as db:
            live = [
                row[0] for row in db.execute(
                    "SELECT ts FROM measures WHERE station_id = ? AND ts % ? != 0 ORDER BY ts",
                    (station_id, step),
                )
            ]
            for ts in sorted(rows):
                near = bisect.bisect_left(live, ts - HISTORY_NEAR_LIVE)
                if near < len(live) and live[near] <= ts + HISTORY_NEAR_LIVE:
                    continue
                values = rows[ts]
                reading = {**dict.fromkeys(COLUMNS), **values, "ts": ts}
                if values.get("rain_30min") is not None:
                    previous = rows.get(ts - step, {}).get("rain_30min") or 0
                    reading["rain_60min"] = round(values["rain_30min"] + previous, 3)
                self.insert(db, station_id, reading)
                stored += 1
        return stored

    def history_plan(self, station_id, now):
        """Return the periods of measures to fetch and the start of the rain totals.

        Covers what is missing since the last check and, once, anything older than
        what is stored, up to `history_days`.
        """
        target = now - self.history_days * DAY
        with self.connect() as db:
            done = db.execute(
                "SELECT done_until FROM history_progress WHERE station_id = ?", (station_id,)
            ).fetchone()
            start = db.execute(
                "SELECT start_ts FROM history_start WHERE station_id = ?", (station_id,)
            ).fetchone()
            oldest = db.execute(
                "SELECT MIN(ts) FROM measures WHERE station_id = ?", (station_id,)
            ).fetchone()[0]
        if done is None:
            return [(target, now)], target
        windows = [(done[0] - HISTORY_EVERY, now)]
        if start is not None and start[0] <= target:
            return windows, now - RAIN_RECHECK
        older_end = start[0] if start is not None else oldest or now
        if older_end > target:
            windows.append((target, older_end))
        return windows, target

    def backfill(self, station, modules):
        """Fetch what is missing, then remember how far the history goes."""
        now = int(time.time())
        station_id = station["id"]
        if now < self.history_wait.get(station_id, 0):
            return False
        self.history_wait[station_id] = now + HISTORY_EVERY
        windows, rain_begin = self.history_plan(station_id, now)
        with self.lock:
            self.status["history_running"] = station_id
        try:
            rows = {}
            for begin, end in windows:
                for kind, module_id in modules.items():
                    sensor = netatmo.fetch_history(self.tokens, station_id, kind, module_id, begin, end)
                    for ts, values in sensor.items():
                        rows.setdefault(ts, {}).update(values)
            rain = {}
            if "rain" in modules:
                rain = netatmo.fetch_daily_rain(self.tokens, station_id, modules["rain"], rain_begin, now)
            stored = self.store_history(station_id, rows)
            with self.connect() as db:
                self.store_rain(db, station_id, rain)
                db.execute(
                    "INSERT OR REPLACE INTO history_progress (station_id, done_until) VALUES (?, ?)",
                    (station_id, now),
                )
                if rain_begin == now - self.history_days * DAY:
                    db.execute(
                        "INSERT OR REPLACE INTO history_start (station_id, start_ts) VALUES (?, ?)",
                        (station_id, rain_begin),
                    )
            self.history_errors.pop(station_id, None)
        finally:
            with self.lock:
                self.status["history_running"] = None
        with self.lock:
            self.status["history_rows"] += stored
        return True

    def collect_live(self, station):
        reading = netatmo.fetch_station(self.tokens, station)
        self.modules[station["id"]] = reading["modules"]
        with self.connect() as db:
            self.insert(db, station["id"], reading)
        self.refine_position(station, reading.get("location"))

    def refine_position(self, station, location):
        """Use the station's exact position, so later requests cover a smaller area."""
        if "box" in station or not location:
            return
        lon, lat = location
        with self.connect() as db:
            db.execute(
                "UPDATE stations SET lat = ?, lon = ?, box = ? WHERE id = ?",
                (lat, lon, netatmo.EXACT_BOX_DEG, station["id"]),
            )

    def collect_history(self):
        """Work on one station per poll, so history never crowds out live readings."""
        for station in self.stations:
            modules = self.modules.get(station["id"])
            if not modules:
                continue
            try:
                if self.backfill(station, modules):
                    return
            except netatmo.AuthRequired:
                raise
            except Exception as err:  # live readings keep working
                self.history_errors[station["id"]] = str(err)[:200]
                if isinstance(err, netatmo.ApiError) and err.status >= 500:
                    self.history_wait.pop(station["id"], None)  # try again at the next poll
                return

    def fetch_wind(self, station, now):
        """Return the estimated wind that is missing for a station, up to now."""
        target = now - self.history_days * DAY
        with self.connect() as db:
            oldest = db.execute(
                "SELECT MIN(ts) FROM wind_estimates WHERE station_id = ?", (station["id"],)
            ).fetchone()[0]
        lat, lon = station["lat"], station["lon"]
        rows = {}
        if oldest is None or oldest - target > WIND_MARGIN:
            last = min(oldest or now, now - WIND_ARCHIVE_DELAY)
            rows.update(wind_estimate.fetch_archive(
                lat, lon,
                datetime.date.fromtimestamp(target).isoformat(),
                datetime.date.fromtimestamp(last).isoformat(),
            ))
        days = WIND_RECENT_DAYS if oldest is None else WIND_RECHECK_DAYS
        rows.update(wind_estimate.fetch_recent(lat, lon, days))
        return {ts: values for ts, values in rows.items() if ts <= now}

    def collect_wind(self):
        """Estimate the wind of stations that have no anemometer, once an hour."""
        now = int(time.time())
        for station in self.stations:
            station_id = station["id"]
            modules = self.modules.get(station_id)
            if not modules or "wind" in modules or now < self.wind_wait.get(station_id, 0):
                continue
            self.wind_wait[station_id] = now + HISTORY_EVERY
            try:
                rows = self.fetch_wind(station, now)
                with self.connect() as db:
                    self.store_wind(db, station_id, rows)
                self.wind_errors.pop(station_id, None)
            except (OSError, ValueError, KeyError) as err:  # measures keep working
                self.wind_errors[station_id] = str(err)[:200]

    def collect_forecast(self):
        """Refresh the sky and the forecast of each station once an hour."""
        now = int(time.time())
        for station in self.stations:
            station_id = station["id"]
            if now < self.forecast_wait.get(station_id, 0):
                continue
            self.forecast_wait[station_id] = now + HISTORY_EVERY
            try:
                reading = forecast.fetch_forecast(station["lat"], station["lon"])
                self.forecasts[station_id] = {**reading, "fetched": now}
                self.forecast_errors.pop(station_id, None)
            except (OSError, ValueError, KeyError, TypeError) as err:  # the last forecast stays shown
                self.forecast_errors[station_id] = str(err)[:200]
        with self.lock:
            self.status["forecast"] = dict(self.forecast_errors)

    def department_of(self, station):
        station_id = station["id"]
        if station_id not in self.departments:
            self.departments[station_id] = alerts.find_department(station["lat"], station["lon"])
        return self.departments[station_id]

    def collect_alerts(self):
        """Refresh the official warnings of each station at most every few minutes."""
        now = int(time.time())
        if now < self.alert_wait:
            return
        self.alert_wait = now + ALERTS_EVERY
        try:
            france = alerts.fetch_feed("france", now)
            andorra = None
            found = {}
            for station in self.stations:
                if alerts.in_andorra(station["lat"], station["lon"]):
                    andorra = alerts.fetch_feed("andorra", now) if andorra is None else andorra
                    found[station["id"]] = alerts.for_area(andorra, andorra[0]["area"]) if andorra else []
                else:
                    area = self.department_of(station)
                    found[station["id"]] = alerts.for_area(france, area) if area else []
            self.alerts = found
            error = None
        except (OSError, ValueError, KeyError, IndexError, alerts.ET.ParseError) as err:
            error = str(err)[:200]  # the last known warnings stay shown
        with self.lock:
            self.status["alerts_error"] = error

    def collect_neighbours(self):
        """Fetch the stations around those whose medians were asked for recently."""
        now = int(time.time())
        for station in self.stations:
            station_id = station["id"]
            is_wanted = now - self.neighbours_wanted.get(station_id, 0) < NEIGHBOURS_KEEP
            if not is_wanted or now < self.neighbours_wait.get(station_id, 0):
                continue
            self.neighbours_wait[station_id] = now + HISTORY_EVERY
            try:
                self.neighbours[station_id] = neighbours.fetch_around(self.tokens, station["lat"], station["lon"])
            except netatmo.AuthRequired:
                raise
            except Exception:  # an extra indication only: the card simply goes without it
                self.neighbours_wait[station_id] = now + self.poll_seconds

    def neighbours_summary(self, station, radius, now):
        """Return the medians of a station, or None when the option is off."""
        if not radius or not self.limits()["neighbours_allowed"]:
            return None
        if self.demo:
            return demo_neighbours(station.get("slot", 0), radius)
        station_id = station["id"]
        is_new = station_id not in self.neighbours_wanted
        self.neighbours_wanted[station_id] = now
        readings = self.neighbours.get(station_id)
        if readings is None:
            if is_new:
                self.wake.set()  # no need to wait for the next poll
            return {"radius": radius, "pending": True}
        return neighbours.summarise(readings, station, radius, now)

    def neighbours_comparison(self, station, latest, radius, now):
        if not radius or not latest or not self.limits()["neighbours_allowed"]:
            return None
        if self.demo:
            return demo_comparison(station.get("slot", 0), radius)
        readings = self.neighbours.get(station["id"])
        return None if readings is None else neighbours.compare(readings, station, latest, radius, now)

    def publish(self, errors, general):
        with self.lock:
            self.status.update(last_poll=int(time.time()), error=general, stations=errors)

    def poll(self):
        if self.demo:
            ts = int(time.time()) // 600 * 600
            with self.connect() as db:
                for index, station in enumerate(DEMO_STATIONS):
                    self.insert(db, station["id"], demo_reading(index, ts))
            self.publish({}, None)
            return
        errors = {}
        general = None
        self.collect_forecast()  # first: it is quick, and the sky shows right after a restart
        self.collect_alerts()
        if self.setup == "ok":
            for index, station in enumerate(self.stations):
                if index:
                    time.sleep(STATION_GAP)
                try:
                    self.collect_live(station)
                except netatmo.AuthRequired as err:
                    general = str(err)
                    break
                except Exception as err:  # keep collecting: the next poll may work
                    errors[station["id"]] = str(err)[:200]
        self.publish(errors, general)
        if self.setup == "ok" and general is None:
            try:
                self.collect_neighbours()
                self.collect_history()
            except netatmo.AuthRequired as err:
                self.publish(errors, str(err))
            self.collect_wind()
        with self.lock:
            self.status["history"] = dict(self.history_errors)
            self.status["wind"] = dict(self.wind_errors)
        try:
            self.check_notifications()
        except Exception as err:  # the collection goes on whatever happens to the messages
            with self.lock:
                self.status["notify_error"] = str(err)[:200]
        self.log_changes()

    def log_changes(self):
        """Write each new collection problem, and its end, in the error log; repeats are left out."""
        with self.lock:
            status = dict(self.status)
        current = {
            ("Netatmo", ""): status["error"],
            ("Alertes météo", ""): status["alerts_error"],
            ("Notifications", ""): status["notify_error"],
        }
        for kind, key in (("Mesures", "stations"), ("Historique", "history"), ("Vent estimé", "wind"), ("Prévisions", "forecast")):
            for station_id, message in status[key].items():
                current[(kind, station_id)] = message
        now = int(time.time())
        entries = []
        for spot in sorted(set(current) | set(self.logged)):
            message, before = current.get(spot), self.logged.get(spot)
            if message and message != before:
                entries.append((now, *spot, message))
            elif before and not message:
                entries.append((now, *spot, "Rétabli"))
        self.logged = {spot: message for spot, message in current.items() if message}
        if entries:
            with self.connect() as db:
                db.executemany("INSERT INTO error_log (ts, kind, station_id, message) VALUES (?, ?, ?, ?)", entries)
                db.execute(
                    "DELETE FROM error_log WHERE rowid NOT IN "
                    "(SELECT rowid FROM error_log ORDER BY ts DESC, rowid DESC LIMIT ?)",
                    (MAX_LOG,),
                )

    def forget_station(self, station_id):
        """Delete everything stored about a station that nobody follows any more."""
        with self.edit_lock, self.connect() as db:
            if db.execute("SELECT 1 FROM user_stations WHERE station_id = ?", (station_id,)).fetchone():
                raise StationError("Quelqu'un suit encore cette station")
            if not db.execute("SELECT 1 FROM stations WHERE id = ?", (station_id,)).fetchone():
                raise StationError("Station inconnue")
            for table in ("measures", "wind_estimates", "rain_days", "history_progress", "history_start", "error_log"):
                db.execute(f"DELETE FROM {table} WHERE station_id = ?", (station_id,))
            db.execute("DELETE FROM stations WHERE id = ?", (station_id,))
        for cache in (self.modules, self.forecasts, self.alerts, self.neighbours, self.departments):
            cache.pop(station_id, None)
        compact = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        try:
            compact.execute("VACUUM")  # gives the space back to the disk
        finally:
            compact.close()

    def backup_to(self, path):
        """Write a consistent copy of the database, taken while the site keeps running."""
        target = sqlite3.connect(path)
        try:
            with self.connect() as db:
                db.backup(target)
        finally:
            target.close()

    def locate(self, station_id, places):
        """Look for the station around each place, then in the areas next to them.

        Only the exact station id is accepted, so a neighbour can never be picked up.
        """
        if self.setup != "ok":
            return places[0], None
        for index, area in enumerate(search_areas(places)):
            if index:
                time.sleep(SEARCH_PAUSE)
            try:
                return area, netatmo.fetch_station(self.tokens, {"id": station_id, **area})
            except netatmo.StationNotFound:
                continue
        raise StationError(
            f"Station introuvable autour de {places[0]['label']}. "
            "Indiquez une commune plus proche ou des coordonnées."
        )

    def find_new_station(self, station_id, place):
        """Return a row for the stations table and its first reading, if any."""
        if len(self.stations) >= self.limits()["total_stations"]:
            raise StationError("Le serveur suit déjà le maximum de stations")
        try:
            places = station_tools.find_places(place)
        except OSError:
            raise StationError("Recherche de la commune impossible : réessayez ou indiquez des coordonnées") from None
        if not places:
            raise StationError("Lieu introuvable : essayez avec le code postal ou des coordonnées")
        try:
            chosen, reading = self.locate(station_id, places)
        except (netatmo.ApiError, netatmo.AuthRequired, OSError) as err:
            raise StationError(f"Netatmo ne répond pas correctement : {err}") from None
        row = {"id": station_id, "lat": chosen["lat"], "lon": chosen["lon"], "box": None, "place": chosen["label"]}
        location = reading.get("location") if reading else None
        if location:
            row.update(lon=location[0], lat=location[1], box=netatmo.EXACT_BOX_DEG)
        return row, reading

    def add_station(self, user_id, link, name, place):
        self.refuse_in_demo("Ajout")
        station_id = station_tools.parse_station_id(link)
        if not station_id:
            raise StationError("Lien non reconnu : il doit venir de la carte Netatmo et contenir stationid=…")
        mine = self.user_stations(user_id)
        if any(station["id"] == station_id for station in mine):
            raise StationError("Cette station est déjà dans votre liste")
        most = self.limits()["user_stations"]
        if len(mine) >= most:
            raise StationError(f"{most} stations au maximum")
        with self.connect() as db:
            known = db.execute("SELECT place FROM stations WHERE id = ?", (station_id,)).fetchone()
        # A station someone already follows is simply shared: nothing to search.
        row, reading = (None, None) if known else self.find_new_station(station_id, place)
        label = known["place"] if known else row["place"]
        with self.edit_lock, self.connect() as db:
            mine = self.user_stations(user_id)
            if len(mine) >= most:
                raise StationError(f"{most} stations au maximum")
            if row:
                db.execute(
                    "INSERT OR IGNORE INTO stations (id, lat, lon, box, place) VALUES (?, ?, ?, ?, ?)",
                    (row["id"], row["lat"], row["lon"], row["box"], row["place"]),
                )
            if reading:
                self.modules[station_id] = reading["modules"]
                self.insert(db, station_id, reading)
            shown_name = name.strip()[:MAX_NAME] or label or station_id
            db.execute(
                "INSERT OR IGNORE INTO user_stations (user_id, station_id, name, slot, position) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, station_id, shown_name, free_slot(mine), len(mine)),
            )
        self.wake.set()  # its history is fetched by the collector right away
        return {"name": shown_name, "place": label}

    def rename_station(self, user_id, station_id, name):
        self.refuse_in_demo("Renommage")
        name = name.strip()
        if not name or len(name) > MAX_NAME:
            raise StationError(f"Le nom doit faire entre 1 et {MAX_NAME} caractères")
        with self.connect() as db:
            changed = db.execute(
                "UPDATE user_stations SET name = ? WHERE user_id = ? AND station_id = ?",
                (name, user_id, station_id),
            ).rowcount
        if not changed:
            raise StationError("Station introuvable")

    def reorder_stations(self, user_id, ordered_ids):
        """Save the display order; the colours stay with their stations."""
        self.refuse_in_demo("Déplacement")
        mine = [station["id"] for station in self.user_stations(user_id)]
        if sorted(ordered_ids) != sorted(mine):
            raise StationError("Liste de stations incorrecte")
        with self.connect() as db:
            for position, station_id in enumerate(ordered_ids):
                db.execute(
                    "UPDATE user_stations SET position = ? WHERE user_id = ? AND station_id = ?",
                    (position, user_id, station_id),
                )

    def remove_station(self, user_id, station_id):
        """Forget a station for this user; its stored history stays in the database."""
        self.refuse_in_demo("Suppression")
        with self.connect() as db:
            db.execute(
                "DELETE FROM user_stations WHERE user_id = ? AND station_id = ?",
                (user_id, station_id),
            )

    def collect_forever(self):
        while True:
            started = time.monotonic()
            self.poll()
            wait = max(MIN_WAIT, self.poll_seconds - (time.monotonic() - started))
            with self.lock:
                self.status["next_poll"] = int(time.time() + wait)
            self.wake.wait(wait)
            self.wake.clear()

    def series(self, db, station_id, since, until, bucket, daily, table="measures", names=COLUMNS):
        ranged = [name for name in RANGE_FIELDS if name in names]
        columns = [f"{AGGREGATES.get(name, 'AVG')}({name}) AS {name}" for name in names]
        for name in ranged:
            columns += [f"MIN({name}) AS {name}_min", f"MAX({name}) AS {name}_max"]
        params = [station_id, since, until]
        if daily:
            day = "date(ts, 'unixepoch', 'localtime')"
            stamp = f"CAST(strftime('%s', {day} || ' 12:00:00', 'utc') AS INTEGER)"
            group = day
        else:
            stamp = "MAX(ts)"
            group = "ts / ?"
            params.append(bucket)
        rows = db.execute(
            f"SELECT {stamp} AS stamp, {', '.join(columns)} FROM {table} "
            f"WHERE station_id = ? AND ts BETWEEN ? AND ? GROUP BY {group} ORDER BY stamp",
            params,
        ).fetchall()
        # A day's midday can fall outside a period that starts or ends during that day.
        out = {"ts": [min(until, max(since, row["stamp"])) for row in rows]}
        for name in (*names, *(f"{n}_{end}" for n in ranged for end in ("min", "max"))):
            out[name] = [None if row[name] is None else round(row[name], 2) for row in rows]
        return out

    def barometer(self, db, station_id, latest, now):
        if not latest:
            return None
        points = db.execute(
            "SELECT ts, pressure FROM measures WHERE station_id = ? AND ts >= ? "
            "AND pressure IS NOT NULL ORDER BY ts",
            (station_id, now - barometer.WINDOW),
        ).fetchall()
        return barometer.outlook([tuple(row) for row in points], latest["temperature"], latest["humidity"], now)

    def rain_totals(self, db, station_id, since, until):
        first = datetime.date.fromtimestamp(since).isoformat()
        last = datetime.date.fromtimestamp(until).isoformat()
        return [
            [row["day"], row["rain"]] for row in db.execute(
                "SELECT day, rain FROM rain_days WHERE station_id = ? AND day BETWEEN ? AND ? ORDER BY day",
                (station_id, first, last),
            )
        ]

    def user_status(self, mine):
        """The collection status, limited to what concerns this user's stations."""
        names = {station["id"]: station["name"] for station in mine}
        with self.lock:
            status = dict(self.status)
        for key in ("stations", "history", "wind", "forecast"):
            status[key] = {sid: message for sid, message in status[key].items() if sid in names}
        if not self.local:
            status.pop("notify_error", None)  # may name someone's address: the administrator sees it in the log
        status["history_running"] = names.get(status["history_running"])
        return status

    def stats_payload(self, user, hours, window=None, radius=0):
        """Figures of the period and records; they are computed on demand, never stored."""
        now = int(time.time())
        stations = []
        with self.connect() as db:
            for station in self.user_stations(user["id"]):
                latest = db.execute(
                    "SELECT * FROM measures WHERE station_id = ? ORDER BY ts DESC LIMIT 1",
                    (station["id"],),
                ).fetchone()
                oldest = db.execute(
                    "SELECT MIN(ts) FROM measures WHERE station_id = ?", (station["id"],)
                ).fetchone()[0]
                if window:
                    since, until = window[0], min(window[1], now)
                else:
                    since, until = (now - hours * 3600 if hours else oldest or now), now
                stations.append({
                    "id": station["id"],
                    "oldest": oldest,
                    **stats.for_station(db, station["id"], since, until),
                    "neighbours": self.neighbours_comparison(station, latest, radius, now),
                })
        return {"stations": stations}

    def payload(self, user, hours, window=None, daily=False, radius=0):
        """Return the data of the last `hours`, or of a (start, end) window if given.

        With `daily`, periods of a week or more are grouped by calendar day.
        """
        now = int(time.time())
        mine = self.user_stations(user["id"])
        with self.connect() as db:
            marks = ", ".join("?" for _ in mine)
            first = db.execute(
                f"SELECT MIN(ts) FROM measures WHERE station_id IN ({marks})",
                [station["id"] for station in mine],
            ).fetchone()[0] or now
            if window:
                since, until = window[0], min(window[1], now)
            else:
                since, until = (now - hours * 3600 if hours else first), now
            daily = daily and until - since >= DAILY_MIN_SPAN
            bucket = DAY if daily else max(1, math.ceil((until - since) / MAX_POINTS))
            stations = []
            for station in mine:
                latest = db.execute(
                    "SELECT * FROM measures WHERE station_id = ? ORDER BY ts DESC LIMIT 1",
                    (station["id"],),
                ).fetchone()
                low, high = db.execute(
                    "SELECT MIN(temperature), MAX(temperature) FROM measures "
                    "WHERE station_id = ? AND ts BETWEEN ? AND ?",
                    (station["id"], since, until),
                ).fetchone()
                wind_latest = db.execute(
                    "SELECT * FROM wind_estimates WHERE station_id = ? ORDER BY ts DESC LIMIT 1",
                    (station["id"],),
                ).fetchone()
                stations.append({
                    "id": station["id"],
                    "name": station["name"],
                    "slot": station["slot"],
                    "place": station.get("place"),
                    "url": None if self.demo else station_tools.weathermap_url(station["id"]),
                    "latest": dict(latest) if latest else None,
                    "temperature_min": low,
                    "temperature_max": high,
                    "series": self.series(db, station["id"], since, until, bucket, daily),
                    "rain_days": self.rain_totals(db, station["id"], since, until),
                    "wind_latest": dict(wind_latest) if wind_latest else None,
                    "forecast": self.forecasts.get(station["id"]),
                    "alerts": self.alerts.get(station["id"], []),
                    "neighbours": self.neighbours_summary(station, radius, now),
                    "barometer": self.barometer(db, station["id"], latest, now),
                    "wind_estimate": self.series(
                        db, station["id"], since, until, bucket, daily,
                        "wind_estimates", wind_estimate.FIELDS,
                    ),
                })
        return {
            "user": user["name"],
            "is_admin": self.is_admin(user),
            "invites_open": self.invites_open(),
            "neighbours_allowed": self.limits()["neighbours_allowed"],
            "announcement": self.announcement(now)[0],
            "mode": "local" if self.local else "online",
            "version": APP_VERSION,
            "demo": self.demo,
            "now": now,
            "first": first,
            "since": since,
            "until": until,
            "bucket": bucket,
            "daily": daily,
            "poll_seconds": self.poll_seconds,
            "setup": self.setup,
            "status": self.user_status(mine),
            "stations": stations,
        }


def read_window(query):
    """Return the (start, end) asked in the query string, or None."""
    try:
        start, end = int(query["from"][0]), int(query["to"][0])
    except (KeyError, ValueError):
        return None
    return (start, end) if start < end else None


def read_hours(query):
    try:
        return max(0, int(query.get("hours", ["24"])[0]))
    except ValueError:
        return 24


def read_radius(query):
    try:
        return min(neighbours.MAX_RADIUS_KM, max(0, int(query.get("radius", ["0"])[0])))
    except ValueError:
        return 0


def session_cookie(token, max_age, is_secure):
    parts = [f"{COOKIE}={token}", "Path=/", f"Max-Age={max_age}", "HttpOnly", "SameSite=Lax"]
    return "; ".join(parts + (["Secure"] if is_secure else []))


def page(name, local):
    """A file of web/; in local mode, the pages are marked so that the account parts stay hidden."""
    body = (WEB / name).read_bytes()
    if local and name.endswith(".html"):
        body = body.replace(b"<html", b'<html class="mode-local"', 1)
    return body


def is_password(header, password):
    """Check an HTTP Basic "Authorization" header; the user name does not matter."""
    kind, _, value = header.partition(" ")
    try:
        given = base64.b64decode(value, validate=True).decode().partition(":")[2]
    except ValueError:
        return False
    return kind.lower() == "basic" and secrets.compare_digest(given.encode(), password.encode())


def is_local_host(header):
    """True when the Host header names this computer (with or without a port)."""
    host = header.strip().lower()
    host = host[1:].partition("]")[0] if host.startswith("[") else host.partition(":")[0]
    return host in LOCAL_HOSTS


def is_online_only(path):
    return any(path == item or (item.endswith("/") and path.startswith(item)) for item in ONLINE_ONLY)


def make_handler(app, invite_code="", secure_cookie=True, password="", port=8765):
    """invite_code and secure_cookie serve the online mode; password and port the local mode."""
    app.first_code = invite_code
    redirect_uri = f"http://localhost:{port}/callback"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def authorized(self):
            """Local mode: the optional password, else only this computer's own names."""
            # Without a password the dashboard only answers to this computer's own names, so a
            # website that points its address at 127.0.0.1 ("DNS rebinding") cannot use it.
            if not password and not is_local_host(self.headers.get("Host", "")):
                self.reply(403, b"Adresse refusee", "text/plain; charset=utf-8")
                return False
            if not password or is_password(self.headers.get("Authorization", ""), password):
                return True
            if self.headers.get("Authorization"):
                time.sleep(AUTH_DELAY)
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Meteo perso", charset="UTF-8"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        def refused_here(self):
            """True when the local mode has already answered: wrong password or address, or an account page."""
            if not app.local:
                return False
            if not self.authorized():
                return True
            path = urllib.parse.urlsplit(self.path).path
            if path == "/login":
                self.redirect("/")
            elif is_online_only(path):
                self.reply(404, b"Introuvable", "text/plain; charset=utf-8")
            else:
                return False
            return True

        def connect_netatmo(self):
            """Local mode: send the browser to Netatmo, which comes back on /callback."""
            app.oauth_state = secrets.token_urlsafe(24)
            self.redirect(app.tokens.authorize_url(redirect_uri, app.oauth_state))

        def callback(self, query):
            state = query.get("state", [""])[0]
            code = query.get("code", [""])[0]
            error = None
            if not app.oauth_state or not secrets.compare_digest(state, app.oauth_state):
                error = "Réponse Netatmo refusée (state incorrect)"
            elif not code:
                error = "Connexion Netatmo annulée"
            else:
                try:
                    app.tokens.exchange_code(code, redirect_uri)
                except Exception as err:
                    error = f"Connexion Netatmo impossible : {err}"
            app.oauth_state = None
            if error:
                with app.lock:
                    app.status["error"] = error[:200]
            else:
                app.wake.set()
            self.redirect("/")

        def send_common_headers(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("X-Robots-Tag", "noindex, nofollow")  # not listed by search engines

        def reply(self, code, body, content_type, cookie=None):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_common_headers()
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(body)

        def reply_json(self, code, payload, cookie=None):
            self.reply(code, json.dumps(payload).encode(), "application/json", cookie)

        def redirect(self, location):
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.send_common_headers()
            self.end_headers()

        def session_token(self):
            cookies = http.cookies.SimpleCookie()
            try:
                cookies.load(self.headers.get("Cookie", ""))
            except http.cookies.CookieError:
                return ""
            return cookies[COOKIE].value if COOKIE in cookies else ""

        def current_user(self):
            if app.local:
                return LOCAL_USER  # the password, if any, was checked by refused_here
            token = self.session_token()
            return app.accounts.session_user(token) if token else None

        def refuse_guest(self):
            self.reply_json(401, {"error": "Connexion nécessaire"})

        def current_admin(self):
            """Return the signed-in administrator, or None after refusing the request."""
            user = self.current_user()
            if not user:
                self.refuse_guest()
            elif not app.is_admin(user):
                self.reply_json(403, {"error": "Réservé à l'administrateur"})
            else:
                return user
            return None

        def read_form(self):
            """Return the JSON body, or None after refusing the request."""
            # Requiring JSON stops other websites from posting here unnoticed.
            is_json = self.headers.get_content_type() == "application/json"
            length = int(self.headers.get("Content-Length") or 0)
            if is_json and 0 < length <= MAX_BODY:
                try:
                    form = json.loads(self.rfile.read(length))
                    if isinstance(form, dict):
                        return form
                except ValueError:
                    pass
            self.reply_json(400, {"error": "Requête refusée"})
            return None

        def do_GET(self):
            if self.refused_here():
                return
            url = urllib.parse.urlsplit(self.path)
            if app.local and url.path in ("/connecter", "/callback"):
                if url.path == "/callback":
                    self.callback(urllib.parse.parse_qs(url.query))
                elif app.setup == "auth":
                    self.connect_netatmo()
                else:
                    self.redirect("/")
                return
            # Déjà connecté : la page de connexion renvoie dans l'appli (sauf pour une invitation)
            if url.path == "/login" and "code" not in urllib.parse.parse_qs(url.query) and self.current_user():
                self.redirect("/")
                return
            if url.path in PUBLIC_FILES:
                name, content_type = PUBLIC_FILES[url.path]
                self.reply(200, page(name, app.local), content_type)
                return
            if url.path == "/entrer":
                self.enter_from_portal(urllib.parse.parse_qs(url.query).get("c", [""])[0])
                return
            user = self.current_user()
            if url.path == "/settings.js":
                settings = app.user_settings(user["id"]) if user else None
                self.reply(200, (SETTINGS_SCRIPT % json.dumps(settings)).encode(), JAVASCRIPT)
            elif url.path in ADMIN_FILES:
                if not user:
                    self.redirect("/login")
                elif not app.is_admin(user):
                    self.redirect("/")
                else:
                    name, content_type = ADMIN_FILES[url.path]
                    self.reply(200, page(name, app.local), content_type)
            elif url.path in PRIVATE_FILES:
                if not user:
                    self.redirect("/login")
                    return
                name, content_type = PRIVATE_FILES[url.path]
                self.reply(200, page(name, app.local), content_type)
            elif url.path == "/api/data":
                if not user:
                    self.refuse_guest()
                    return
                query = urllib.parse.parse_qs(url.query)
                daily = query.get("daily", ["0"])[0] == "1"
                body = json.dumps(
                    app.payload(user, read_hours(query), read_window(query), daily, read_radius(query))
                ).encode()
                self.reply(200, body, "application/json")
            elif url.path == "/api/stats":
                if not user:
                    self.refuse_guest()
                    return
                query = urllib.parse.parse_qs(url.query)
                body = json.dumps(
                    app.stats_payload(user, read_hours(query), read_window(query), read_radius(query))
                ).encode()
                self.reply(200, body, "application/json")
            elif url.path == "/api/admin/overview":
                if self.current_admin():
                    self.reply_json(200, app.overview())
            elif url.path == "/api/admin/backup":
                self.send_backup()
            elif url.path == "/api/admin/users":
                admin = self.current_admin()
                if admin:
                    self.reply_json(200, {
                        "users": app.admin_users(),
                        "invites_open": app.invites_open(),
                        "me": admin["id"],
                        "owner": app.accounts.admin_id(),
                        "now": int(time.time()),
                    })
            elif url.path == "/api/notifications":
                if user:
                    self.reply_json(200, app.notifications_page(user["id"], app.local))
                else:
                    self.refuse_guest()
            elif url.path == "/api/push/pending":
                if user:
                    self.reply_json(200, {"messages": app.pending_messages(user["id"])})
                else:
                    self.refuse_guest()
            elif url.path == "/api/invite":
                if not user:
                    self.refuse_guest()
                    return
                may_invite = app.invites_open() or app.is_admin(user)
                self.reply_json(200, {"code": app.invite_code(user["id"]) if may_invite else None})
            else:
                self.reply(404, b"Introuvable", "text/plain; charset=utf-8")

        def send_backup(self):
            if not self.current_admin():
                return
            name = f"meteo-{datetime.date.today().isoformat()}.db"
            with tempfile.TemporaryDirectory(dir=DATA) as folder:
                path = Path(folder) / name
                app.backup_to(path)
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.sqlite3")
                self.send_header("Content-Length", str(path.stat().st_size))
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.send_common_headers()
                self.end_headers()
                with path.open("rb") as file:
                    shutil.copyfileobj(file, self.wfile)

        def may_manage(self, admin, user_id):
            """Only the owner manages administrators, and nobody manages the owner."""
            if user_id == app.accounts.admin_id():
                return False
            return app.is_owner(admin) or not app.accounts.is_admin(user_id)

        def start_session(self, user_id):
            token = app.accounts.create_session(user_id)
            cookie = session_cookie(token, account_tools.SESSION_SECONDS, secure_cookie)
            self.reply_json(200, {}, cookie)

        def login(self, form):
            try:
                user_id = app.accounts.login(str(form.get("name", "")), str(form.get("password", "")))
            except account_tools.AccountError as err:
                time.sleep(AUTH_DELAY)
                self.reply_json(401, {"error": str(err)})
            else:
                self.start_session(user_id)

        def create_account(self, form):
            try:
                user_id = app.register_with_code(
                    str(form.get("code", "")), str(form.get("name", "")), str(form.get("password", "")),
                )
            except InviteError as err:
                time.sleep(AUTH_DELAY)
                self.reply_json(403, {"error": str(err)})
            except account_tools.AccountError as err:
                self.reply_json(422, {"error": str(err)})
            else:
                self.start_session(user_id)

        def enter_from_portal(self, code):
            user_id = take_portal_code(code)
            if not user_id:
                # Code déjà utilisé : sur téléphone, l'appli installée rouvre le même lien juste après le navigateur.
                # La session est alors déjà ouverte : on entre dans l'appli au lieu d'afficher la connexion.
                self.redirect("/" if self.current_user() else "/login")
                return
            token = app.accounts.create_session(user_id)
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.send_common_headers()
            self.send_header("Set-Cookie", session_cookie(token, account_tools.SESSION_SECONDS, secure_cookie))
            self.end_headers()

        def portal_check(self):
            """Internal call from the portal: never from the internet (no Cloudflare header), key required."""
            key, given = portal_key(), self.headers.get("X-Portail-Cle") or ""
            if not key or self.headers.get("CF-Connecting-IP") or not secrets.compare_digest(given.encode(), key.encode()):
                self.reply(404, b"Introuvable", "text/plain; charset=utf-8")
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                form = json.loads(self.rfile.read(length)) if 0 < length <= MAX_BODY else {}
            except ValueError:
                form = {}
            name, password = str(form.get("nom", "")).strip(), str(form.get("mdp", ""))
            with app.accounts.connect() as db:
                row = db.execute("SELECT id FROM users WHERE name = ?", (name,)).fetchone()
            if not row:  # no account here: not a failed try
                self.reply_json(200, {"ok": 0})
                return
            try:
                user_id = app.accounts.login(name, password)
            except account_tools.AccountError:
                self.reply_json(200, {"ok": 0})
                return
            self.reply_json(200, {"ok": 1, "code": new_portal_code(user_id)})

        def do_POST(self):
            if self.refused_here():
                return
            if self.path == "/api/portail/verifier":
                self.portal_check()
                return
            form = self.read_form()
            if form is None:
                return
            if self.path == "/api/login":
                self.login(form)
            elif self.path == "/api/register":
                self.create_account(form)
            elif self.path == "/api/logout":
                token = self.session_token()
                if token:
                    app.accounts.end_session(token)
                self.reply_json(200, {}, session_cookie("", 0, secure_cookie))
            elif self.path == "/api/stations":
                self.add_station(form)
            elif self.path == "/api/password":
                self.change_password(form)
            elif self.path == "/api/notifications/test":
                self.notification_test()
            elif self.path in ("/api/push/subscribe", "/api/push/unsubscribe"):
                self.push_subscription(form)
            elif self.path in ("/api/mailbox", "/api/mailbox/remove", "/api/mailbox/test"):
                self.mailbox(form)
            elif self.path == "/api/invite":
                self.renew_own_code()
            elif self.path == "/api/admin/code":
                self.renew_user_code(form)
            elif self.path == "/api/admin/invites":
                if self.current_admin():
                    app.set_invites_open(bool(form.get("open")))
                    self.reply_json(200, {})
            elif self.path == "/api/admin/limits":
                if self.current_admin():
                    try:
                        app.save_limits(form)
                    except ValueError as err:
                        self.reply_json(422, {"error": str(err)})
                    else:
                        self.reply_json(200, {"limits": app.limits()})
            elif self.path == "/api/admin/announcement":
                if self.current_admin():
                    text = str(form.get("text", "")).strip()
                    hours = form.get("hours", 0)
                    hours = hours if type(hours) is int else None  # neither 1.5 nor true
                    if len(text) > MAX_ANNOUNCEMENT:
                        self.reply_json(422, {"error": f"{MAX_ANNOUNCEMENT} caractères au plus"})
                    elif hours not in ANNOUNCEMENT_HOURS:
                        self.reply_json(422, {"error": "Durée inconnue"})
                    else:
                        app.set_announcement(text, hours)
                        self.reply_json(200, {})
            elif self.path == "/api/admin/role":
                self.set_role(form)
            elif self.path == "/api/admin/reset":
                self.reset_user_password(form)
            else:
                self.reply_json(404, {"error": "Introuvable"})

        def renew_own_code(self):
            user = self.current_user()
            if not user:
                self.refuse_guest()
            elif not (app.invites_open() or app.is_admin(user)):
                self.reply_json(403, {"error": "Les invitations sont fermées pour le moment"})
            else:
                self.reply_json(200, {"code": app.renew_code(user["id"])})

        def renew_user_code(self, form):
            if not self.current_admin():
                return
            try:
                user_id = int(form.get("id", 0))
            except (ValueError, TypeError):
                self.reply_json(400, {"error": "Requête refusée"})
                return
            if not any(user["id"] == user_id for user in app.accounts.list_users()):
                self.reply_json(422, {"error": "Utilisateur inconnu"})
                return
            self.reply_json(200, {"code": app.renew_code(user_id)})

        def site_origin(self):
            """The address the browser used, given to the push services as the contact of the site."""
            host = self.headers.get("Host", "")
            if app.local or not re.fullmatch(r"[A-Za-z0-9.-]{1,100}(:\d{1,5})?", host):
                return "mailto:meteo@example.invalid"
            return f"https://{host}"

        def push_subscription(self, form):
            user = self.current_user()
            if not user:
                self.refuse_guest()
                return
            endpoint = str(form.get("endpoint", ""))
            try:
                if self.path == "/api/push/subscribe":
                    app.subscribe(user["id"], endpoint, self.site_origin())
                else:
                    app.unsubscribe(user["id"], endpoint)
            except notify.NotifyError as err:
                self.reply_json(422, {"error": str(err)})
            else:
                self.reply_json(200, {"devices": len(app.devices(user["id"]))})

        def mailbox(self, form):
            """The Gmail box that sends the emails: the local user or an administrator sets it."""
            user = self.current_user()
            if not user:
                self.refuse_guest()
                return
            if not (app.local or app.is_admin(user)):
                self.reply_json(403, {"error": "Réservé à l'administrateur"})
                return
            try:
                if self.path == "/api/mailbox":
                    app.save_mailbox(str(form.get("user", "")), str(form.get("password", "")))
                elif self.path == "/api/mailbox/test":
                    app.test_mailbox(str(form.get("address", "")))
                else:
                    app.remove_mailbox()
            except notify.NotifyError as err:
                time.sleep(AUTH_DELAY)
                self.reply_json(422, {"error": str(err)})
            else:
                self.reply_json(200, {"address": app.smtp["user"] if app.smtp else None})

        def notification_test(self):
            user = self.current_user()
            if not user:
                self.refuse_guest()
                return
            try:
                results = app.send_test(user["id"])
            except notify.NotifyError as err:
                self.reply_json(422, {"error": str(err)})
            else:
                self.reply_json(200, {"results": results})

        def change_password(self, form):
            user = self.current_user()
            if not user:
                self.refuse_guest()
                return
            try:
                app.accounts.change_password(
                    user, str(form.get("current", "")), str(form.get("new", "")), self.session_token(),
                )
            except account_tools.AccountError as err:
                time.sleep(AUTH_DELAY)
                self.reply_json(422, {"error": str(err)})
            else:
                self.reply_json(200, {})

        def set_role(self, form):
            admin = self.current_admin()
            if not admin:
                return
            if not app.is_owner(admin):
                self.reply_json(403, {"error": "Seul le propriétaire du site nomme les administrateurs"})
                return
            try:
                app.accounts.set_admin(int(form.get("id", 0)), bool(form.get("admin")))
            except (ValueError, TypeError):
                self.reply_json(400, {"error": "Requête refusée"})
            except account_tools.AccountError as err:
                self.reply_json(422, {"error": str(err)})
            else:
                self.reply_json(200, {})

        def reset_user_password(self, form):
            admin = self.current_admin()
            if not admin:
                return
            try:
                user_id = int(form.get("id", 0))
                if user_id == admin["id"]:
                    raise account_tools.AccountError("Changez votre propre mot de passe dans « Mon mot de passe »")
                if not self.may_manage(admin, user_id):
                    raise account_tools.AccountError("Seul le propriétaire du site peut gérer un administrateur")
                self.reply_json(200, {"password": app.accounts.temporary_password(user_id)})
            except (ValueError, TypeError):
                self.reply_json(400, {"error": "Requête refusée"})
            except account_tools.AccountError as err:
                self.reply_json(422, {"error": str(err)})

        def add_station(self, form):
            user = self.current_user()
            if not user:
                self.refuse_guest()
                return
            try:
                station = app.add_station(
                    user["id"], str(form.get("link", "")), str(form.get("name", "")), str(form.get("place", "")),
                )
            except StationError as err:
                self.reply_json(422, {"error": str(err)})
            else:
                self.reply_json(201, station)

        def do_PATCH(self):
            if self.refused_here():
                return
            form = self.read_form()
            if form is None:
                return
            user = self.current_user()
            if not user:
                self.refuse_guest()
            elif self.path == "/api/settings":
                try:
                    app.save_settings(user["id"], form.get("settings"))
                except ValueError as err:
                    self.reply_json(422, {"error": str(err)})
                else:
                    self.reply_json(200, {})
            elif self.path == "/api/notifications":
                try:
                    settings = app.save_notifications(user["id"], form)
                except notify.NotifyError as err:
                    self.reply_json(422, {"error": str(err)})
                else:
                    self.reply_json(200, settings)
            elif self.path == "/api/stations/order":
                try:
                    app.reorder_stations(user["id"], [str(item) for item in form.get("ids", [])])
                except (StationError, TypeError) as err:
                    self.reply_json(422, {"error": str(err)})
                else:
                    self.reply_json(200, {})
            elif self.path != "/api/stations":
                self.reply_json(404, {"error": "Introuvable"})
            else:
                try:
                    app.rename_station(user["id"], str(form.get("id", "")), str(form.get("name", "")))
                except StationError as err:
                    self.reply_json(422, {"error": str(err)})
                else:
                    self.reply_json(200, {})

        def delete_user(self, target):
            admin = self.current_admin()
            if not admin:
                return
            if not target.isdigit() or int(target) == admin["id"] or not self.may_manage(admin, int(target)):
                self.reply_json(422, {"error": "Ce compte ne peut pas être supprimé"})
                return
            app.delete_user(int(target))
            self.reply_json(200, {})

        def do_DELETE(self):
            if self.refused_here():
                return
            url = urllib.parse.urlsplit(self.path)
            station_id = urllib.parse.parse_qs(url.query).get("id", [""])[0]
            if url.path == "/api/admin/users":
                self.delete_user(station_id)
                return
            if url.path == "/api/admin/stations":
                if self.current_admin():
                    try:
                        app.forget_station(station_id)
                    except StationError as err:
                        self.reply_json(422, {"error": str(err)})
                    else:
                        self.reply_json(200, {})
                return
            user = self.current_user()
            if not user:
                self.refuse_guest()
            elif url.path != "/api/stations" or not station_id:
                self.reply_json(400, {"error": "Requête refusée"})
            else:
                app.remove_station(user["id"], station_id)
                self.reply_json(200, {})

    return Handler


def load_config():
    if not CONFIG.exists():
        shutil.copy(ROOT / "config.example.json", CONFIG)
    return json.loads(CONFIG.read_text(encoding="utf-8-sig"))  # Notepad may add a BOM


def main():
    invite_code = os.environ.get("METEO_INVITE_CODE", "")
    parser = argparse.ArgumentParser(description="Dashboard météo Netatmo")
    parser.add_argument(
        "--mode", choices=("local", "en-ligne"), default="en-ligne" if invite_code else "local",
        help="local : une personne, sans compte (par défaut) ; en-ligne : comptes et invitations "
             "(par défaut quand METEO_INVITE_CODE existe)",
    )
    parser.add_argument("--demo", action="store_true", help="données simulées, sans compte Netatmo (mode local)")
    parser.add_argument("--host", default="127.0.0.1", help="adresse d'écoute (0.0.0.0 dans un conteneur)")
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-browser", action="store_true", help="mode local : ne pas ouvrir le navigateur")
    args = parser.parse_args()
    local = args.mode == "local" or args.demo

    DATA.mkdir(exist_ok=True)
    config = load_config()
    if local:
        password = os.environ.get("METEO_PASSWORD", "")
        if args.host not in LOCAL_HOSTS and not password:
            parser.error("METEO_PASSWORD est obligatoire quand le dashboard est accessible depuis le réseau")
        port = args.port or int(config.get("port", 8765))
        app = Dashboard(config, local=True, demo=args.demo)
        handler = make_handler(app, password=password, port=port)
    else:
        if len(invite_code) < 8:
            parser.error("METEO_INVITE_CODE est obligatoire et doit faire au moins 8 caractères")
        secure_cookie = os.environ.get("METEO_INSECURE_COOKIE") != "1"  # only for tests without HTTPS
        port = args.port or 8765
        app = Dashboard(config)
        handler = make_handler(app, invite_code, secure_cookie)
    threading.Thread(target=app.collect_forever, daemon=True).start()
    server = ThreadingHTTPServer((args.host, port), handler)
    if local:
        address = f"http://localhost:{port}/"
        print(f"Dashboard : {address}  (Ctrl+C pour quitter)", flush=True)
        if not args.no_browser:
            webbrowser.open(address)
    else:
        print(f"Dashboard en ligne sur le port {port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
