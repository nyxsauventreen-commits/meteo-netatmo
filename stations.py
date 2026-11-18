"""Helpers to add a station from its Netatmo map link."""
import json
import re
import urllib.parse
import urllib.request

GEOCODER = "https://data.geopf.fr/geocodage/search"  # French public address service
WORLD_GEOCODER = "https://geocoding-api.open-meteo.com/v1/search"  # places outside France
MAX_PLACES = 5
MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$", re.I)
COORDINATES = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


def parse_station_id(text):
    """Return the station id found in a map link (or a bare id), else None."""
    text = text.strip()
    if MAC.match(text):
        return text.lower()
    url = urllib.parse.urlsplit(text if "://" in text else f"https://{text}")
    host = url.hostname or ""
    if host != "netatmo.com" and not host.endswith(".netatmo.com"):
        return None
    value = urllib.parse.parse_qs(url.query).get("stationid", [""])[0].strip()
    return value.lower() if MAC.match(value) else None


def weathermap_url(station_id):
    return f"https://weathermap.netatmo.com/?stationid={station_id}&zoom=13"


def _french_places(text):
    query = urllib.parse.urlencode({"q": text, "type": "municipality", "limit": MAX_PLACES})
    with urllib.request.urlopen(f"{GEOCODER}?{query}", timeout=20) as response:
        features = json.load(response).get("features") or []
    places = []
    for feature in features:
        lon, lat = feature["geometry"]["coordinates"]
        properties = feature.get("properties") or {}
        label = " ".join(filter(None, (properties.get("label"), properties.get("postcode"))))
        places.append({"lat": lat, "lon": lon, "label": label})
    return places


def _world_places(text):
    query = urllib.parse.urlencode({"name": text, "count": MAX_PLACES, "language": "fr", "format": "json"})
    with urllib.request.urlopen(f"{WORLD_GEOCODER}?{query}", timeout=20) as response:
        results = json.load(response).get("results") or []
    return [
        {
            "lat": result["latitude"],
            "lon": result["longitude"],
            "label": ", ".join(filter(None, (result.get("name"), result.get("country")))),
        }
        for result in results
    ]


def find_places(text):
    """Return possible locations for coordinates or a place name, French communes first."""
    match = COORDINATES.match(text)
    if match:
        lat, lon = float(match[1]), float(match[2])
        if abs(lat) <= 85 and abs(lon) <= 180:
            return [{"lat": lat, "lon": lon, "label": f"{lat}, {lon}"}]
        return []
    text = text.strip()
    places = _french_places(text)
    try:
        places += _world_places(text)
    except OSError:
        if not places:
            raise
    unique = {}
    for place in places:
        unique.setdefault((round(place["lat"], 1), round(place["lon"], 1)), place)
    return list(unique.values())
