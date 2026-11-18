"""Client for the official Netatmo API (OAuth2 + /api/getpublicdata)."""
import collections
import datetime
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.netatmo.com"
SCOPE = "read_station"
BOX_DEG = 0.05  # half-size of the area requested around a station (about 5 km)
EXACT_BOX_DEG = 0.01  # once the exact position is known; dense areas otherwise time out

FIELDS = (
    "temperature", "humidity", "pressure", "rain_60min", "rain_24h",
    "wind_strength", "wind_angle", "gust_strength", "gust_angle",
)
HISTORY_STEP = 1800  # seconds between two points of the "30min" scale
HISTORY_LIMIT = 1024  # most points Netatmo returns per call
HISTORY_PAUSE = 2  # seconds between calls, to go easy on the API
HISTORY_RETRY_DELAYS = (3, 15)  # Netatmo sometimes answers 503 for a short while
HISTORY_TYPES = {
    "base": ("pressure", ("pressure",)),
    "outdoor": ("temperature,humidity", ("temperature", "humidity")),
    "rain": ("sum_rain", ("rain_30min",)),
    "wind": (
        "windstrength,windangle,guststrength,gustangle",
        ("wind_strength", "wind_angle", "gust_strength", "gust_angle"),
    ),
}
MODULE_PREFIXES = {"02:": "outdoor", "05:": "rain", "06:": "wind"}
TOKEN_ERROR_CODES = (None, 2, 3)  # invalid or expired access token


class ApiError(Exception):
    def __init__(self, status, message, code=None):
        super().__init__(f"HTTP {status} : {message}")
        self.status = status
        self.message = message
        self.code = code


class AuthRequired(Exception):
    """The user has to (re)connect the app to their Netatmo account."""


class StationNotFound(Exception):
    pass


_calls = collections.deque()  # times of the requests of the last hour
_calls_lock = threading.Lock()


def calls_last_hour(now=None):
    """How many requests were sent to Netatmo during the last hour."""
    now = time.time() if now is None else now
    with _calls_lock:
        while _calls and _calls[0] < now - 3600:
            _calls.popleft()
        return len(_calls)


def _send(req):
    with _calls_lock:
        _calls.append(time.time())
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", "replace")
        try:
            error = json.loads(raw).get("error", raw)
        except ValueError:
            error = raw
        code = None
        if isinstance(error, dict):
            code = error.get("code")
            error = error.get("message", raw)
        raise ApiError(err.code, str(error)[:200], code) from None


class Tokens:
    """Keeps the OAuth tokens on disk.

    Netatmo invalidates the previous refresh token as soon as a new one is
    issued, so every response is written to disk before it is used.
    """

    def __init__(self, path, client_id, client_secret, seed_refresh_token=""):
        self.path = Path(path)
        self.client_id = client_id
        self.client_secret = client_secret
        self.lock = threading.Lock()
        self.data = {}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        elif seed_refresh_token:
            self.data = {"refresh_token": seed_refresh_token}

    @property
    def connected(self):
        return bool(self.data.get("refresh_token"))

    def _save(self, payload):
        self.data = {
            "access_token": payload["access_token"],
            "refresh_token": payload["refresh_token"],
            "expires_at": time.time() + int(payload.get("expires_in", 10800)),
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data), encoding="utf-8")
        os.replace(tmp, self.path)

    def _token_call(self, fields):
        body = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            **fields,
        }).encode()
        return _send(urllib.request.Request(
            f"{API}/oauth2/token",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        ))

    def authorize_url(self, redirect_uri, state):
        query = urllib.parse.urlencode({
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": SCOPE,
            "state": state,
        })
        return f"{API}/oauth2/authorize?{query}"

    def exchange_code(self, code, redirect_uri):
        with self.lock:
            self._save(self._token_call({
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "scope": SCOPE,
            }))

    def access_token(self, force_refresh=False):
        with self.lock:
            if not self.connected:
                raise AuthRequired("Compte Netatmo non connecté")
            fresh = self.data.get("expires_at", 0) - 60 > time.time()
            if fresh and self.data.get("access_token") and not force_refresh:
                return self.data["access_token"]
            try:
                self._save(self._token_call({
                    "grant_type": "refresh_token",
                    "refresh_token": self.data["refresh_token"],
                }))
            except ApiError as err:
                if err.status == 400 and "invalid_grant" in err.message:
                    self.data = {}
                    self.path.unlink(missing_ok=True)
                    raise AuthRequired("Autorisation Netatmo expirée") from None
                raise
            return self.data["access_token"]


