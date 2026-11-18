import base64
import datetime
import http.client
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import alerts
import app
import forecast
import neighbours
import netatmo
import stations
import wind_estimate

LINK = "https://weathermap.netatmo.com/?stationid=70:ee:50:ab:cd:ef&zoom=13.391287926189841"
SECRET = "do-not-lose-me"
HOMONYMS = [
    {"lat": 44.11, "lon": 0.89, "label": "Valence 82400"},
    {"lat": 44.93, "lon": 4.90, "label": "Valence 26000"},
]


class ParseStationId(unittest.TestCase):
    def test_map_link(self):
        self.assertEqual(stations.parse_station_id(LINK), "70:ee:50:ab:cd:ef")

    def test_encoded_link_and_spaces(self):
        link = "  https://weathermap.netatmo.com/?stationid=70%3Aee%3A50%3AAB%3Acd%3Aef "
        self.assertEqual(stations.parse_station_id(link), "70:ee:50:ab:cd:ef")

    def test_bare_id(self):
        self.assertEqual(stations.parse_station_id("70:EE:50:AB:CD:EF"), "70:ee:50:ab:cd:ef")

    def test_rejected(self):
        for text in ("", "bonjour", "https://weathermap.netatmo.com/?zoom=11",
                     "https://evil.example/?stationid=70:ee:50:ab:cd:ef",
                     "https://notnetatmo.com/?stationid=70:ee:50:ab:cd:ef"):
            self.assertIsNone(stations.parse_station_id(text), text)


