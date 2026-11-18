"""Current sky and forecasts from Open-Meteo (weather models, CC BY 4.0)."""
import json
import urllib.parse
import urllib.request

URL = "https://api.open-meteo.com/v1/forecast"
DAYS = 15
HOURS = 26  # a full day ahead, even just before the hourly refresh
DAILY = {
    "weather_code": "code",
    "temperature_2m_max": "max",
    "temperature_2m_min": "min",
    "precipitation_sum": "rain",
    "precipitation_probability_max": "rain_chance",
    "wind_speed_10m_max": "wind",
    "wind_gusts_10m_max": "gust",
    "sunrise": "sunrise",
    "sunset": "sunset",
    "daylight_duration": "daylight",
}
HOURLY = {
    "weather_code": "code",
    "temperature_2m": "temperature",
    "is_day": "is_day",
    "precipitation_probability": "rain_chance",
    "wind_speed_10m": "wind",
}


def rows(block, names):
    """Turn Open-Meteo's columns into one dictionary per time step."""
    return [
        {"time": time, **{name: block[key][index] for key, name in names.items()}}
        for index, time in enumerate(block["time"])
    ]


def fetch_forecast(lat, lon):
    """Return {"code", "is_day", "days": [...], "hours": [...]}; times are local to the station."""
    query = urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lon,
        "current": "weather_code,is_day",
        "daily": ",".join(DAILY),
        "hourly": ",".join(HOURLY),
        "forecast_days": DAYS,
        "forecast_hours": HOURS,
        "timezone": "auto",
        "wind_speed_unit": "kmh",
    })
    with urllib.request.urlopen(f"{URL}?{query}", timeout=30) as response:
        data = json.load(response)
    days = [{"date": row.pop("time"), **row} for row in rows(data["daily"], DAILY)]
    current = data["current"]
    return {
        "code": current["weather_code"],
        "is_day": bool(current["is_day"]),
        "days": days,
        "hours": rows(data["hourly"], HOURLY),
    }
