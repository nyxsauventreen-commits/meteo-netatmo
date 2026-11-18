"""Estimated wind from Open-Meteo, for stations without an anemometer.

These values come from weather models, not from a sensor at the station.
Open-Meteo data is licensed under CC BY 4.0: the dashboard shows the credit.
"""
import json
import urllib.parse
import urllib.request

FORECAST = "https://api.open-meteo.com/v1/forecast"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
HOURLY = ("wind_speed_10m", "wind_direction_10m", "wind_gusts_10m")
FIELDS = ("wind_strength", "wind_angle", "gust_strength")


def _read(url, params):
    query = urllib.parse.urlencode({
        **params,
        "hourly": ",".join(HOURLY),
        "wind_speed_unit": "kmh",
        "timeformat": "unixtime",
    })
    with urllib.request.urlopen(f"{url}?{query}", timeout=30) as response:
        hourly = json.load(response)["hourly"]
    rows = {}
    for ts, *values in zip(hourly["time"], *(hourly[name] for name in HOURLY)):
        if values[0] is not None:
            rows[ts] = dict(zip(FIELDS, values))
    return rows


def fetch_recent(lat, lon, past_days):
    """Return {timestamp: values} for the last days, up to the end of today."""
    return _read(FORECAST, {"latitude": lat, "longitude": lon, "past_days": past_days, "forecast_days": 1})


def fetch_archive(lat, lon, start_date, end_date):
    """Return {timestamp: values} between two "YYYY-MM-DD" dates."""
    return _read(ARCHIVE, {"latitude": lat, "longitude": lon, "start_date": start_date, "end_date": end_date})
