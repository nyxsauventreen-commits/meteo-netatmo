"""Local weather dashboard: collects Netatmo readings and serves the charts."""
import argparse
import base64
import bisect
import contextlib
import datetime
import json
import math
import os
import secrets
import shutil
import sqlite3
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import alerts
import barometer
import forecast
import neighbours
import netatmo
import stations as station_tools
import stats
import wind_estimate

ROOT = Path(__file__).resolve().parent
# In a container, METEO_DATA holds everything that changes, config.json included.
DATA = Path(os.environ.get("METEO_DATA") or ROOT / "data")
WEB = ROOT / "web"
CONFIG = DATA / "config.json" if os.environ.get("METEO_DATA") else ROOT / "config.json"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
AUTH_DELAY = 1  # seconds lost on a wrong password, to slow down guessing
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/guide": ("guide.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
MAX_POINTS = 1500
MAX_STATIONS = 8  # one distinct colour per station
MAX_BODY = 4096
STATION_GAP = 20  # seconds between the live readings of two stations
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
DEMO_DAYS = 800
DEMO_DETAILED_DAYS = 14
DEMO_STATIONS = [
    {"id": "demo:1", "name": "Valence", "slot": 0},
    {"id": "demo:2", "name": "Boulot", "slot": 1},
]


class StationError(Exception):
    """A station cannot be added; the message is shown to the user."""


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