class FindPlaces(unittest.TestCase):
    def test_coordinates_skip_the_geocoder(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            place = stations.find_places("44.93, 4.89")[0]
        urlopen.assert_not_called()
        self.assertEqual((place["lat"], place["lon"]), (44.93, 4.89))

    def test_impossible_coordinates(self):
        self.assertEqual(stations.find_places("95.0, 4.89"), [])

    FRENCH = {"features": [{
        "geometry": {"coordinates": [4.892, 44.933]},
        "properties": {"label": "Valence", "postcode": "26000"},
    }]}

    def replies(self, *answers):
        return [io.BytesIO(json.dumps(answer).encode()) for answer in answers]

    def test_commune(self):
        with mock.patch("urllib.request.urlopen", side_effect=self.replies(self.FRENCH, {})):
            self.assertEqual(stations.find_places("Valence"),
                             [{"lat": 44.933, "lon": 4.892, "label": "Valence 26000"}])

    def test_place_outside_france(self):
        world = {"results": [{"latitude": 42.54, "longitude": 1.73, "name": "Pas de la Case", "country": "Andorre"}]}
        with mock.patch("urllib.request.urlopen", side_effect=self.replies({}, world)):
            self.assertEqual(stations.find_places("Pas de la Case"),
                             [{"lat": 42.54, "lon": 1.73, "label": "Pas de la Case, Andorre"}])

    def test_french_result_comes_first_and_is_not_doubled(self):
        world = {"results": [
            {"latitude": 44.93, "longitude": 4.89, "name": "Valence", "country": "France"},
            {"latitude": 50.0, "longitude": 3.0, "name": "Valence bis", "country": "France"},
        ]}
        with mock.patch("urllib.request.urlopen", side_effect=self.replies(self.FRENCH, world)):
            labels = [place["label"] for place in stations.find_places("Valence")]
        self.assertEqual(labels, ["Valence 26000", "Valence bis, France"])

    def test_world_service_down_keeps_french_results(self):
        replies = [*self.replies(self.FRENCH), OSError("down")]
        with mock.patch("urllib.request.urlopen", side_effect=replies):
            self.assertEqual(len(stations.find_places("Valence")), 1)


class DashboardCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.config_path = root / "config.json"
        for patcher in (mock.patch.object(app, "DATA", root), mock.patch.object(app, "CONFIG", self.config_path)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.config = {
            "client_id": "id",
            "client_secret": SECRET,
            "stations": [
                {"id": "70:ee:50:12:34:56", "name": "Valence", "lat": 44.93, "lon": 4.89},
                {"id": "", "name": "Boulot", "lat": None, "lon": None},
            ],
        }
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        self.dashboard = app.Dashboard(self.config, local=True)

    def saved(self):
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def mine(self):
        return self.dashboard.user_stations(app.LOCAL_USER["id"])

    def payload(self, *args, **kwargs):
        return self.dashboard.payload(app.LOCAL_USER, *args, **kwargs)

    def add(self, link=LINK, name="Boulot", place="Valence"):
        with mock.patch.object(stations, "find_places", return_value=HOMONYMS):
            return self.dashboard.add_station(app.LOCAL_USER["id"], link, name, place)


class EditStations(DashboardCase):
    def test_add_is_saved_and_config_is_left_alone(self):
        before = self.config_path.read_text(encoding="utf-8")
        self.add()
        self.assertEqual([s["name"] for s in self.mine()], ["Valence", "Boulot"])
        self.assertEqual(self.mine()[1]["id"], "70:ee:50:ab:cd:ef")
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), before)

    def test_stations_of_version_1_are_imported_once_with_their_colours(self):
        self.config["stations"] = [
            {"id": "70:ee:50:00:00:01", "name": "Un", "lat": 43.4, "lon": 1.7, "slot": 3},
            {"id": "70:ee:50:00:00:02", "name": "Deux", "lat": 43.5, "lon": 1.6, "slot": 3},
        ]
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        for name in ("meteo.db", "meteo.db-wal", "meteo.db-shm"):
            (app.DATA / name).unlink(missing_ok=True)
        dashboard = app.Dashboard(self.config, local=True)
        mine = dashboard.user_stations(app.LOCAL_USER["id"])
        self.assertEqual([(s["name"], s["slot"]) for s in mine], [("Un", 3), ("Deux", 0)])
        dashboard.remove_station(app.LOCAL_USER["id"], "70:ee:50:00:00:01")
        restarted = app.Dashboard(self.config, local=True)  # a removed station does not come back
        self.assertEqual([s["name"] for s in restarted.user_stations(app.LOCAL_USER["id"])], ["Deux"])

    def test_order_is_saved_and_keeps_the_colours(self):
        self.add()
        colours = {s["id"]: s["slot"] for s in self.mine()}
        reversed_ids = [s["id"] for s in reversed(self.mine())]
        self.dashboard.reorder_stations(app.LOCAL_USER["id"], reversed_ids)
        self.assertEqual([s["id"] for s in self.mine()], reversed_ids)
        self.assertEqual({s["id"]: s["slot"] for s in self.mine()}, colours)

    def test_order_needs_exactly_my_stations(self):
        self.add()
        for ids in ([], ["70:ee:50:00:00:09", "x"], [self.mine()[0]["id"]]):
            with self.assertRaises(app.StationError):
                self.dashboard.reorder_stations(app.LOCAL_USER["id"], ids)

    def test_config_saved_by_notepad_with_a_bom(self):
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8-sig")
        self.assertEqual(app.load_config()["client_secret"], SECRET)

    def test_station_survives_a_restart(self):
        self.add()
        restarted = app.Dashboard(self.saved(), local=True)
        self.assertEqual(len(restarted.user_stations(app.LOCAL_USER["id"])), 2)

    def test_empty_placeholder_is_dropped(self):
        self.assertEqual(len(self.mine()), 1)

    def test_name_defaults_to_place(self):
        self.assertEqual(self.add(name=" ")["name"], "Valence 82400")

    def test_duplicate(self):
        self.add()
        with self.assertRaisesRegex(app.StationError, "déjà"):
            self.add()

    def test_bad_link(self):
        with self.assertRaisesRegex(app.StationError, "non reconnu"):
            self.add(link="https://example.com")

    def test_connected_add_finds_the_right_homonym(self):
        self.dashboard.tokens.data = {"refresh_token": "r"}
        reading = {"ts": 100, "modules": {"base": "b"}, **dict.fromkeys(netatmo.FIELDS)}
        replies = [netatmo.StationNotFound("no"), reading]
        with mock.patch.object(netatmo, "fetch_station", side_effect=replies):
            station = self.add()
        self.assertEqual(station["place"], "Valence 26000")
        with self.dashboard.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM measures").fetchone()[0], 1)

    def test_connected_add_refused_when_station_is_elsewhere(self):
        self.dashboard.tokens.data = {"refresh_token": "r"}
        with mock.patch.object(netatmo, "fetch_station", side_effect=netatmo.StationNotFound("no")) as fetch, \
                mock.patch.object(app.time, "sleep"):
            with self.assertRaisesRegex(app.StationError, "introuvable"):
                self.add()
        self.assertEqual(fetch.call_count, app.MAX_SEARCH_AREAS)
        self.assertEqual(len(self.mine()), 1)  # nothing added

    def test_station_in_a_neighbouring_area_is_found_at_its_real_position(self):
        self.dashboard.tokens.data = {"refresh_token": "r"}
        reading = {"ts": 100, "modules": {"base": "b"}, "location": [1.9, 43.6], **dict.fromkeys(netatmo.FIELDS)}
        misses = [netatmo.StationNotFound("no")] * 4
        with mock.patch.object(netatmo, "fetch_station", side_effect=[*misses, reading]) as fetch, \
                mock.patch.object(app.time, "sleep"):
            self.add()
        self.assertEqual(fetch.call_args[0][1]["id"], "70:ee:50:ab:cd:ef")
        station = next(s for s in self.dashboard.stations if s["id"] == "70:ee:50:ab:cd:ef")
        self.assertEqual((station["lat"], station["lon"], station["box"]), (43.6, 1.9, netatmo.EXACT_BOX_DEG))

    def test_search_goes_from_the_places_outwards(self):
        areas = app.search_areas(HOMONYMS)
        self.assertEqual(areas[:2], HOMONYMS)
        self.assertEqual(len(areas), app.MAX_SEARCH_AREAS)
        one_place = app.search_areas(HOMONYMS[:1])
        self.assertEqual(len({(round(a["lat"], 2), round(a["lon"], 2)) for a in one_place}), app.MAX_SEARCH_AREAS)
        first_ring = max(abs(a["lat"] - HOMONYMS[0]["lat"]) for a in one_place[:9])
        self.assertAlmostEqual(first_ring, app.SEARCH_STEP)

    def test_colours_stay_with_their_station(self):
        self.add()
        self.add(link="70:ee:50:00:00:01", name="Trois")
        slots = {s["name"]: s["slot"] for s in self.mine()}
        self.dashboard.remove_station(app.LOCAL_USER["id"], "70:ee:50:ab:cd:ef")
        self.assertEqual({s["name"]: s["slot"] for s in self.mine()}["Trois"], slots["Trois"])
        self.add(name="Retour")
        self.assertEqual({s["name"]: s["slot"] for s in self.mine()}["Retour"], slots["Boulot"])


