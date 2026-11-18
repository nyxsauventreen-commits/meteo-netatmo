import datetime
import io
import json
import sqlite3
import time
import unittest
from unittest import mock

import alerts
import forecast
import neighbours
import netatmo
import stats

NOW = 1_800_000_000


def entry(title, area, onset, expires, kind="Alert"):
    return f"""<entry><title>{title}</title>
      <cap:areaDesc>{area}</cap:areaDesc><cap:onset>{onset}</cap:onset>
      <cap:expires>{expires}</cap:expires><cap:message_type>{kind}</cap:message_type></entry>"""


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()


def feed(*entries):
    return f"""<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:cap="urn:oasis:names:tc:emergency:cap:1.2">{''.join(entries)}</feed>""".encode()


class Alerts(unittest.TestCase):
    def parse(self, *entries):
        return alerts.parse_feed(feed(*entries), NOW)

    def test_reads_a_warning(self):
        warning = self.parse(entry(
            "Orange Rain-flood Warning issued for France - Corse-du-Sud", "Corse-du-Sud", iso(NOW - 60), iso(NOW + 60),
        ))[0]
        self.assertEqual(
            (warning["level"], warning["hazard"], warning["icon"], warning["start"], warning["end"]),
            ("orange", "pluie-inondation", "🌧️", NOW - 60, NOW + 60),
        )

    def test_expired_and_cancelled_are_left_out(self):
        self.assertEqual(self.parse(
            entry("Yellow Wind Warning issued for France - Gard", "Gard", iso(NOW - 90), iso(NOW - 10)),
            entry("Yellow Wind Warning issued for France - Gard", "Gard", iso(NOW - 90), iso(NOW + 90), "Cancel"),
        ), [])

    def test_upcoming_warning_is_kept(self):
        self.assertEqual(len(self.parse(
            entry("Yellow Wind Warning issued for France - Gard", "Gard", iso(NOW + 60), iso(NOW + 90)),
        )), 1)

    def test_unknown_hazard_stays_readable(self):
        warning = self.parse(entry("Red Meteor Warning issued for France - Gard", "Gard", iso(NOW), iso(NOW + 9)))[0]
        self.assertEqual((warning["hazard"], warning["icon"]), ("meteor", "⚠️"))

    def test_green_or_unrelated_titles_are_ignored(self):
        self.assertEqual(self.parse(entry("Green Wind Notice", "Gard", iso(NOW), iso(NOW + 9))), [])

    def test_area_ignores_accents_and_hyphens(self):
        warnings = self.parse(entry("Yellow Wind Warning x", "Hérault", iso(NOW), iso(NOW + 9)))
        self.assertEqual(len(alerts.for_area(warnings, "herault")), 1)
        self.assertEqual(alerts.for_area(warnings, "Gard"), [])

    def test_one_warning_per_hazard_the_strongest_first(self):
        warnings = self.parse(
            entry("Yellow Wind Warning x", "Gard", iso(NOW), iso(NOW + 9)),
            entry("Orange Wind Warning x", "Gard", iso(NOW), iso(NOW + 9)),
            entry("Red Thunderstorm Warning x", "Gard", iso(NOW), iso(NOW + 9)),
        )
        found = alerts.for_area(warnings, "Gard")
        self.assertEqual([(w["hazard"], w["level"]) for w in found], [("orages", "red"), ("vent", "orange")])

    def test_andorra(self):
        self.assertTrue(alerts.in_andorra(42.54, 1.73))
        self.assertFalse(alerts.in_andorra(43.53, 1.49))

    def test_department_lookup(self):
        reply = io.BytesIO(json.dumps([{"departement": {"code": "31", "nom": "Haute-Garonne"}}]).encode())
        with mock.patch("urllib.request.urlopen", return_value=reply):
            self.assertEqual(alerts.find_department(43.5, 1.4), "Haute-Garonne")

    def test_outside_france_has_no_department(self):
        with mock.patch("urllib.request.urlopen", return_value=io.BytesIO(b"[]")):
            self.assertIsNone(alerts.find_department(42.5, 1.7))


