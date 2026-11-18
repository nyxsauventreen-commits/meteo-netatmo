"""Official weather warnings from MeteoAlarm (EUMETNET, CC BY 4.0), by French département.

The MeteoAlarm server does not send its intermediate certificate, which Python needs:
meteoalarm-ca.pem holds that public certificate. Certificates stay fully verified.
"""
import datetime
import json
import re
import ssl
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

FEED = "https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-{country}"
COMMUNES = "https://geo.api.gouv.fr/communes"
CERTIFICATE = Path(__file__).resolve().parent / "meteoalarm-ca.pem"
ATOM = "{http://www.w3.org/2005/Atom}"
CAP = "{urn:oasis:names:tc:emergency:cap:1.2}"
TITLE = re.compile(r"^(Yellow|Orange|Red)\s+(.+?)\s+Warning", re.I)
LEVELS = {"yellow": 1, "orange": 2, "red": 3}
LEVEL_NAMES = {"yellow": "jaune", "orange": "orange", "red": "rouge"}
ANDORRA = (42.42, 42.66, 1.40, 1.79)  # south, north, west, east
# hazard in the feed -> (French name, picture shown on the card)
HAZARDS = {
    "wind": ("vent", "💨"),
    "rain": ("pluie", "🌧️"),
    "rain-flood": ("pluie-inondation", "🌧️"),
    "flooding": ("crues", "🌊"),
    "flood": ("crues", "🌊"),
    "thunderstorm": ("orages", "⛈️"),
    "snow-ice": ("neige-verglas", "❄️"),
    "snow/ice": ("neige-verglas", "❄️"),
    "snow": ("neige", "❄️"),
    "ice": ("verglas", "❄️"),
    "high-temperature": ("canicule", "🌡️"),
    "heat": ("canicule", "🌡️"),
    "low-temperature": ("grand froid", "🥶"),
    "cold": ("grand froid", "🥶"),
    "avalanches": ("avalanches", "🏔️"),
    "avalanche": ("avalanches", "🏔️"),
    "coastalevent": ("vagues-submersion", "🌊"),
    "coastal": ("vagues-submersion", "🌊"),
    "forest-fire": ("feux de forêt", "🔥"),
    "fog": ("brouillard", "🌫️"),
}


def normalise(name):
    text = unicodedata.normalize("NFD", name.casefold())
    return re.sub(r"[^a-z]", "", "".join(c for c in text if not unicodedata.combining(c)))


def parse_time(text):
    return int(datetime.datetime.fromisoformat(text).timestamp())


def parse_feed(xml, now):
    """Return the warnings still valid: {"area", "level", "hazard", "icon", "start", "end"}."""
    warnings = []
    for entry in ET.fromstring(xml).findall(f"{ATOM}entry"):
        match = TITLE.match(entry.findtext(f"{ATOM}title") or "")
        area = entry.findtext(f"{CAP}areaDesc")
        expires = entry.findtext(f"{CAP}expires")
        onset = entry.findtext(f"{CAP}onset")
        kind = (entry.findtext(f"{CAP}message_type") or "").lower()
        if not (match and area and expires and onset) or kind == "cancel":
            continue
        end = parse_time(expires)
        if end <= now:
            continue
        hazard, icon = HAZARDS.get(match[2].lower(), (match[2].lower(), "⚠️"))
        warnings.append({
            "area": area,
            "level": match[1].lower(),
            "hazard": hazard,
            "icon": icon,
            "start": parse_time(onset),
            "end": end,
        })
    return warnings


def for_area(warnings, area):
    """Keep one warning per hazard: the strongest, then the one that starts first."""
    wanted = normalise(area)
    best = {}
    for warning in sorted(warnings, key=lambda w: (-LEVELS[w["level"]], w["start"])):
        if normalise(warning["area"]) == wanted:
            best.setdefault(warning["hazard"], warning)
    return sorted(best.values(), key=lambda w: -LEVELS[w["level"]])


def fetch_feed(country, now):
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=CERTIFICATE)
    request = urllib.request.Request(FEED.format(country=country), headers={"User-Agent": "meteo-perso"})
    with urllib.request.urlopen(request, timeout=30, context=context) as response:
        return parse_feed(response.read(), now)


def in_andorra(lat, lon):
    south, north, west, east = ANDORRA
    return south <= lat <= north and west <= lon <= east


def find_department(lat, lon):
    """Return the name of the French département at a position, or None."""
    query = urllib.parse.urlencode({"lat": lat, "lon": lon, "fields": "departement", "format": "json"})
    with urllib.request.urlopen(f"{COMMUNES}?{query}", timeout=20) as response:
        communes = json.load(response)
    return communes[0]["departement"]["nom"] if communes else None