class Demo(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patcher = mock.patch.object(app, "DATA", Path(folder.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dashboard = app.Dashboard({}, demo=True)

    def test_two_simulated_stations_with_their_history(self):
        data = self.dashboard.payload(app.LOCAL_USER, 24 * 30)
        self.assertTrue(data["demo"])
        self.assertEqual(data["mode"], "local")
        self.assertEqual([s["name"] for s in data["stations"]], ["Valence", "Boulot"])
        self.assertTrue(all(s["series"]["ts"] and s["forecast"] and s["url"] is None for s in data["stations"]))
        self.assertEqual(len(data["stations"][0]["alerts"]), 2)

    def test_poll_calls_no_service(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            self.dashboard.poll()
        urlopen.assert_not_called()

    def test_stations_cannot_be_changed(self):
        with self.assertRaisesRegex(app.StationError, "démonstration"):
            self.dashboard.remove_station(app.LOCAL_USER["id"], "demo:1")


class Password(unittest.TestCase):
    def header(self, text):
        return "Basic " + base64.b64encode(text.encode()).decode()

    def test_right_password_with_any_user(self):
        self.assertTrue(app.is_password(self.header("moi:secret"), "secret"))
        self.assertTrue(app.is_password(self.header(":secret"), "secret"))

    def test_accents_and_colons(self):
        self.assertTrue(app.is_password(self.header("moi:été:2026"), "été:2026"))

    def test_refused(self):
        for header in ("", "Basic", "Basic !!!", "Bearer abc", self.header("moi:faux"), self.header("secret")):
            self.assertFalse(app.is_password(header, "secret"), header)


class LocalAddress(DashboardCase):
    """Without a password, only this computer's own names are answered (DNS rebinding)."""

    def setUp(self):
        super().setUp()
        self.start(app.make_handler(self.dashboard, port=8765))

    def start(self, handler):
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path, host="localhost:8765", method="GET", headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            connection.request(method, path, body=body, headers={"Host": host, **(headers or {})})
            response = connection.getresponse()
            return response.status, response.getheader("Location"), response.read()
        finally:
            connection.close()

    def status(self, host):
        return self.request("/api/data", host)[0]

    def test_own_names_are_answered(self):
        for host in ("localhost:8765", "127.0.0.1:8765", "[::1]:8765", "LOCALHOST"):
            self.assertEqual(self.status(host), 200, host)

    def test_other_names_are_refused(self):
        for host in ("evil.example:8765", "localhost.evil.example", "", "192.168.1.20:8765"):
            self.assertEqual(self.status(host), 403, host)

    def test_no_account_and_no_administration(self):
        status, location, _ = self.request("/login")
        self.assertEqual((status, location), (302, "/"))
        for path in ("/admin", "/admin.js", "/api/admin/overview", "/api/admin/backup", "/api/invite", "/entrer"):
            self.assertEqual(self.request(path)[0], 404, path)
        form = {"Content-Type": "application/json"}
        for path in ("/api/login", "/api/register", "/api/password", "/api/portail/verifier"):
            self.assertEqual(self.request(path, method="POST", headers=form, body=b"{}")[0], 404, path)

    def test_page_data_and_choices_without_logging_in(self):
        status, _, body = self.request("/api/data")
        data = json.loads(body)
        self.assertEqual((status, data["mode"], data["is_admin"]), (200, "local", False))
        self.assertEqual([s["name"] for s in data["stations"]], ["Valence"])
        settings = json.dumps({"settings": {"meteo-theme": "sombre"}}).encode()
        headers = {"Content-Type": "application/json"}
        self.assertEqual(self.request("/api/settings", method="PATCH", headers=headers, body=settings)[0], 200)
        self.assertIn(b'"meteo-theme": "sombre"', self.request("/settings.js")[2])

    def test_pages_are_marked_as_local(self):
        self.assertIn(b'<html class="mode-local"', self.request("/")[2])
        self.assertIn(b'<html class="mode-local"', self.request("/guide")[2])

    def test_password_protects_everything(self):
        self.start(app.make_handler(self.dashboard, password="secret", port=8765))
        self.assertEqual(self.request("/api/data", host="192.168.1.20:8765")[0], 401)
        good = {"Authorization": "Basic " + base64.b64encode(b"moi:secret").decode()}
        self.assertEqual(self.request("/api/data", host="192.168.1.20:8765", headers=good)[0], 200)

    def test_netatmo_connection_starts_from_the_page(self):
        status, location, _ = self.request("/connecter")
        self.assertEqual(status, 302)
        self.assertTrue(location.startswith(f"{netatmo.API}/oauth2/authorize?"))
        self.assertIn("redirect_uri=http%3A%2F%2Flocalhost%3A8765%2Fcallback", location)
        status, location, _ = self.request("/callback?state=faux&code=x")
        self.assertEqual((status, location), (302, "/"))
        self.assertIn("state incorrect", self.dashboard.status["error"])


class Window(DashboardCase):
    def setUp(self):
        super().setUp()
        self.now = int(time.time())
        with self.dashboard.connect() as db:
            for age in range(0, 20 * 86400, 600):
                reading = {**dict.fromkeys(netatmo.FIELDS), "ts": self.now - age, "temperature": age}
                self.dashboard.insert(db, "70:ee:50:12:34:56", reading)

    def test_read_window(self):
        self.assertEqual(app.read_window({"from": ["10"], "to": ["20"]}), (10, 20))
        for query in ({}, {"from": ["10"]}, {"from": ["x"], "to": ["20"]}, {"from": ["20"], "to": ["10"]}):
            self.assertIsNone(app.read_window(query), query)

    def test_window_limits_points_and_extremes(self):
        start, end = self.now - 10 * 86400, self.now - 9 * 86400
        data = self.payload(24, (start, end))
        station = data["stations"][0]
        self.assertEqual((data["since"], data["until"]), (start, end))
        self.assertTrue(all(start <= ts <= end for ts in station["series"]["ts"]))
        self.assertEqual(station["temperature_min"], 9 * 86400)
        self.assertEqual(station["temperature_max"], 10 * 86400)

    def test_zooming_in_shows_every_measure(self):
        wide = self.payload(0)
        narrow = self.payload(24, (self.now - 86400, self.now))
        self.assertGreater(wide["bucket"], 1)
        self.assertLessEqual(narrow["bucket"], 58)
        self.assertEqual(len(narrow["stations"][0]["series"]["ts"]), 145)

    def test_barometer_uses_the_last_three_hours(self):
        with self.dashboard.connect() as db:
            db.execute("UPDATE measures SET pressure = 1000 + ? - ts * 0.0002", (self.now * 0.0002,))
        result = self.payload(24)["stations"][0]["barometer"]
        self.assertEqual(result["level"], "fall")
        self.assertAlmostEqual(result["change"], -2.2, delta=0.1)  # 0.0002 hPa per second over 3 hours

    def test_no_barometer_without_pressure(self):
        self.assertIsNone(self.payload(24)["stations"][0]["barometer"])

    def test_window_cannot_end_in_the_future(self):
        data = self.payload(24, (self.now - 3600, self.now + 86400))
        self.assertLessEqual(data["until"], int(time.time()))

    def test_daily_mode_gives_one_point_per_calendar_day_with_extremes(self):
        data = self.payload(24, (self.now - 10 * 86400, self.now), daily=True)
        series = data["stations"][0]["series"]
        self.assertTrue(data["daily"])
        days = {datetime.date.fromtimestamp(ts) for ts in series["ts"]}
        self.assertEqual(len(days), len(series["ts"]))
        self.assertIn(len(series["ts"]), (10, 11, 12))
        for low, mean, high in zip(series["temperature_min"], series["temperature"], series["temperature_max"]):
            self.assertLessEqual(low, mean)
            self.assertLessEqual(mean, high)
        self.assertGreater(series["temperature_max"][0], series["temperature_min"][0])

    def test_daily_points_stay_inside_the_period(self):
        start, end = self.now - 8 * 86400 - 3600, self.now - 3600
        series = self.payload(24, (start, end), daily=True)["stations"][0]["series"]
        self.assertTrue(all(start <= ts <= end for ts in series["ts"]))

    def test_short_periods_stay_detailed(self):
        data = self.payload(24, (self.now - 3 * 86400, self.now), daily=True)
        self.assertFalse(data["daily"])


class EstimatedWind(DashboardCase):
    MODULES = {"base": "b", "outdoor": "o"}
    VALUES = {"wind_strength": 7.2, "wind_angle": 3, "gust_strength": 13.0}

    def setUp(self):
        super().setUp()
        self.station = self.dashboard.stations[0]
        self.station_id = self.station["id"]
        self.dashboard.modules[self.station_id] = self.MODULES
        self.now = int(time.time())

    def collect(self, archive=None, recent=None):
        with mock.patch.object(wind_estimate, "fetch_archive", return_value=archive or {}) as self.archive, \
                mock.patch.object(wind_estimate, "fetch_recent", return_value=recent or {}) as self.recent:
            self.dashboard.collect_wind()

    def stored(self):
        with self.dashboard.connect() as db:
            return [row[0] for row in db.execute("SELECT ts FROM wind_estimates ORDER BY ts")]

    def test_first_fetch_covers_the_history_and_the_recent_days(self):
        old = self.now - 400 * 86400
        recent = self.now - 3600
        self.collect({old: self.VALUES}, {recent: self.VALUES})
        self.assertEqual(self.stored(), [old, recent])
        self.assertEqual(self.recent.call_args[0][2], app.WIND_RECENT_DAYS)

    def test_later_fetches_only_read_the_recent_days(self):
        target = self.now - app.DEFAULT_HISTORY_DAYS * 86400
        self.collect({target: self.VALUES})
        self.dashboard.wind_wait.clear()
        self.collect()
        self.archive.assert_not_called()
        self.assertEqual(self.recent.call_args[0][2], app.WIND_RECHECK_DAYS)

    def test_future_hours_are_not_kept(self):
        self.collect({}, {self.now + 3600: self.VALUES})
        self.assertEqual(self.stored(), [])

    def test_a_real_anemometer_needs_no_estimate(self):
        self.dashboard.modules[self.station_id] = {**self.MODULES, "wind": "w"}
        self.collect()
        self.recent.assert_not_called()

    def test_asked_once_an_hour(self):
        self.collect()
        self.collect()
        self.recent.assert_not_called()

    def test_failure_is_reported_and_stores_nothing(self):
        with mock.patch.object(wind_estimate, "fetch_archive", side_effect=OSError("down")):
            self.dashboard.collect_wind()
        self.assertIn("down", self.dashboard.wind_errors[self.station_id])

    def test_estimates_stay_apart_from_measures(self):
        self.collect({}, {self.now - 3600: self.VALUES})
        station = self.payload(24)["stations"][0]
        self.assertEqual(station["series"]["ts"], [])
        self.assertEqual(station["wind_estimate"]["wind_strength"], [7.2])
        self.assertEqual(station["wind_latest"]["gust_strength"], 13.0)


class AlertsAndNeighbours(DashboardCase):
    WARNING = {"area": "Haute-Garonne", "level": "orange", "hazard": "vent", "icon": "💨", "start": 1, "end": 9}

    def setUp(self):
        super().setUp()
        self.station = self.dashboard.stations[0]
        self.station_id = self.station["id"]

    def collect(self):
        with mock.patch.object(alerts, "fetch_feed", return_value=[self.WARNING]) as self.feed, \
                mock.patch.object(alerts, "find_department", return_value="Haute-Garonne") as self.department:
            self.dashboard.collect_alerts()

    def test_warnings_reach_the_page(self):
        self.collect()
        self.assertEqual(self.payload(24)["stations"][0]["alerts"][0]["hazard"], "vent")

    def test_no_warning_means_an_empty_list(self):
        self.assertEqual(self.payload(24)["stations"][0]["alerts"], [])

    def test_department_is_looked_up_once(self):
        self.collect()
        self.dashboard.alert_wait = 0
        self.collect()
        self.department.assert_not_called()

    def test_not_asked_too_often(self):
        self.collect()
        self.collect()
        self.feed.assert_not_called()

    def test_failure_keeps_the_last_warnings_and_is_reported(self):
        self.collect()
        self.dashboard.alert_wait = 0
        with mock.patch.object(alerts, "fetch_feed", side_effect=OSError("down")):
            self.dashboard.collect_alerts()
        self.assertEqual(len(self.dashboard.alerts[self.station_id]), 1)
        self.assertIn("down", self.dashboard.status["alerts_error"])

    def test_station_abroad_has_no_warning_and_no_error(self):
        with mock.patch.object(alerts, "fetch_feed", return_value=[self.WARNING]), \
                mock.patch.object(alerts, "find_department", return_value=None):
            self.dashboard.collect_alerts()
        self.assertEqual(self.dashboard.alerts[self.station_id], [])
        self.assertIsNone(self.dashboard.status["alerts_error"])

    def test_option_off_makes_no_netatmo_call(self):
        self.payload(24)
        with mock.patch.object(neighbours, "fetch_around") as fetch:
            self.dashboard.collect_neighbours()
        fetch.assert_not_called()
        self.assertIsNone(self.payload(24)["stations"][0]["neighbours"])

    def test_option_on_asks_for_neighbours_then_gives_medians(self):
        first = self.payload(24, radius=5)["stations"][0]["neighbours"]
        self.assertEqual(first, {"radius": 5, "pending": True})
        self.assertTrue(self.dashboard.wake.is_set())
        now = int(time.time())
        readings = [
            {"id": f"n{i}", "lat": self.station["lat"], "lon": self.station["lon"], "ts": now,
             "temperature": 10 + i, "humidity": 80, "pressure": 1015}
            for i in range(5)
        ]
        with mock.patch.object(neighbours, "fetch_around", return_value=readings):
            self.dashboard.collect_neighbours()
        summary = self.payload(24, radius=5)["stations"][0]["neighbours"]
        self.assertEqual((summary["count"], summary["temperature"]), (5, 12))

    def test_failure_of_neighbours_does_not_touch_the_rest(self):
        self.payload(24, radius=5)
        with mock.patch.object(neighbours, "fetch_around", side_effect=netatmo.ApiError(503, "down")):
            self.dashboard.collect_neighbours()
        self.assertEqual(self.payload(24, radius=5)["stations"][0]["neighbours"]["pending"], True)


class Forecast(DashboardCase):
    ANSWER = {
        "current": {"weather_code": 2, "is_day": 1},
        "daily": {
            "time": ["2026-11-05", "2026-11-06"],
            "weather_code": [45, 61],
            "temperature_2m_max": [12.3, 14.7],
            "temperature_2m_min": [3.3, 3.1],
            "precipitation_sum": [0.0, 1.4],
            "precipitation_probability_max": [0, 18],
            "wind_speed_10m_max": [6.9, 9.2],
            "wind_gusts_10m_max": [12.2, 15.1],
            "sunrise": ["2026-11-05T07:46", "2026-11-06T07:47"],
            "sunset": ["2026-11-05T17:36", "2026-11-06T17:35"],
            "daylight_duration": [35400.5, 35280.0],
        },
        "hourly": {
            "time": [], "weather_code": [], "temperature_2m": [], "is_day": [],
            "precipitation_probability": [], "wind_speed_10m": [],
        },
    }
    READING = {"code": 2, "is_day": True, "days": []}

    def test_answer_is_turned_into_days(self):
        reply = io.BytesIO(json.dumps(self.ANSWER).encode())
        with mock.patch("urllib.request.urlopen", return_value=reply):
            result = forecast.fetch_forecast(44.93, 4.89)
        self.assertEqual((result["code"], result["is_day"]), (2, True))
        self.assertEqual(result["days"][1], {
            "date": "2026-11-06", "code": 61, "max": 14.7, "min": 3.1,
            "rain": 1.4, "rain_chance": 18, "wind": 9.2, "gust": 15.1,
            "sunrise": "2026-11-06T07:47", "sunset": "2026-11-06T17:35", "daylight": 35280.0,
        })

    def test_shown_in_the_page_data(self):
        with mock.patch.object(forecast, "fetch_forecast", return_value=self.READING):
            self.dashboard.collect_forecast()
        self.assertEqual(self.payload(24)["stations"][0]["forecast"]["code"], 2)

    def test_asked_once_an_hour(self):
        with mock.patch.object(forecast, "fetch_forecast", return_value=self.READING) as fetch:
            self.dashboard.collect_forecast()
            self.dashboard.collect_forecast()
        self.assertEqual(fetch.call_count, 1)

    def test_failure_keeps_the_last_forecast(self):
        with mock.patch.object(forecast, "fetch_forecast", return_value=self.READING):
            self.dashboard.collect_forecast()
        self.dashboard.forecast_wait.clear()
        with mock.patch.object(forecast, "fetch_forecast", side_effect=OSError("down")):
            self.dashboard.collect_forecast()
        station_id = self.dashboard.stations[0]["id"]
        self.assertEqual(self.dashboard.forecasts[station_id]["code"], 2)
        self.assertIn("down", self.dashboard.status["forecast"][station_id])

    def test_works_before_netatmo_is_connected(self):
        self.assertEqual(self.dashboard.setup, "auth")
        with mock.patch.object(forecast, "fetch_forecast", return_value=self.READING) as fetch:
            self.dashboard.poll()
        fetch.assert_called_once()


class Rename(DashboardCase):
    STATION = "70:ee:50:12:34:56"

    def test_new_name_is_saved_and_keeps_the_rest(self):
        self.dashboard.rename_station(app.LOCAL_USER["id"], self.STATION, "  Maison ")
        self.assertEqual([(s["id"], s["name"]) for s in self.mine()], [(self.STATION, "Maison")])

    def test_refused(self):
        for station_id, name in ((self.STATION, "   "), (self.STATION, "x" * 41), ("70:ee:50:00:00:09", "Maison")):
            with self.assertRaises(app.StationError):
                self.dashboard.rename_station(app.LOCAL_USER["id"], station_id, name)


class Backfill(DashboardCase):
    STEP = netatmo.HISTORY_STEP
    MODULES = {"base": "b", "outdoor": "o", "rain": "r"}

    def setUp(self):
        super().setUp()
        self.station = self.dashboard.stations[0]
        self.dashboard.tokens.data = {"refresh_token": "r"}

    def rows(self):
        with self.dashboard.connect() as db:
            return db.execute("SELECT * FROM measures ORDER BY ts").fetchall()

    def history(self, kind):
        return {
            "base": {3600: {"pressure": 1015}, 5400: {"pressure": 1016}},
            "outdoor": {3600: {"temperature": 8, "humidity": 80}, 5400: {"temperature": 9, "humidity": 81}},
            "rain": {3600: {"rain_30min": 0.2}, 5400: {"rain_30min": 0.3}},
        }[kind]

    def run_backfill(self, side_effect=None):
        fake = side_effect or (lambda tokens, sid, kind, module, begin, end: self.history(kind))
        with mock.patch.object(netatmo, "fetch_history", side_effect=fake) as fetch, \
                mock.patch.object(netatmo, "fetch_daily_rain", return_value={"2026-10-25": 0.8}) as self.rain:
            self.dashboard.backfill(self.station, self.MODULES)
        return fetch

    def test_sensors_are_merged_and_rain_covers_one_hour(self):
        self.run_backfill()
        first, second = self.rows()
        self.assertEqual((first["temperature"], first["pressure"], first["rain_60min"]), (8, 1015, 0.2))
        self.assertEqual(second["rain_60min"], 0.5)

    def test_live_readings_are_not_doubled(self):
        with self.dashboard.connect() as db:
            live = {**dict.fromkeys(netatmo.FIELDS), "ts": 5400 + 300, "temperature": 9.5}
            self.dashboard.insert(db, self.station["id"], live)
        self.run_backfill()
        self.assertEqual([row["ts"] for row in self.rows()], [3600, 5700])

    def test_first_run_uses_history_days_then_only_the_gap(self):
        fetch = self.run_backfill()
        begin, end = fetch.call_args[0][4:6]
        self.assertEqual(end - begin, app.DEFAULT_HISTORY_DAYS * 86400)
        self.dashboard.history_wait.clear()
        fetch = self.run_backfill()
        begin, end = fetch.call_args[0][4:6]
        self.assertAlmostEqual(end - begin, app.HISTORY_EVERY, delta=5)

    def test_not_asked_again_within_the_hour(self):
        self.run_backfill()
        self.assertEqual(self.run_backfill().call_count, 0)

    def test_a_station_with_one_year_gets_the_older_year_once(self):
        now = int(time.time())
        oldest = now - 365 * 86400
        with self.dashboard.connect() as db:
            self.dashboard.insert(db, self.station["id"], {**dict.fromkeys(netatmo.FIELDS), "ts": oldest})
            db.execute("INSERT INTO history_progress VALUES (?, ?)", (self.station["id"], now - 600))
        windows = {call[0][4:6] for call in self.run_backfill().call_args_list}
        self.assertEqual(len(windows), 2)
        older = min(windows)
        self.assertEqual(older[1], oldest)
        self.assertAlmostEqual(older[0], now - app.DEFAULT_HISTORY_DAYS * 86400, delta=5)
        self.assertAlmostEqual(self.rain.call_args[0][3], older[0], delta=5)  # all the rain totals too
        self.dashboard.history_wait.clear()
        self.assertEqual(len({call[0][4:6] for call in self.run_backfill().call_args_list}), 1)
        self.assertAlmostEqual(self.rain.call_args[0][3], now - app.RAIN_RECHECK, delta=5)

    def test_rain_totals_are_stored_and_returned(self):
        self.run_backfill()
        window = (int(datetime.datetime(2026, 10, 20).timestamp()), int(datetime.datetime(2026, 10, 30).timestamp()))
        self.assertEqual(self.payload(24, window)["stations"][0]["rain_days"], [["2026-10-25", 0.8]])

    SECOND = "70:ee:50:ff:00:02"  # collected after the first one, whose id comes first

    def two_stations(self):
        other = {"id": self.SECOND, "name": "Deux", "lat": 43.5, "lon": 1.5}
        self.dashboard.import_config_stations(app.LOCAL_USER["id"], [other])

    def poll_and_trace(self):
        calls = []
        reading = {**dict.fromkeys(netatmo.FIELDS), "ts": 7000, "modules": self.MODULES}

        def live(tokens, station):
            calls.append(("live", station["id"]))
            return reading

        def history(tokens, station_id, kind, *rest):
            calls.append(("history", station_id))
            return {}

        with mock.patch.object(netatmo, "fetch_station", side_effect=live), \
                mock.patch.object(netatmo, "fetch_history", side_effect=history), \
                mock.patch.object(netatmo, "fetch_daily_rain", return_value={}), \
                mock.patch.object(self.dashboard, "collect_wind"), \
                mock.patch.object(self.dashboard, "collect_forecast"), mock.patch.object(self.dashboard, "collect_alerts"), \
                mock.patch.object(app.time, "sleep") as sleep:
            self.dashboard.poll()
        return calls, sleep

    def test_all_live_readings_come_before_history(self):
        self.two_stations()
        calls, _ = self.poll_and_trace()
        self.assertEqual([kind for kind, _ in calls[:2]], ["live", "live"])
        self.assertEqual({kind for kind, _ in calls[2:]}, {"history"})

    def test_live_readings_are_spaced_out(self):
        self.two_stations()
        _, sleep = self.poll_and_trace()
        sleep.assert_called_once_with(app.STATION_GAP)

    def test_one_station_gets_its_history_per_poll(self):
        self.two_stations()
        first, _ = self.poll_and_trace()
        second, _ = self.poll_and_trace()
        ids = [{sid for kind, sid in calls if kind == "history"} for calls in (first, second)]
        self.assertEqual(ids, [{self.station["id"]}, {self.SECOND}])

    def test_exact_position_is_saved_once(self):
        reading = {**dict.fromkeys(netatmo.FIELDS), "ts": 7000, "modules": self.MODULES, "location": [1.49, 43.53]}
        with mock.patch.object(netatmo, "fetch_station", return_value=reading):
            self.dashboard.collect_live(self.station)
        saved = self.dashboard.stations[0]
        self.assertEqual((saved["lat"], saved["lon"], saved["box"]), (43.53, 1.49, netatmo.EXACT_BOX_DEG))
        self.dashboard.refine_position(saved, [9, 9])
        self.assertEqual(self.dashboard.stations[0]["lat"], 43.53)

    def test_outage_is_retried_at_the_next_poll(self):
        reading = {**dict.fromkeys(netatmo.FIELDS), "ts": 7000, "modules": self.MODULES}
        outage = netatmo.ApiError(503, "unavailable")
        with mock.patch.object(netatmo, "fetch_station", return_value=reading), \
                mock.patch.object(netatmo, "fetch_history", side_effect=outage), \
                mock.patch.object(self.dashboard, "collect_forecast"), mock.patch.object(self.dashboard, "collect_alerts"), \
                mock.patch.object(self.dashboard, "collect_wind"):
            self.dashboard.poll()
        self.assertNotIn(self.station["id"], self.dashboard.history_wait)

    def test_failure_stores_nothing_and_keeps_live_collection(self):
        reading = {**dict.fromkeys(netatmo.FIELDS), "ts": 7000, "temperature": 7, "modules": self.MODULES}

        def fail(*args):
            raise netatmo.ApiError(403, "refused", code=9)

        with mock.patch.object(netatmo, "fetch_station", return_value=reading), \
                mock.patch.object(self.dashboard, "collect_wind"), \
                mock.patch.object(self.dashboard, "collect_forecast"), mock.patch.object(self.dashboard, "collect_alerts"), \
                mock.patch.object(netatmo, "fetch_history", side_effect=fail):
            self.dashboard.poll()
        self.assertEqual([row["ts"] for row in self.rows()], [7000])
        self.assertEqual(self.dashboard.status["stations"], {})
        self.assertIn("refused", self.dashboard.status["history"][self.station["id"]])
        with self.dashboard.connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM history_progress").fetchone())


if __name__ == "__main__":
    unittest.main()