class Neighbours(unittest.TestCase):
    STATION = {"id": "me", "lat": 43.5, "lon": 1.5}

    def reading(self, name, km_north=1, temperature=10, age=0):
        return {
            "id": name, "lat": 43.5 + km_north / neighbours.LAT_KM, "lon": 1.5, "ts": NOW - age,
            "temperature": temperature, "humidity": 80, "pressure": 1015,
        }

    def test_distance(self):
        self.assertAlmostEqual(neighbours.distance_km(43.5, 1.5, 43.59, 1.5), 10, delta=0.1)
        self.assertAlmostEqual(neighbours.distance_km(43.5, 1.5, 43.5, 1.624), 10, delta=0.1)

    def test_median_ignores_a_badly_placed_station(self):
        temperatures = {"a": 10, "b": 10, "c": 11, "d": 12, "e": 42}
        readings = [self.reading(name, temperature=value) for name, value in temperatures.items()]
        self.assertEqual(neighbours.summarise(readings, self.STATION, 5, NOW)["temperature"], 11)

    def test_four_stations_are_not_enough(self):
        readings = [self.reading(name) for name in "abcd"]
        self.assertEqual(neighbours.summarise(readings, self.STATION, 5, NOW), {"radius": 5, "count": 4})

    def test_radius_counts_only_near_stations(self):
        readings = [self.reading(n, km) for n, km in (("a", 1), ("b", 3), ("c", 4), ("d", 9))]
        self.assertEqual(neighbours.summarise(readings, self.STATION, 5, NOW)["count"], 3)

    def test_station_itself_and_old_readings_are_left_out(self):
        readings = [self.reading("me"), self.reading("a"), self.reading("b", age=3 * 3600)]
        self.assertEqual(neighbours.summarise(readings, self.STATION, 5, NOW)["count"], 1)

    def test_too_few_stations_gives_no_median(self):
        summary = neighbours.summarise([self.reading("a")], self.STATION, 5, NOW)
        self.assertEqual(summary, {"radius": 5, "count": 1})

    def test_missing_values_are_skipped(self):
        readings = [self.reading(n) for n in "abcde"]
        readings[0]["humidity"] = None
        summary = neighbours.summarise(readings, self.STATION, 5, NOW)
        self.assertIsNone(summary["humidity"])
        self.assertEqual(summary["pressure"], 1015)

    def test_area_is_read_in_four_requests_without_doubles(self):
        item = {
            "_id": "a", "place": {"location": [1.5, 43.5]},
            "measures": {"m": {"res": {str(NOW): [10, 80]}, "type": ["temperature", "humidity"]}},
        }
        with mock.patch.object(netatmo, "fetch_area", return_value=[item]) as fetch, \
                mock.patch.object(neighbours.time, "sleep"):
            readings = neighbours.fetch_around(mock.Mock(), 43.5, 1.5)
        self.assertEqual(fetch.call_count, 4)
        self.assertEqual([r["id"] for r in readings], ["a"])


class NetatmoCalls(unittest.TestCase):
    def test_requests_of_the_last_hour_are_counted(self):
        before = netatmo.calls_last_hour()
        reply = io.BytesIO(b"{}")
        with mock.patch("urllib.request.urlopen", return_value=reply):
            netatmo._send("https://api.netatmo.com/test")
        self.assertEqual(netatmo.calls_last_hour(), before + 1)
        self.assertEqual(netatmo.calls_last_hour(time.time() + 3601), 0)


class Comparison(unittest.TestCase):
    STATION = {"id": "me", "lat": 43.5, "lon": 1.5}
    LATEST = {"temperature": 12, "humidity": 70, "pressure": 1026}

    def readings(self, count=5):
        return [
            {"id": str(n), "lat": 43.5, "lon": 1.5, "ts": NOW, "temperature": 8 + n, "humidity": 80, "pressure": 1015}
            for n in range(count)
        ]

    def test_station_is_placed_among_its_neighbours(self):
        result = neighbours.compare(self.readings(), self.STATION, self.LATEST, 5, NOW)
        self.assertEqual(result["gaps"], {"temperature": 2, "humidity": -10, "pressure": 11})
        self.assertEqual((result["low"], result["high"], result["warmer_than"]), (8, 12, 80))

    def test_too_few_neighbours_gives_nothing(self):
        self.assertIsNone(neighbours.compare(self.readings(4), self.STATION, self.LATEST, 5, NOW))

    def test_missing_measure_of_the_station_is_left_out(self):
        latest = {**self.LATEST, "temperature": None}
        result = neighbours.compare(self.readings(), self.STATION, latest, 5, NOW)
        self.assertIsNone(result["gaps"]["temperature"])
        self.assertIsNone(result["warmer_than"])


