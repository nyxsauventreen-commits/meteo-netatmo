"""Short-term outlook from a station's own pressure, like a barometer.

Only the change of pressure is used, never its value: some stations are several
hPa off, which does not affect a trend.
"""
import math

WINDOW = 3 * 3600  # the trend is expressed over 3 hours, as weather services do
MIN_SPAN = 2 * 3600
MIN_POINTS = 4
MAX_AGE = 3600  # older data says nothing about now
FOG_SPREAD = 1.5  # degrees between air and dew point, and below this fog may form

# Upper limit of each level, in hPa over 3 hours.
LEVELS = (
    (-3.5, "fall_fast", "baisse rapide, dégradation probable, pluie ou vent possibles"),
    (-1.5, "fall", "baisse, le temps se dégrade"),
    (-0.5, "fall_slow", "baisse lente, le ciel peut se couvrir"),
    (0.5, "stable", "stable, peu de changement"),
    (1.5, "rise_slow", "hausse lente, légère amélioration"),
    (3.5, "rise", "hausse, amélioration"),
    (math.inf, "rise_fast", "hausse rapide, amélioration mais temps instable, vent possible"),
)


def trend(points, now):
    """Return the pressure change over 3 hours from [(ts, hPa)], or None.

    A straight line through all the points resists the jitter of single readings.
    """
    if len(points) < MIN_POINTS:
        return None
    first, last = points[0][0], points[-1][0]
    if last - first < MIN_SPAN or now - last > MAX_AGE:
        return None
    mean_ts = sum(ts for ts, _ in points) / len(points)
    mean_value = sum(value for _, value in points) / len(points)
    spread = sum((ts - mean_ts) ** 2 for ts, _ in points)
    slope = sum((ts - mean_ts) * (value - mean_value) for ts, value in points) / spread
    return slope * WINDOW


def dew_point(temperature, humidity):
    """Magnus formula, in degrees Celsius."""
    gamma = math.log(humidity / 100) + 17.62 * temperature / (243.12 + temperature)
    return 243.12 * gamma / (17.62 - gamma)


def fog_is_possible(temperature, humidity):
    if temperature is None or not humidity:
        return False
    return temperature - dew_point(temperature, humidity) <= FOG_SPREAD


def outlook(points, temperature, humidity, now):
    """Return {"change", "level", "text", "fog"} for a station, or None."""
    change = trend(points, now)
    if change is None:
        return None
    _, level, text = next(item for item in LEVELS if change < item[0])
    return {
        "change": round(change, 1),
        "level": level,
        "text": text,
        "fog": fog_is_possible(temperature, humidity),
    }
