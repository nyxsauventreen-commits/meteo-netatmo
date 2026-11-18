"""Figures drawn from the measures already stored: nothing extra is saved."""
import datetime

FROST = 0  # °C
HOT = 30  # °C
RAINY = 1  # mm in a day
YEAR = 365 * 86400


def extreme(db, station_id, since, until, column, order):
    row = db.execute(
        f"SELECT ts, {column} AS value FROM measures WHERE station_id = ? AND ts BETWEEN ? AND ? "
        f"AND {column} IS NOT NULL ORDER BY {column} {order} LIMIT 1",
        (station_id, since, until),
    ).fetchone()
    return {"ts": row["ts"], "value": row["value"]} if row else None


def rain(db, station_id, since, until):
    """Return the official daily totals of a period, or None without a rain gauge."""
    days = db.execute(
        "SELECT day, rain FROM rain_days WHERE station_id = ? AND day BETWEEN ? AND ?",
        (
            station_id,
            datetime.date.fromtimestamp(since).isoformat(),
            datetime.date.fromtimestamp(until).isoformat(),
        ),
    ).fetchall()
    if not days:
        return None
    wettest = max(days, key=lambda row: row["rain"])
    return {
        "total": round(sum(row["rain"] for row in days), 1),
        "rainy_days": sum(row["rain"] >= RAINY for row in days),
        "wettest": {"day": wettest["day"], "value": wettest["rain"]},
    }


def mean_temperature(db, station_id, since, until):
    mean = db.execute(
        "SELECT AVG(temperature) FROM measures WHERE station_id = ? AND ts BETWEEN ? AND ?",
        (station_id, since, until),
    ).fetchone()[0]
    return None if mean is None else round(mean, 1)


def period(db, station_id, since, until):
    days = db.execute(
        "SELECT MIN(temperature) AS low, MAX(temperature) AS high FROM measures "
        "WHERE station_id = ? AND ts BETWEEN ? AND ? AND temperature IS NOT NULL "
        "GROUP BY date(ts, 'unixepoch', 'localtime')",
        (station_id, since, until),
    ).fetchall()
    return {
        "mean": mean_temperature(db, station_id, since, until),
        "hottest": extreme(db, station_id, since, until, "temperature", "DESC"),
        "coldest": extreme(db, station_id, since, until, "temperature", "ASC"),
        "gust": extreme(db, station_id, since, until, "gust_strength", "DESC"),
        "days": len(days),
        "frost_days": sum(day["low"] <= FROST for day in days),
        "hot_days": sum(day["high"] >= HOT for day in days),
        "rain": rain(db, station_id, since, until),
    }


def last_year(db, station_id, since, until):
    """Return the same period one year earlier, if the history reaches that far."""
    oldest = db.execute("SELECT MIN(ts) FROM measures WHERE station_id = ?", (station_id,)).fetchone()[0]
    if oldest is None or oldest > since - YEAR:
        return None
    past = rain(db, station_id, since - YEAR, until - YEAR)
    return {
        "mean": mean_temperature(db, station_id, since - YEAR, until - YEAR),
        "rain": past["total"] if past else None,
    }


MONTHS = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)
# Meteorological seasons: whole months, so that totals compare well from one year to the next.
SEASONS = ((12, "de l'hiver"), (3, "du printemps"), (6, "de l'été"), (9, "de l'automne"))


def one_year_earlier(day):
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 February
        return day.replace(year=day.year - 1, day=28)


def season_start(today):
    month = max((start for start, _ in SEASONS if start <= today.month), default=12)
    year = today.year - 1 if month == 12 and today.month < 12 else today.year
    return datetime.date(year, month, 1), dict(SEASONS)[month]


def rain_total(db, station_id, first, last):
    total = db.execute(
        "SELECT SUM(rain) FROM rain_days WHERE station_id = ? AND day BETWEEN ? AND ?",
        (station_id, first.isoformat(), last.isoformat()),
    ).fetchone()[0]
    return round(total or 0, 1)


def rain_calendar(db, station_id, today=None):
    """Rain of the current month, season and year so far, and of the same dates a year earlier.

    None without a rain gauge. A year earlier is None when the totals do not reach that far.
    """
    today = today or datetime.date.today()
    oldest = db.execute("SELECT MIN(day) FROM rain_days WHERE station_id = ?", (station_id,)).fetchone()[0]
    if oldest is None:
        return None
    oldest = datetime.date.fromisoformat(oldest)
    season, season_name = season_start(today)
    spans = (
        ("month", f"en {MONTHS[today.month - 1]}", today.replace(day=1)),
        ("season", season_name, season),
        ("year", f"en {today.year}", today.replace(month=1, day=1)),
    )
    calendar = {}
    for key, label, first in spans:
        past_first, past_last = one_year_earlier(first), one_year_earlier(today)
        calendar[key] = {
            "label": label,
            "since": first.isoformat(),
            "total": rain_total(db, station_id, first, today),
            "last_year": rain_total(db, station_id, past_first, past_last) if oldest <= past_first else None,
        }
    return calendar


def for_station(db, station_id, since, until):
    return {
        "period": period(db, station_id, since, until),
        "records": period(db, station_id, 0, until),
        "last_year": last_year(db, station_id, since, until),
        "rain_calendar": rain_calendar(db, station_id),
    }