def load_stations(config):
    """Keep complete entries and give each one a colour that never changes."""
    loaded = [dict(station) for station in config.get("stations", []) if is_configured(station)]
    loaded = loaded[:MAX_STATIONS]
    for station in loaded:
        if station.get("slot") not in range(MAX_STATIONS):
            station.pop("slot", None)
            station["slot"] = free_slot(loaded)
    return loaded


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
    def __init__(self, config, demo):
        self.demo = demo
        self.config = config
        self.poll_seconds = max(5, int(config.get("poll_minutes", 10))) * 60
        self.history_days = max(1, int(config.get("history_days", DEFAULT_HISTORY_DAYS)))
        self.history_errors = {}
        self.history_wait = {}
        self.wind_errors = {}
        self.wind_wait = {}
        self.modules = {}
        self.stations = DEMO_STATIONS if demo else load_stations(config)
        self.forecasts = {station["id"]: demo_forecast(i) for i, station in enumerate(self.stations)} if demo else {}
        self.forecast_errors = {}
        self.forecast_wait = {}
        self.alerts = {station["id"]: demo_alerts(i) for i, station in enumerate(self.stations)} if demo else {}
        self.alert_wait = 0
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
        self.oauth_state = None
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.status = {
            "last_poll": None, "next_poll": None, "error": None, "stations": {},
            "history": {}, "history_running": None, "history_rows": 0, "wind": {}, "forecast": {},
            "alerts_error": None,
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
        if demo:
            self.seed_demo()

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
        with self.connect() as db:
            if db.execute("SELECT 1 FROM measures LIMIT 1").fetchone():
                return
            start = int(time.time()) // 3600 * 3600
            self.store_wind(db, self.stations[0]["id"], {
                ts: {name: demo_reading(1, ts)[name] for name in wind_estimate.FIELDS}
                for ts in range(start - DEMO_DAYS * DAY, start + 1, 3600)
            })
            now = int(time.time()) // 600 * 600
            detailed = now - DEMO_DETAILED_DAYS * DAY
            gap = range(now - 3 * DAY, now - 3 * DAY + 5 * 3600)
            stamps = [*range(now - DEMO_DAYS * DAY, detailed, netatmo.HISTORY_STEP), *range(detailed, now + 1, 600)]
            for index, station in enumerate(self.stations):
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
            self.status["history_running"] = station["name"]
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
        exact = {"lat": lat, "lon": lon, "box": netatmo.EXACT_BOX_DEG}
        with self.edit_lock:
            self.save_stations([
                {**item, **exact} if item["id"] == station["id"] else item
                for item in self.stations
            ])

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

    def neighbours_summary(self, station, index, radius, now):
        """Return the medians of a station, or None when the option is off."""
        if not radius:
            return None
        if self.demo:
            return demo_neighbours(index, radius)
        station_id = station["id"]
        is_new = station_id not in self.neighbours_wanted
        self.neighbours_wanted[station_id] = now
        readings = self.neighbours.get(station_id)
        if readings is None:
            if is_new:
                self.wake.set()  # no need to wait for the next poll
            return {"radius": radius, "pending": True}
        return neighbours.summarise(readings, station, radius, now)

    def neighbours_comparison(self, station, index, latest, radius, now):
        if not radius or not latest:
            return None
        if self.demo:
            return demo_comparison(index, radius)
        readings = self.neighbours.get(station["id"])
        return None if readings is None else neighbours.compare(readings, station, latest, radius, now)

    def publish(self, errors, general):
        with self.lock:
            self.status.update(last_poll=int(time.time()), error=general, stations=errors)

    def poll(self):
        errors = {}
        general = None
        if not self.demo:
            self.collect_forecast()  # first: it is quick, and the sky shows right after a restart
            self.collect_alerts()
        if self.demo:
            ts = int(time.time()) // 600 * 600
            with self.connect() as db:
                for index, station in enumerate(self.stations):
                    self.insert(db, station["id"], demo_reading(index, ts))
        elif self.setup == "ok":
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
        if not self.demo and self.setup == "ok" and general is None:
            try:
                self.collect_neighbours()
                self.collect_history()
            except netatmo.AuthRequired as err:
                self.publish(errors, str(err))
            self.collect_wind()
        with self.lock:
            self.status["history"] = dict(self.history_errors)
            self.status["wind"] = dict(self.wind_errors)

    def save_stations(self, updated):
        # Re-read the file so edits made while the app runs are not overwritten.
        config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
        config["stations"] = updated
        tmp = CONFIG.with_suffix(".tmp")
        tmp.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, CONFIG)
        self.stations = updated

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

    def add_station(self, link, name, place):
        if self.demo:
            raise StationError("Ajout indisponible en mode démonstration")
        station_id = station_tools.parse_station_id(link)
        if not station_id:
            raise StationError("Lien non reconnu : il doit venir de la carte Netatmo et contenir stationid=…")
        with self.edit_lock:
            if any(station["id"] == station_id for station in self.stations):
                raise StationError("Cette station est déjà dans le dashboard")
            if len(self.stations) >= MAX_STATIONS:
                raise StationError(f"{MAX_STATIONS} stations au maximum")
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
            station = {
                "id": station_id,
                "name": name.strip()[:40] or chosen["label"],
                "lat": chosen["lat"],
                "lon": chosen["lon"],
                "place": chosen["label"],
                "slot": free_slot(self.stations),
            }
            location = reading.get("location") if reading else None
            if location:
                station.update(lon=location[0], lat=location[1], box=netatmo.EXACT_BOX_DEG)
            self.save_stations([*self.stations, station])
            if reading:
                self.modules[station_id] = reading["modules"]
                with self.connect() as db:
                    self.insert(db, station_id, reading)
                self.wake.set()  # its history is fetched by the collector right away
            return station

    def rename_station(self, station_id, name):
        name = name.strip()
        if self.demo:
            raise StationError("Renommage indisponible en mode démonstration")
        if not name or len(name) > MAX_NAME:
            raise StationError(f"Le nom doit faire entre 1 et {MAX_NAME} caractères")
        with self.edit_lock:
            if not any(station["id"] == station_id for station in self.stations):
                raise StationError("Station introuvable")
            self.save_stations([
                {**station, "name": name} if station["id"] == station_id else station
                for station in self.stations
            ])

    def reorder_stations(self, ordered_ids):
        """Save the display order; the colours stay with their stations."""
        if self.demo:
            raise StationError("Déplacement indisponible en mode démonstration")
        with self.edit_lock:
            by_id = {station["id"]: station for station in self.stations}
            if sorted(ordered_ids) != sorted(by_id):
                raise StationError("Liste de stations incorrecte")
            self.save_stations([by_id[station_id] for station_id in ordered_ids])

    def remove_station(self, station_id):
        """Forget a station; its stored history stays in the database."""
        if self.demo:
            raise StationError("Suppression indisponible en mode démonstration")
        with self.edit_lock:
            self.save_stations([s for s in self.stations if s["id"] != station_id])

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

    def time_span(self, db, hours, window, now):
        """Return the first stored measure, and the start and end of the period asked for."""
        first = db.execute("SELECT MIN(ts) FROM measures").fetchone()[0] or now
        if window:
            return first, window[0], min(window[1], now)
        return first, now - hours * 3600 if hours else first, now

    def stats_payload(self, hours, window=None, radius=0):
        """Figures of the period and records; they are computed on demand, never stored."""
        now = int(time.time())
        with self.connect() as db:
            _, since, until = self.time_span(db, hours, window, now)
            stations = []
            for index, station in enumerate(self.stations):
                latest = db.execute(
                    "SELECT * FROM measures WHERE station_id = ? ORDER BY ts DESC LIMIT 1",
                    (station["id"],),
                ).fetchone()
                oldest = db.execute(
                    "SELECT MIN(ts) FROM measures WHERE station_id = ?", (station["id"],)
                ).fetchone()[0]
                stations.append({
                    "id": station["id"],
                    "oldest": oldest,
                    **stats.for_station(db, station["id"], since, until),
                    "neighbours": self.neighbours_comparison(station, index, latest, radius, now),
                })
        return {"since": since, "until": until, "stations": stations}

    def payload(self, hours, window=None, daily=False, radius=0):
        """Return the data of the last `hours`, or of a (start, end) window if given.

        With `daily`, periods of a week or more are grouped by calendar day.
        """
        now = int(time.time())
        with self.connect() as db:
            first, since, until = self.time_span(db, hours, window, now)
            daily = daily and until - since >= DAILY_MIN_SPAN
            bucket = DAY if daily else max(1, math.ceil((until - since) / MAX_POINTS))
            stations = []
            for index, station in enumerate(self.stations):
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
                    "neighbours": self.neighbours_summary(station, index, radius, now),
                    "barometer": self.barometer(db, station["id"], latest, now),
                    "wind_estimate": self.series(
                        db, station["id"], since, until, bucket, daily,
                        "wind_estimates", wind_estimate.FIELDS,
                    ),
                })
        with self.lock:
            status = dict(self.status)
        return {
            "demo": self.demo,
            "now": now,
            "first": first,
            "since": since,
            "until": until,
            "bucket": bucket,
            "daily": daily,
            "poll_seconds": self.poll_seconds,
            "setup": self.setup,
            "status": status,
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


def protected(method):
    def wrapper(handler):
        if handler.authorized():
            method(handler)
    return wrapper


def make_handler(app, port, password=""):
    redirect_uri = f"http://localhost:{port}/callback"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def authorized(self):
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

        def reply(self, code, body, content_type):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def redirect(self, location):
            self.send_response(302)
            self.send_header("Location", location)
            self.end_headers()

        @protected
        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(url.query)
            if url.path in STATIC:
                name, content_type = STATIC[url.path]
                self.reply(200, (WEB / name).read_bytes(), content_type)
            elif url.path == "/api/data":
                daily = query.get("daily", ["0"])[0] == "1"
                body = json.dumps(app.payload(read_hours(query), read_window(query), daily, read_radius(query))).encode()
                self.reply(200, body, "application/json")
            elif url.path == "/api/stats":
                body = json.dumps(app.stats_payload(read_hours(query), read_window(query), read_radius(query))).encode()
                self.reply(200, body, "application/json")
            elif url.path == "/connecter" and app.setup == "auth":
                app.oauth_state = secrets.token_urlsafe(24)
                self.redirect(app.tokens.authorize_url(redirect_uri, app.oauth_state))
            elif url.path == "/callback":
                self.callback(query)
            else:
                self.reply(404, b"Introuvable", "text/plain; charset=utf-8")

        def reply_json(self, code, payload):
            self.reply(code, json.dumps(payload).encode(), "application/json")

        def read_form(self):
            """Return the JSON body, or None after refusing the request."""
            # Requiring JSON stops other websites from posting here unnoticed.
            is_json = self.headers.get_content_type() == "application/json"
            length = int(self.headers.get("Content-Length") or 0)
            is_known = self.path in ("/api/stations", "/api/stations/order")
            if is_known and is_json and 0 < length <= MAX_BODY:
                try:
                    form = json.loads(self.rfile.read(length))
                    if isinstance(form, dict):
                        return form
                except ValueError:
                    pass
            self.reply_json(400, {"error": "Requête refusée"})
            return None

        @protected
        def do_POST(self):
            form = self.read_form()
            if form is None:
                return
            try:
                station = app.add_station(
                    str(form.get("link", "")), str(form.get("name", "")), str(form.get("place", "")),
                )
            except StationError as err:
                self.reply_json(422, {"error": str(err)})
            else:
                self.reply_json(201, {"name": station["name"], "place": station["place"]})

        @protected
        def do_PATCH(self):
            form = self.read_form()
            if form is None:
                return
            try:
                if self.path == "/api/stations/order":
                    app.reorder_stations([str(item) for item in form.get("ids", [])])
                else:
                    app.rename_station(str(form.get("id", "")), str(form.get("name", "")))
            except (StationError, TypeError) as err:
                self.reply_json(422, {"error": str(err)})
            else:
                self.reply_json(200, {})

        @protected
        def do_DELETE(self):
            url = urllib.parse.urlsplit(self.path)
            station_id = urllib.parse.parse_qs(url.query).get("id", [""])[0]
            if url.path != "/api/stations" or not station_id:
                self.reply_json(400, {"error": "Requête refusée"})
                return
            try:
                app.remove_station(station_id)
            except StationError as err:
                self.reply_json(422, {"error": str(err)})
            else:
                self.reply_json(200, {})

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

    return Handler


def load_config():
    if not CONFIG.exists():
        shutil.copy(ROOT / "config.example.json", CONFIG)
    return json.loads(CONFIG.read_text(encoding="utf-8-sig"))  # Notepad may add a BOM


def main():
    parser = argparse.ArgumentParser(description="Dashboard météo local")
    parser.add_argument("--demo", action="store_true", help="données simulées, sans compte Netatmo")
    parser.add_argument("--host", default="127.0.0.1", help="adresse d'écoute (0.0.0.0 dans un conteneur)")
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    password = os.environ.get("METEO_PASSWORD", "")
    if args.host not in LOCAL_HOSTS and not password:
        parser.error("METEO_PASSWORD est obligatoire quand le dashboard est accessible depuis le réseau")

    DATA.mkdir(exist_ok=True)
    config = load_config()
    port = args.port or int(config.get("port", 8765))
    app = Dashboard(config, args.demo)
    threading.Thread(target=app.collect_forever, daemon=True).start()
    server = ThreadingHTTPServer((args.host, port), make_handler(app, port, password))
    address = f"http://localhost:{port}/"
    print(f"Dashboard : {address}  (Ctrl+C pour quitter)", flush=True)
    if not args.no_browser:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