class Stats(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE measures (station_id TEXT, ts INTEGER, temperature REAL, gust_strength REAL)")
        self.db.execute("CREATE TABLE rain_days (station_id TEXT, day TEXT, rain REAL)")
        self.addCleanup(self.db.close)
        self.start = int(datetime.datetime(2026, 1, 10, 12).timestamp())

    def measure(self, days, temperature, gust=None):
        self.db.execute("INSERT INTO measures VALUES ('s', ?, ?, ?)", (self.start + days * 86400, temperature, gust))

    def test_period_figures(self):
        self.measure(0, -2)
        self.measure(1, 4, 30)
        self.measure(2, 31)
        self.db.execute("INSERT INTO rain_days VALUES ('s', '2026-01-11', 12.5), ('s', '2026-01-12', 0.4)")
        result = stats.period(self.db, "s", self.start, self.start + 2 * 86400)
        self.assertEqual((result["days"], result["frost_days"], result["hot_days"]), (3, 1, 1))
        self.assertEqual((result["hottest"]["value"], result["coldest"]["value"], result["gust"]["value"]), (31, -2, 30))
        self.assertEqual(result["rain"], {"total": 12.9, "rainy_days": 1, "wettest": {"day": "2026-01-11", "value": 12.5}})

    def test_without_rain_gauge_or_measures(self):
        result = stats.period(self.db, "s", self.start, self.start + 86400)
        self.assertIsNone(result["rain"])
        self.assertIsNone(result["mean"])
        self.assertIsNone(result["hottest"])

    def test_last_year_needs_a_history_that_long(self):
        self.measure(0, 5)
        self.assertIsNone(stats.last_year(self.db, "s", self.start, self.start + 86400))
        self.assertEqual(
            stats.last_year(self.db, "s", self.start + stats.YEAR, self.start + stats.YEAR + 86400),
            {"mean": 5, "rain": None},
        )


class RainCalendar(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE rain_days (station_id TEXT, day TEXT, rain REAL)")
        start = datetime.date(2025, 1, 1)
        self.db.executemany(
            "INSERT INTO rain_days VALUES ('s', ?, ?)",
            [((start + datetime.timedelta(days=n)).isoformat(), 1.0) for n in range(700)],
        )

    def test_month_season_and_year_so_far_with_last_year(self):
        calendar = stats.rain_calendar(self.db, "s", datetime.date(2026, 11, 18))
        self.assertEqual(calendar["month"], {"label": "en novembre", "since": "2026-11-01", "total": 18.0, "last_year": 18.0})
        self.assertEqual((calendar["season"]["label"], calendar["season"]["since"]), ("de l'automne", "2026-09-01"))
        self.assertEqual(calendar["season"]["total"], 30 + 31 + 18)
        self.assertEqual(calendar["year"]["total"], 322.0)
        self.assertEqual(calendar["year"]["last_year"], 322.0)

    def test_winter_starts_in_december_of_the_year_before(self):
        calendar = stats.rain_calendar(self.db, "s", datetime.date(2026, 2, 10))
        self.assertEqual((calendar["season"]["label"], calendar["season"]["since"]), ("de l'hiver", "2025-12-01"))
        self.assertIsNone(calendar["season"]["last_year"])  # the totals start in January 2025

    def test_leap_day(self):
        self.assertEqual(stats.one_year_earlier(datetime.date(2028, 2, 29)), datetime.date(2027, 2, 28))

    def test_no_rain_gauge(self):
        self.assertIsNone(stats.rain_calendar(self.db, "autre", datetime.date(2026, 11, 18)))


class Hours(unittest.TestCase):
    ANSWER = {
        "current": {"weather_code": 2, "is_day": 1},
        "daily": {"time": ["2026-11-06"], **{key: [1] for key in forecast.DAILY}},
        "hourly": {
            "time": ["2026-11-06T13:00"], "weather_code": [3], "temperature_2m": [9.7], "is_day": [1],
            "precipitation_probability": [20], "wind_speed_10m": [5],
        },
    }

    def test_hours_and_days_come_back_separately(self):
        with mock.patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(self.ANSWER).encode())):
            result = forecast.fetch_forecast(43.5, 1.5)
        self.assertEqual(result["hours"], [{
            "time": "2026-11-06T13:00", "code": 3, "temperature": 9.7,
            "is_day": 1, "rain_chance": 20, "wind": 5,
        }])
        self.assertEqual(result["days"][0]["date"], "2026-11-06")

    def test_fifteen_days_are_requested(self):
        reply = io.BytesIO(json.dumps(self.ANSWER).encode())
        with mock.patch("urllib.request.urlopen", return_value=reply) as request:
            forecast.fetch_forecast(43.5, 1.5)
        self.assertIn("forecast_days=15", request.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