def parse_station(item):
    """Turn one /getpublicdata station into a flat reading (or None)."""
    reading = dict.fromkeys(FIELDS)
    stamps = []
    for module in (item.get("measures") or {}).values():
        names = module.get("type") or []
        if isinstance(names, str):
            names = [names]
        res = module.get("res") or {}
        for stamp in sorted(res, key=int):
            values = res[stamp]
            labels = list(names)
            if labels == ["temperature"] and len(values) == 2:
                labels.append("humidity")
            stamps.append(int(stamp))
            for label, value in zip(labels, values):
                if label in reading:
                    reading[label] = value
        if "rain_timeutc" in module:
            stamps.append(int(module["rain_timeutc"]))
            reading["rain_60min"] = module.get("rain_60min")
            reading["rain_24h"] = module.get("rain_24h")
        if "wind_timeutc" in module:
            stamps.append(int(module["wind_timeutc"]))
            # The official example spells these two keys differently.
            reading["wind_strength"] = module.get("wind_strength", module.get("wind_strengh"))
            reading["gust_strength"] = module.get("gust_strength", module.get("gust_strenght"))
            reading["wind_angle"] = module.get("wind_angle")
            reading["gust_angle"] = module.get("gust_angle")
    if not stamps:
        return None
    reading["ts"] = max(stamps)
    reading["modules"] = find_modules(item)
    reading["location"] = (item.get("place") or {}).get("location")  # [longitude, latitude]
    return reading


def find_modules(item):
    """Map the kinds of sensors of a public station to their module ids."""
    modules = {"base": item.get("_id")}
    for module_id in item.get("measures") or {}:
        kind = MODULE_PREFIXES.get(module_id[:3])
        if kind:
            modules[kind] = module_id
    return modules


def _get(tokens, url):
    for retry in (False, True):
        headers = {"Authorization": f"Bearer {tokens.access_token(force_refresh=retry)}"}
        try:
            return _send(urllib.request.Request(url, headers=headers))
        except ApiError as err:
            if retry or err.status not in (401, 403) or err.code not in TOKEN_ERROR_CODES:
                raise


def _get_patiently(tokens, url):
    for delay in HISTORY_RETRY_DELAYS:
        try:
            return _get(tokens, url)
        except ApiError as err:
            if err.status < 500:
                raise
            time.sleep(delay)
    return _get(tokens, url)


def _read_measures(tokens, params, begin, end):
    """Return {timestamp: values} for a period, in as many calls as it takes."""
    rows = {}
    while begin < end:
        query = urllib.parse.urlencode({
            **params,
            "date_begin": begin,
            "date_end": end,
            "limit": HISTORY_LIMIT,
            "optimize": "false",
        })
        body = _get_patiently(tokens, f"{API}/api/getmeasure?{query}").get("body")
        time.sleep(HISTORY_PAUSE)
        if not body:
            break
        rows.update({int(stamp): values for stamp, values in body.items()})
        if len(body) < HISTORY_LIMIT:
            break
        begin = max(int(stamp) for stamp in body) + 1
    return rows


def fetch_history(tokens, device_id, kind, module_id, begin, end):
    """Return {timestamp on the 30-minute grid: {field: value}} for one sensor."""
    types, fields = HISTORY_TYPES[kind]
    params = {
        "device_id": device_id, "module_id": module_id,
        "scale": "30min", "type": types, "real_time": "true",
    }
    rows = _read_measures(tokens, params, begin, end)
    return {
        stamp // HISTORY_STEP * HISTORY_STEP: dict(zip(fields, values))
        for stamp, values in rows.items()
    }


def fetch_daily_rain(tokens, device_id, module_id, begin, end):
    """Return {"YYYY-MM-DD": mm} using Netatmo's own daily totals.

    Each total is stamped around midday, so its local date is the day it covers.
    """
    params = {"device_id": device_id, "module_id": module_id, "scale": "1day", "type": "sum_rain"}
    rows = _read_measures(tokens, params, begin, end)
    return {
        datetime.date.fromtimestamp(stamp).isoformat(): values[0]
        for stamp, values in rows.items() if values[0] is not None
    }


def fetch_area(tokens, lat, lon, half_lat, half_lon):
    """Return the raw public stations around a position."""
    query = urllib.parse.urlencode({
        "lat_ne": lat + half_lat,
        "lon_ne": lon + half_lon,
        "lat_sw": lat - half_lat,
        "lon_sw": lon - half_lon,
    })
    return _get_patiently(tokens, f"{API}/api/getpublicdata?{query}").get("body") or []


def fetch_station(tokens, station):
    """Return the latest reading of a public station."""
    box = station.get("box", BOX_DEG)
    query = urllib.parse.urlencode({
        "lat_ne": station["lat"] + box,
        "lon_ne": station["lon"] + box,
        "lat_sw": station["lat"] - box,
        "lon_sw": station["lon"] - box,
    })
    payload = _get_patiently(tokens, f"{API}/api/getpublicdata?{query}")
    wanted = station["id"].lower()
    for item in payload.get("body") or []:
        if str(item.get("_id", "")).lower() == wanted:
            reading = parse_station(item)
            if reading:
                return reading
    raise StationNotFound("Station absente de la zone demandée : vérifier id, lat et lon")
