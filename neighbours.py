"""Median of the public Netatmo stations around a station.

The median ignores badly placed or badly set stations, unlike an average. The result
is an extra indication and never replaces the measures of the station itself.
"""
import math
import statistics
import time

import netatmo

MAX_RADIUS_KM = 10
MIN_STATIONS = 5
MAX_AGE = 2 * 3600  # older readings say nothing about now
LAT_KM = 111.2
HALF_LAT = 0.05  # one quarter of the search area, in degrees
PAUSE = 2  # seconds between two requests
FIELDS = ("temperature", "humidity", "pressure")


def distance_km(lat1, lon1, lat2, lon2):
    mean_lat = math.radians((lat1 + lat2) / 2)
    return math.hypot((lat2 - lat1) * LAT_KM, (lon2 - lon1) * LAT_KM * math.cos(mean_lat))


def fetch_around(tokens, lat, lon):
    """Return the readings around a position, in four small areas.

    One large area would not do: Netatmo returns a limited sample of its stations.
    """
    half_lon = HALF_LAT / math.cos(math.radians(lat)) * 1.05
    found = {}
    for north in (-1, 1):
        for east in (-1, 1):
            items = netatmo.fetch_area(tokens, lat + north * HALF_LAT, lon + east * half_lon, HALF_LAT, half_lon)
            for item in items:
                reading = netatmo.parse_station(item)
                if reading and reading.get("location"):
                    found[item["_id"]] = {
                        "id": item["_id"],
                        "lon": reading["location"][0],
                        "lat": reading["location"][1],
                        "ts": reading["ts"],
                        **{name: reading[name] for name in FIELDS},
                    }
            time.sleep(PAUSE)
    return list(found.values())


def nearby(readings, station, radius, now):
    """Return the recent readings within `radius` km, leaving out the station itself."""
    return [
        reading for reading in readings
        if reading["id"] != station["id"]
        and now - reading["ts"] <= MAX_AGE
        and distance_km(station["lat"], station["lon"], reading["lat"], reading["lon"]) <= radius
    ]


def summarise(readings, station, radius, now):
    """Return the medians within `radius` km."""
    near = nearby(readings, station, radius, now)
    summary = {"radius": radius, "count": len(near)}
    if len(near) < MIN_STATIONS:
        return summary
    for name in FIELDS:
        values = [reading[name] for reading in near if reading[name] is not None]
        summary[name] = round(statistics.median(values), 1) if len(values) >= MIN_STATIONS else None
    return summary


def compare(readings, station, latest, radius, now):
    """Return how a station stands among its neighbours, or None with too few of them."""
    summary = summarise(readings, station, radius, now)
    if "temperature" not in summary:
        return None
    gaps = {
        name: None if summary[name] is None or latest[name] is None else round(latest[name] - summary[name], 1)
        for name in FIELDS
    }
    temperatures = [
        reading["temperature"] for reading in nearby(readings, station, radius, now)
        if reading["temperature"] is not None
    ]
    result = {"radius": radius, "count": summary["count"], "gaps": gaps, "low": None, "high": None, "warmer_than": None}
    if temperatures and latest["temperature"] is not None:
        colder = sum(value < latest["temperature"] for value in temperatures)
        result.update(
            low=min(temperatures), high=max(temperatures),
            warmer_than=round(100 * colder / len(temperatures)),
        )
    return result
