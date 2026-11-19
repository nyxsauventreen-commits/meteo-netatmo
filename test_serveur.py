import http.client
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import accounts
import app
import netatmo
import stations
import webpush

PASSWORD = "un-mot-de-passe-long"
INVITE = "code-invitation"
SECRET = "do-not-lose-me"
FIRST = "70:ee:50:12:34:56"
OTHER = "70:ee:50:ab:cd:ef"
THIRD = "70:ee:50:00:00:03"


def link(station_id):
    return f"https://weathermap.netatmo.com/?stationid={station_id}&zoom=13"


class ServerCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.config = {
            "client_id": "id",
            "client_secret": SECRET,
            "stations": [
                {"id": FIRST, "name": "Maison", "lat": 44.93, "lon": 4.89, "box": 0.01, "place": "Valence"},
                {"id": "", "name": "Vide", "lat": None, "lon": None},
            ],
        }
        (root / "config.json").write_text(json.dumps(self.config), encoding="utf-8")
        for patcher in (
            mock.patch.object(app, "DATA", root),
            mock.patch.object(app, "CONFIG", root / "config.json"),
            mock.patch.object(app, "AUTH_DELAY", 0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.dashboard = app.Dashboard(self.config)
        self.accounts = self.dashboard.accounts

    def user(self, name):
        user_id = self.dashboard.register(name, PASSWORD)
        return {"id": user_id, "name": name}

    def add(self, user, station_id=OTHER, name="", place="43.53, 1.49"):
        return self.dashboard.add_station(user["id"], link(station_id), name, place)


class Accounts(ServerCase):
    def test_register_then_login(self):
        user_id = self.accounts.register("Sam", PASSWORD)
        self.assertEqual(self.accounts.login("Sam", PASSWORD), user_id)
        self.assertEqual(self.accounts.login("sam", PASSWORD), user_id)

    def test_password_is_never_stored_as_is(self):
        self.accounts.register("Sam", PASSWORD)
        raw = (app.DATA / "meteo.db").read_bytes()
        self.assertNotIn(PASSWORD.encode(), raw)

    def test_wrong_password_and_unknown_name_say_the_same(self):
        self.accounts.register("Sam", PASSWORD)
        messages = []
        for name, password in (("Sam", "faux-mot-de-passe"), ("Personne", PASSWORD)):
            with self.assertRaises(accounts.AccountError) as caught:
                self.accounts.login(name, password)
            messages.append(str(caught.exception))
        self.assertEqual(messages[0], messages[1])

    def test_same_name_twice(self):
        self.accounts.register("Sam", PASSWORD)
        with self.assertRaisesRegex(accounts.AccountError, "déjà pris"):
            self.accounts.register("SAM", PASSWORD)

    def test_refused_names_and_passwords(self):
        for name, password in (("x", PASSWORD), ("<script>", PASSWORD), ("Sam", "court")):
            with self.assertRaises(accounts.AccountError):
                self.accounts.register(name, password)

    def test_locked_after_repeated_failures_even_with_the_right_password(self):
        self.accounts.register("Sam", PASSWORD)
        for _ in range(accounts.MAX_FAILURES):
            with self.assertRaises(accounts.AccountError):
                self.accounts.login("Sam", "faux-mot-de-passe")
        with self.assertRaisesRegex(accounts.AccountError, "Trop d'essais"):
            self.accounts.login("Sam", PASSWORD)

    def test_session_lifecycle(self):
        user_id = self.accounts.register("Sam", PASSWORD)
        token = self.accounts.create_session(user_id)
        self.assertEqual(self.accounts.session_user(token)["name"], "Sam")
        self.accounts.end_session(token)
        self.assertIsNone(self.accounts.session_user(token))

    def test_session_token_is_stored_hashed(self):
        token = self.accounts.create_session(self.accounts.register("Sam", PASSWORD))
        with self.dashboard.connect() as db:
            self.assertNotEqual(db.execute("SELECT token FROM sessions").fetchone()[0], token)

    def test_expired_session(self):
        user_id = self.accounts.register("Sam", PASSWORD)
        token = self.accounts.create_session(user_id)
        with self.dashboard.connect() as db:
            db.execute("UPDATE sessions SET expires = 1")
        self.assertIsNone(self.accounts.session_user(token))

    def test_password_reset_closes_sessions(self):
        user_id = self.accounts.register("Sam", PASSWORD)
        token = self.accounts.create_session(user_id)
        self.accounts.reset_password("Sam", "nouveau-mot-de-passe")
        self.assertIsNone(self.accounts.session_user(token))
        self.assertEqual(self.accounts.login("Sam", "nouveau-mot-de-passe"), user_id)


class SeveralUsers(ServerCase):
    def test_first_account_receives_the_stations_of_the_config(self):
        first = self.user("Sam")
        second = self.user("Alex")
        self.assertEqual([s["name"] for s in self.dashboard.user_stations(first["id"])], ["Maison"])
        self.assertEqual(self.dashboard.user_stations(second["id"]), [])

    def test_each_user_sees_only_their_stations(self):
        first, second = self.user("Sam"), self.user("Alex")
        self.add(second, OTHER)
        ids = {user["name"]: [s["id"] for s in self.dashboard.payload(user, 24)["stations"]] for user in (first, second)}
        self.assertEqual(ids, {"Sam": [FIRST], "Alex": [OTHER]})

    def test_a_shared_station_is_collected_once_and_needs_no_search(self):
        first, second = self.user("Sam"), self.user("Alex")
        with mock.patch.object(stations, "find_places") as search:
            self.add(second, FIRST, "Chez Sam")
        search.assert_not_called()
        self.assertEqual([s["id"] for s in self.dashboard.stations], [FIRST])
        names = [self.dashboard.user_stations(u["id"])[0]["name"] for u in (first, second)]
        self.assertEqual(names, ["Maison", "Chez Sam"])

    def test_renaming_only_changes_my_name(self):
        first, second = self.user("Sam"), self.user("Alex")
        self.add(second, FIRST)
        self.dashboard.rename_station(second["id"], FIRST, "Autre nom")
        self.assertEqual(self.dashboard.user_stations(first["id"])[0]["name"], "Maison")

    def test_cannot_rename_a_station_i_do_not_follow(self):
        self.user("Sam")
        second = self.user("Alex")
        with self.assertRaises(app.StationError):
            self.dashboard.rename_station(second["id"], FIRST, "Piratage")

    def test_removing_keeps_it_for_the_others(self):
        first, second = self.user("Sam"), self.user("Alex")
        self.add(second, FIRST)
        self.dashboard.remove_station(second["id"], FIRST)
        self.assertEqual(self.dashboard.user_stations(second["id"]), [])
        self.assertEqual(len(self.dashboard.user_stations(first["id"])), 1)
        self.assertEqual(len(self.dashboard.stations), 1)

    def test_collection_stops_with_the_last_follower(self):
        first = self.user("Sam")
        self.dashboard.remove_station(first["id"], FIRST)
        self.assertEqual(self.dashboard.stations, [])

    def test_colours_are_per_user(self):
        first, second = self.user("Sam"), self.user("Alex")
        self.add(first, OTHER)
        self.add(second, OTHER)
        slots = [self.dashboard.user_stations(u["id"])[-1]["slot"] for u in (first, second)]
        self.assertEqual(slots, [1, 0])

    def test_order_is_per_user_and_keeps_the_colours(self):
        first, second = self.user("Sam"), self.user("Alex")
        self.add(first, OTHER)
        self.add(second, OTHER)
        self.add(second, FIRST)
        colours = {s["id"]: s["slot"] for s in self.dashboard.user_stations(first["id"])}
        self.dashboard.reorder_stations(first["id"], [OTHER, FIRST])
        mine = self.dashboard.user_stations(first["id"])
        self.assertEqual([s["id"] for s in mine], [OTHER, FIRST])
        self.assertEqual({s["id"]: s["slot"] for s in mine}, colours)
        self.assertEqual([s["id"] for s in self.dashboard.user_stations(second["id"])], [OTHER, FIRST])

    def test_new_station_goes_last(self):
        first = self.user("Sam")
        self.add(first, OTHER)
        self.dashboard.reorder_stations(first["id"], [OTHER, FIRST])
        self.add(first, THIRD)
        self.assertEqual([s["id"] for s in self.dashboard.user_stations(first["id"])], [OTHER, FIRST, THIRD])

    def test_cannot_reorder_with_stations_i_do_not_follow(self):
        first = self.user("Sam")
        for ids in ([OTHER], [FIRST, OTHER], []):
            with self.assertRaises(app.StationError):
                self.dashboard.reorder_stations(first["id"], ids)

    def test_old_database_gets_the_order_column(self):
        with self.dashboard.connect() as db:
            db.execute("DROP TABLE user_stations")
            db.execute(
                "CREATE TABLE user_stations (user_id INTEGER NOT NULL, station_id TEXT NOT NULL, "
                "name TEXT NOT NULL, slot INTEGER NOT NULL, PRIMARY KEY (user_id, station_id)) WITHOUT ROWID"
            )
            db.execute("INSERT INTO stations (id, lat, lon) VALUES (?, 43, 1)", (FIRST,))
            db.execute("INSERT INTO user_stations VALUES (1, ?, 'Ancienne', 0)", (FIRST,))
        restarted = app.Dashboard(self.config)
        self.assertEqual(restarted.user_stations(1)[0]["name"], "Ancienne")

    def test_list_of_a_pc_database_is_not_collected_online(self):
        with self.dashboard.connect() as db:
            db.execute("INSERT INTO stations (id, lat, lon) VALUES (?, 43, 1)", (OTHER,))
            db.execute("INSERT INTO user_stations (user_id, station_id, name, slot) VALUES (0, ?, 'PC', 0)", (OTHER,))
        restarted = app.Dashboard(self.config)
        self.assertNotIn(OTHER, [station["id"] for station in restarted.stations])

    def test_limit_per_user(self):
        user = self.user("Sam")
        for index in range(app.MAX_STATIONS - 1):
            self.add(user, f"70:ee:50:00:01:{index:02x}")
        with self.assertRaisesRegex(app.StationError, "maximum"):
            self.add(user, THIRD)

    def test_limit_for_the_whole_server(self):
        user = self.user("Sam")
        with mock.patch.object(app, "MAX_TOTAL_STATIONS", 1):
            with self.assertRaisesRegex(app.StationError, "serveur"):
                self.add(user, OTHER)

    def test_status_shows_only_my_stations(self):
        first, second = self.user("Sam"), self.user("Alex")
        self.dashboard.status["stations"] = {FIRST: "panne"}
        self.dashboard.status["history_running"] = FIRST
        self.assertEqual(self.dashboard.payload(second, 24)["status"]["stations"], {})
        self.assertIsNone(self.dashboard.payload(second, 24)["status"]["history_running"])
        self.assertEqual(self.dashboard.payload(first, 24)["status"]["history_running"], "Maison")

    def test_new_user_without_stations_gets_a_valid_page(self):
        self.user("Sam")
        data = self.dashboard.payload(self.user("Alex"), 0, daily=True)
        self.assertEqual(data["stations"], [])

    def test_exact_position_is_stored_once_for_everyone(self):
        user = self.user("Sam")
        self.add(user, OTHER)
        station = next(s for s in self.dashboard.stations if s["id"] == OTHER)
        self.dashboard.refine_position(station, [1.49, 43.53])
        refined = next(s for s in self.dashboard.stations if s["id"] == OTHER)
        self.assertEqual((refined["lat"], refined["box"]), (43.53, netatmo.EXACT_BOX_DEG))

    def test_collection_covers_each_shared_station_once(self):
        first, second = self.user("Sam"), self.user("Alex")
        self.add(second, FIRST)
        self.dashboard.tokens.data = {"refresh_token": "r"}
        reading = {**dict.fromkeys(netatmo.FIELDS), "ts": int(time.time()), "modules": {"base": FIRST}}
        with mock.patch.object(netatmo, "fetch_station", return_value=reading) as fetch, \
                mock.patch.object(self.dashboard, "collect_forecast"), \
                mock.patch.object(self.dashboard, "collect_alerts"), \
                mock.patch.object(self.dashboard, "collect_neighbours"), \
                mock.patch.object(self.dashboard, "collect_history"), \
                mock.patch.object(self.dashboard, "collect_wind"):
            self.dashboard.poll()
        self.assertEqual(fetch.call_count, 1)


class Web(ServerCase):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(self.dashboard, INVITE))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method, path, body=None, cookie=None, content_type="application/json"):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        headers = {}
        data = None
        if body is not None:
            data = json.dumps(body)
            headers["Content-Type"] = content_type
        if cookie:
            headers["Cookie"] = cookie
        connection.request(method, path, data, headers)
        response = connection.getresponse()
        content = response.read()
        connection.close()
        return response, content

    def admin_code(self):
        admin = self.accounts.admin_id()
        return INVITE if admin is None else self.dashboard.invite_code(admin)

    def sign_up(self, name, code=None):
        form = {"name": name, "password": PASSWORD, "code": code or self.admin_code()}
        response, _ = self.call("POST", "/api/register", form)
        self.assertEqual(response.status, 200)
        return response.getheader("Set-Cookie").split(";")[0]

    def test_settings_stay_with_their_account(self):
        first, second = self.sign_up("Sam"), self.sign_up("Alex")
        form = {"settings": {"meteo-hours": "1", "meteo-theme": "sombre"}}
        self.assertEqual(self.call("PATCH", "/api/settings", form, first)[0].status, 200)
        self.assertIn(b'"meteo-hours": "1"', self.call("GET", "/settings.js", cookie=first)[1])
        self.assertIn(b"}(null));", self.call("GET", "/settings.js", cookie=second)[1])

    def test_settings_are_checked(self):
        cookie = self.sign_up("Sam")
        for settings in (None, {"autre": "1"}, {"meteo-hours": 1}, {"meteo-hours": "x" * 301}):
            response, _ = self.call("PATCH", "/api/settings", {"settings": settings}, cookie)
            self.assertEqual(response.status, 422)
        self.assertIn(b"}(null));", self.call("GET", "/settings.js", cookie=cookie)[1])

    def test_notifications_belong_to_their_account(self):
        first, second = self.sign_up("Sam"), self.sign_up("Alex")
        self.assertEqual(self.call("GET", "/api/notifications")[0].status, 401)
        self.assertEqual(self.call("POST", "/api/notifications/test", {})[0].status, 401)
        response, content = self.call("PATCH", "/api/notifications", {"events": ["frost"]}, first)
        self.assertEqual(response.status, 200)
        endpoint = {"endpoint": "https://fcm.googleapis.com/fcm/send/sam"}
        self.assertEqual(self.call("POST", "/api/push/subscribe", endpoint, first)[0].status, 200)
        mine = json.loads(self.call("GET", "/api/notifications", cookie=first)[1])
        other = json.loads(self.call("GET", "/api/notifications", cookie=second)[1])
        self.assertEqual((mine["events"], mine["devices"], other["devices"]), (["frost"], 1, 0))
        self.assertEqual(self.dashboard.devices(self.accounts.list_users()[0]["id"])[0]["origin"], "https://127.0.0.1:"
                         + str(self.server.server_port))
        self.assertEqual(len(webpush.unb64url(mine["push_key"])), 65)
        self.assertFalse(mine["email_available"])
        refused = self.call("PATCH", "/api/notifications", {"email": True, "address": "x@example.fr"}, first)[0]
        self.assertEqual(refused.status, 422)
        bad = {"endpoint": "https://evil.example/x"}
        self.assertEqual(self.call("POST", "/api/push/subscribe", bad, first)[0].status, 422)
        self.assertEqual(self.call("GET", "/api/push/pending")[0].status, 401)

    def test_only_the_administrator_sets_the_mailbox(self):
        admin, user = self.sign_up("Admin"), self.sign_up("Sam")
        form = {"user": "site@example.org", "password": "abcdefghijklmnop"}
        self.assertEqual(self.call("POST", "/api/mailbox", form, user)[0].status, 403)
        self.assertFalse(json.loads(self.call("GET", "/api/notifications", cookie=user)[1])["mailbox"]["may_change"])
        with mock.patch("smtplib.SMTP_SSL"):
            response, content = self.call("POST", "/api/mailbox", form, admin)
        self.assertEqual((response.status, json.loads(content)), (200, {"address": "site@example.org"}))
        self.assertTrue(json.loads(self.call("GET", "/api/notifications", cookie=user)[1])["email_available"])

    def test_notification_problems_stay_in_the_admin_log(self):
        cookie = self.sign_up("Sam")
        with self.dashboard.lock:
            self.dashboard.status["notify_error"] = "E-mail non envoyé : sam@example.fr"
        data = json.loads(self.call("GET", "/api/data", cookie=cookie)[1])
        self.assertNotIn("notify_error", data["status"])

    def test_guest_cannot_save_settings(self):
        self.assertEqual(self.call("PATCH", "/api/settings", {"settings": {}})[0].status, 401)
        self.assertIn(b"}(null));", self.call("GET", "/settings.js")[1])

    def test_deleted_account_loses_its_settings(self):
        self.sign_up("Admin")
        self.sign_up("Sam")
        user_id = self.accounts.list_users()[1]["id"]
        self.dashboard.save_settings(user_id, {"meteo-hours": "1"})
        self.dashboard.delete_user(user_id)
        self.assertIsNone(self.dashboard.user_settings(user_id))

    def test_neighbours_are_only_asked_for_with_a_radius(self):
        cookie = self.sign_up("Sam")
        plain = json.loads(self.call("GET", "/api/data", cookie=cookie)[1])
        self.assertIsNone(plain["stations"][0]["neighbours"])
        self.assertEqual(plain["stations"][0]["alerts"], [])
        asked = json.loads(self.call("GET", "/api/data?radius=50", cookie=cookie)[1])
        self.assertEqual(asked["stations"][0]["neighbours"], {"radius": 10, "pending": True})

    def test_stats_cover_only_the_stations_of_the_account(self):
        cookie = self.sign_up("Sam")
        other = self.sign_up("Alex")
        now = int(time.time())
        with self.dashboard.connect() as db:
            self.dashboard.insert(db, FIRST, {**dict.fromkeys(app.COLUMNS), "ts": now - 600, "temperature": 12.5})
        body = json.loads(self.call("GET", "/api/stats?hours=24", cookie=cookie)[1])
        self.assertEqual([station["id"] for station in body["stations"]], [FIRST])
        self.assertEqual(body["stations"][0]["period"]["hottest"]["value"], 12.5)
        self.assertEqual(json.loads(self.call("GET", "/api/stats", cookie=other)[1])["stations"], [])
        self.assertEqual(self.call("GET", "/api/stats")[0].status, 401)

    def test_guest_is_sent_to_the_login_page(self):
        response, _ = self.call("GET", "/")
        self.assertEqual((response.status, response.getheader("Location")), (302, "/login"))
        self.assertEqual(self.call("GET", "/login")[0].status, 200)

    def test_guest_gets_no_data_and_no_page_code(self):
        self.assertEqual(self.call("GET", "/api/data")[0].status, 401)
        self.assertEqual(self.call("GET", "/app.js")[0].status, 302)
        self.assertEqual(self.call("POST", "/api/stations", {"link": link(OTHER)})[0].status, 401)
        self.assertEqual(self.call("PATCH", "/api/stations", {"id": FIRST, "name": "x"})[0].status, 401)
        self.assertEqual(self.call("DELETE", f"/api/stations?id={FIRST}")[0].status, 401)

    def test_wrong_invitation_code(self):
        form = {"name": "Sam", "password": PASSWORD, "code": "mauvais-code"}
        self.assertEqual(self.call("POST", "/api/register", form)[0].status, 403)
        self.assertEqual(self.accounts.count(), 0)

    def test_session_cookie_is_protected(self):
        form = {"name": "Sam", "password": PASSWORD, "code": INVITE}
        header = self.call("POST", "/api/register", form)[0].getheader("Set-Cookie")
        for flag in ("HttpOnly", "SameSite=Lax", "Secure"):
            self.assertIn(flag, header)

    def test_two_people_keep_separate_lists(self):
        sam, alex = self.sign_up("Sam"), self.sign_up("Alex")
        form = {"link": link(OTHER), "place": "43.53, 1.49", "name": "Bureau"}
        self.assertEqual(self.call("POST", "/api/stations", form, alex)[0].status, 201)
        names = {}
        for label, cookie in (("sam", sam), ("alex", alex)):
            data = json.loads(self.call("GET", "/api/data", cookie=cookie)[1])
            names[label] = (data["user"], [s["name"] for s in data["stations"]])
        self.assertEqual(names, {"sam": ("Sam", ["Maison"]), "alex": ("Alex", ["Bureau"])})

    def test_deleting_another_users_station_changes_nothing_for_them(self):
        sam, alex = self.sign_up("Sam"), self.sign_up("Alex")
        self.call("DELETE", f"/api/stations?id={FIRST}", cookie=alex)
        data = json.loads(self.call("GET", "/api/data", cookie=sam)[1])
        self.assertEqual(len(data["stations"]), 1)

    def test_login_then_logout(self):
        self.sign_up("Sam")
        response, _ = self.call("POST", "/api/login", {"name": "Sam", "password": PASSWORD})
        cookie = response.getheader("Set-Cookie").split(";")[0]
        self.assertEqual(self.call("GET", "/", cookie=cookie)[0].status, 200)
        self.call("POST", "/api/logout", {}, cookie)
        self.assertEqual(self.call("GET", "/api/data", cookie=cookie)[0].status, 401)

    def test_wrong_password(self):
        self.sign_up("Sam")
        form = {"name": "Sam", "password": "faux-mot-de-passe"}
        self.assertEqual(self.call("POST", "/api/login", form)[0].status, 401)

    def test_forms_from_other_sites_are_refused(self):
        cookie = self.sign_up("Sam")
        response, _ = self.call("POST", "/api/stations", {"link": link(OTHER)}, cookie, "text/plain")
        self.assertEqual(response.status, 400)

    def test_forged_cookie(self):
        self.assertEqual(self.call("GET", "/api/data", cookie="meteo_session=faux")[0].status, 401)
        self.assertEqual(self.call("GET", "/api/data", cookie="bad;;=cookie")[0].status, 401)

    def test_order_over_the_web(self):
        cookie = self.sign_up("Sam")
        self.call("POST", "/api/stations", {"link": link(OTHER), "place": "43.53, 1.49"}, cookie)
        self.assertEqual(self.call("PATCH", "/api/stations/order", {"ids": [OTHER, FIRST]}, cookie)[0].status, 200)
        data = json.loads(self.call("GET", "/api/data", cookie=cookie)[1])
        self.assertEqual([s["id"] for s in data["stations"]], [OTHER, FIRST])
        self.assertEqual(self.call("PATCH", "/api/stations/order", {"ids": [OTHER]}, cookie)[0].status, 422)
        self.assertEqual(self.call("PATCH", "/api/stations/order", {"ids": 5}, cookie)[0].status, 422)
        self.assertEqual(self.call("PATCH", "/api/stations/order", {"ids": []})[0].status, 401)

    def test_guide_needs_an_account(self):
        self.assertEqual(self.call("GET", "/guide")[0].status, 302)
        response, body = self.call("GET", "/guide", cookie=self.sign_up("Sam"))
        self.assertEqual(response.status, 200)
        self.assertIn("Historique des versions", body.decode())

    def test_first_account_is_the_administrator(self):
        sam, alex = self.sign_up("Sam"), self.sign_up("Alex")
        flags = [json.loads(self.call("GET", "/api/data", cookie=c)[1])["is_admin"] for c in (sam, alex)]
        self.assertEqual(flags, [True, False])

    def register(self, name, code):
        form = {"name": name, "password": PASSWORD, "code": code}
        return self.call("POST", "/api/register", form)[0].status

    def my_code(self, cookie):
        return json.loads(self.call("GET", "/api/invite", cookie=cookie)[1])["code"]

    def test_installation_code_is_the_admins_first_code_and_works_once(self):
        sam = self.sign_up("Sam")
        self.assertEqual(self.my_code(sam), INVITE)
        self.assertEqual(self.register("Alex", INVITE), 200)
        self.assertEqual(self.register("Lou", INVITE), 403)
        self.assertNotEqual(self.my_code(sam), INVITE)

    def test_everyone_has_a_code_that_changes_after_use(self):
        self.sign_up("Sam")
        alex = self.sign_up("Alex")
        code = self.my_code(alex)
        self.assertEqual(len(code), 12)
        self.assertEqual(self.register("Lou", code), 200)
        self.assertEqual(self.register("Max", code), 403)
        self.assertNotEqual(self.my_code(alex), code)

    def test_renewed_code_replaces_the_old_one(self):
        self.sign_up("Sam")
        alex = self.sign_up("Alex")
        old = self.my_code(alex)
        new = json.loads(self.call("POST", "/api/invite", {}, alex)[1])["code"]
        self.assertNotEqual(old, new)
        self.assertEqual(self.register("Lou", old), 403)
        self.assertEqual(self.register("Lou", new), 200)

    def test_closed_invitations_leave_only_the_admins_code(self):
        sam = self.sign_up("Sam")
        alex = self.sign_up("Alex")
        alex_code = self.my_code(alex)
        self.assertEqual(self.call("POST", "/api/admin/invites", {"open": False}, alex)[0].status, 403)
        self.assertEqual(self.call("POST", "/api/admin/invites", {"open": False}, sam)[0].status, 200)
        self.assertEqual(self.register("Lou", alex_code), 403)
        self.assertIsNone(self.my_code(alex))
        self.assertEqual(self.call("POST", "/api/invite", {}, alex)[0].status, 403)
        self.assertEqual(self.register("Lou", self.my_code(sam)), 200)

    def test_admin_renews_the_code_of_someone_else(self):
        sam = self.sign_up("Sam")
        alex = self.sign_up("Alex")
        old = self.my_code(alex)
        alex_id = self.accounts.list_users()[1]["id"]
        self.assertEqual(self.call("POST", "/api/admin/code", {"id": alex_id}, alex)[0].status, 403)
        self.assertEqual(self.call("POST", "/api/admin/code", {"id": alex_id}, sam)[0].status, 200)
        self.assertNotEqual(self.my_code(alex), old)
        self.assertEqual(self.call("POST", "/api/admin/code", {"id": 999}, sam)[0].status, 422)

    def test_people_online_are_counted(self):
        sam = self.sign_up("Sam")
        self.sign_up("Alex")
        data = json.loads(self.call("GET", "/api/data", cookie=sam)[1])
        self.assertNotIn("online", data)  # only the administration shows it
        info = json.loads(self.call("GET", "/api/admin/overview", cookie=sam)[1])
        self.assertEqual(info["online"], 2)
        with self.dashboard.connect() as db:
            db.execute("UPDATE sessions SET last_seen = 0")
        self.assertEqual(self.accounts.online_count(), 0)

    def test_last_visit_survives_a_logout(self):
        sam = self.sign_up("Sam")
        alex = self.sign_up("Alex")
        self.call("POST", "/api/logout", {}, alex)
        users = json.loads(self.call("GET", "/api/admin/users", cookie=sam)[1])["users"]
        self.assertIsNotNone(users[1]["last_seen"])
        self.assertFalse(users[1]["online"])

    def test_admin_sees_which_options_people_use(self):
        sam = self.sign_up("Sam")
        alex = self.sign_up("Alex")
        settings = {"meteo-alerts": "1", "meteo-barometer": "0", "meteo-theme": "sombre", "meteo-period": "168"}
        self.call("PATCH", "/api/settings", {"settings": settings}, alex)
        usage = json.loads(self.call("GET", "/api/admin/overview", cookie=sam)[1])["usage"]
        counts = {item["label"]: item["count"] for item in usage["options"]}
        self.assertEqual(usage["people"], 2)
        self.assertEqual((counts["Alertes météo"], counts["Baromètre"], counts["Prévisions"]), (1, 1, 2))
        self.assertEqual(usage["themes"], [{"label": "Automatique", "count": 1}, {"label": "Sombre", "count": 1}])
        self.assertEqual(usage["periods"], [{"label": "24 h", "count": 1}, {"label": "7 jours", "count": 1}])

    def test_admin_page_is_for_the_admin_only(self):
        self.assertEqual(self.call("GET", "/admin")[0].getheader("Location"), "/login")
        sam = self.sign_up("Sam")
        alex = self.sign_up("Alex")
        self.assertEqual(self.call("GET", "/admin", cookie=alex)[0].getheader("Location"), "/")
        self.assertEqual(self.call("GET", "/admin.js", cookie=alex)[0].status, 302)
        self.assertEqual(self.call("GET", "/api/admin/overview", cookie=alex)[0].status, 403)
        response, body = self.call("GET", "/admin", cookie=sam)
        self.assertEqual(response.status, 200)
        self.assertIn("Administration", body.decode())

    def test_overview_of_the_server(self):
        sam = self.sign_up("Sam")
        info = json.loads(self.call("GET", "/api/admin/overview", cookie=sam)[1])
        self.assertEqual(info["version"], app.APP_VERSION)
        self.assertEqual(info["limits"], {
            "total_stations": app.MAX_TOTAL_STATIONS, "user_stations": app.MAX_STATIONS,
            "poll_minutes": 10, "neighbours_allowed": True,
        })
        self.assertEqual([(s["name"], s["followers"]) for s in info["stations"]], [("Maison", 1)])
        self.assertGreater(info["database_bytes"], 0)
        self.assertGreater(info["disk_free"], 0)
        self.assertEqual(info["users"], 1)

    def test_limits_are_checked_then_applied(self):
        sam = self.sign_up("Sam")
        limits = {"total_stations": 30, "user_stations": 1, "poll_minutes": 15, "neighbours_allowed": False}
        for wrong in ({**limits, "user_stations": 9}, {**limits, "poll_minutes": 2}, {**limits, "total_stations": "30"}):
            self.assertEqual(self.call("POST", "/api/admin/limits", wrong, sam)[0].status, 422)
        self.assertEqual(self.call("POST", "/api/admin/limits", limits, sam)[0].status, 200)
        self.assertEqual(self.dashboard.poll_seconds, 15 * 60)
        response, body = self.call("POST", "/api/stations", {"link": link(OTHER), "place": "43.53, 1.49"}, sam)
        self.assertEqual((response.status, json.loads(body)["error"]), (422, "1 stations au maximum"))
        data = json.loads(self.call("GET", "/api/data?radius=5", cookie=sam)[1])
        self.assertFalse(data["neighbours_allowed"])
        self.assertIsNone(data["stations"][0]["neighbours"])

    def test_announcement_reaches_everyone(self):
        sam = self.sign_up("Sam")
        alex = self.sign_up("Alex")
        self.assertEqual(self.call("POST", "/api/admin/announcement", {"text": "Coupure ce soir"}, alex)[0].status, 403)
        self.assertEqual(self.call("POST", "/api/admin/announcement", {"text": "x" * 301}, sam)[0].status, 422)
        self.call("POST", "/api/admin/announcement", {"text": " Coupure ce soir "}, sam)
        self.assertEqual(json.loads(self.call("GET", "/api/data", cookie=alex)[1])["announcement"], "Coupure ce soir")

    def test_announcement_ends_after_its_duration(self):
        sam = self.sign_up("Sam")
        for hours in (2, 1.5, True, "6"):
            answer = self.call("POST", "/api/admin/announcement", {"text": "Coupure", "hours": hours}, sam)
            self.assertEqual(answer[0].status, 422)
        self.call("POST", "/api/admin/announcement", {"text": "Coupure", "hours": 6}, sam)
        info = json.loads(self.call("GET", "/api/admin/overview", cookie=sam)[1])
        self.assertEqual(info["announcement"], "Coupure")
        self.assertAlmostEqual(info["announcement_until"], time.time() + 6 * 3600, delta=60)
        self.assertEqual(self.dashboard.announcement(time.time() + 6 * 3600 + 1), ("", None))
        self.call("POST", "/api/admin/announcement", {"text": "Travaux", "hours": 0}, sam)
        self.assertEqual(self.dashboard.announcement(time.time() + 365 * 86400), ("Travaux", None))

    def user_id(self, name):
        return next(user["id"] for user in self.accounts.list_users() if user["name"] == name)

    def test_owner_names_another_administrator(self):
        sam, alex, lou = self.sign_up("Sam"), self.sign_up("Alex"), self.sign_up("Lou")
        alex_id, lou_id = self.user_id("Alex"), self.user_id("Lou")
        self.assertEqual(self.call("POST", "/api/admin/role", {"id": alex_id, "admin": True}, alex)[0].status, 403)
        self.assertEqual(self.call("POST", "/api/admin/role", {"id": alex_id, "admin": True}, sam)[0].status, 200)
        self.assertEqual(self.call("GET", "/admin", cookie=alex)[0].status, 200)
        self.assertTrue(json.loads(self.call("GET", "/api/data", cookie=alex)[1])["is_admin"])
        roles = [u["role"] for u in json.loads(self.call("GET", "/api/admin/users", cookie=alex)[1])["users"]]
        self.assertEqual(roles, ["owner", "admin", "user"])
        # Another administrator cannot touch the owner, nor name administrators.
        sam_id = self.user_id("Sam")
        self.assertEqual(self.call("DELETE", f"/api/admin/users?id={sam_id}", cookie=alex)[0].status, 422)
        self.assertEqual(self.call("POST", "/api/admin/reset", {"id": sam_id}, alex)[0].status, 422)
        self.assertEqual(self.call("POST", "/api/admin/role", {"id": lou_id, "admin": True}, alex)[0].status, 403)
        self.assertEqual(self.call("POST", "/api/admin/role", {"id": sam_id, "admin": False}, sam)[0].status, 422)
        self.assertEqual(self.call("DELETE", f"/api/admin/users?id={lou_id}", cookie=alex)[0].status, 200)
        self.call("POST", "/api/admin/role", {"id": alex_id, "admin": False}, sam)
        self.assertEqual(self.call("GET", "/admin", cookie=alex)[0].getheader("Location"), "/")

    def test_backup_is_a_full_copy_for_administrators_only(self):
        sam = self.sign_up("Sam")
        alex = self.sign_up("Alex")
        self.assertEqual(self.call("GET", "/api/admin/backup", cookie=alex)[0].status, 403)
        response, body = self.call("GET", "/api/admin/backup", cookie=sam)
        self.assertEqual(response.status, 200)
        self.assertIn("attachment", response.getheader("Content-Disposition"))
        path = Path(tempfile.mkdtemp()) / "copie.db"
        path.write_bytes(body)
        copy = sqlite3.connect(path)
        self.addCleanup(copy.close)
        self.assertEqual(copy.execute("SELECT COUNT(*) FROM users").fetchone()[0], 2)

    def test_error_log_keeps_changes_only(self):
        sam = self.sign_up("Sam")
        for error in ("panne", "panne", None):
            self.dashboard.status["stations"] = {FIRST: error} if error else {}
            self.dashboard.log_changes()
        log = json.loads(self.call("GET", "/api/admin/overview", cookie=sam)[1])["log"]
        self.assertEqual([(e["kind"], e["station"], e["message"]) for e in log], [
            ("Mesures", "Maison", "Rétabli"), ("Mesures", "Maison", "panne"),
        ])

    def test_abandoned_station_data_can_be_deleted(self):
        sam = self.sign_up("Sam")
        with self.dashboard.connect() as db:
            self.dashboard.insert(db, FIRST, {**dict.fromkeys(app.COLUMNS), "ts": 1000, "temperature": 5})
        self.assertEqual(self.call("DELETE", f"/api/admin/stations?id={FIRST}", cookie=sam)[0].status, 422)
        self.call("DELETE", f"/api/stations?id={FIRST}", cookie=sam)
        info = json.loads(self.call("GET", "/api/admin/overview", cookie=sam)[1])
        self.assertEqual([(s["id"], s["measures"]) for s in info["abandoned"]], [(FIRST, 1)])
        self.assertEqual(self.call("DELETE", f"/api/admin/stations?id={FIRST}", cookie=sam)[0].status, 200)
        info = json.loads(self.call("GET", "/api/admin/overview", cookie=sam)[1])
        self.assertEqual(info["abandoned"], [])
        with self.dashboard.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM measures").fetchone()[0], 0)

    def test_admin_sees_users_and_the_invitation_code(self):
        sam = self.sign_up("Sam")
        self.sign_up("Alex")
        answer = json.loads(self.call("GET", "/api/admin/users", cookie=sam)[1])
        self.assertTrue(answer["invites_open"])
        self.assertEqual(answer["users"][0]["code"], self.dashboard.invite_code(answer["users"][0]["id"]))
        self.assertEqual(answer["users"][1]["invited_by"], "Sam")
        self.assertTrue(answer["users"][0]["online"])
        self.assertEqual([u["name"] for u in answer["users"]], ["Sam", "Alex"])
        self.assertEqual(answer["users"][0]["stations"], [
            {"name": "Maison", "url": stations.weathermap_url(FIRST)},
        ])
        self.assertEqual(answer["users"][1]["stations"], [])
        self.assertIsNotNone(answer["users"][0]["last_login"])
        self.assertNotIn("password", json.dumps(answer))

    def test_only_the_admin_gets_in(self):
        self.sign_up("Sam")
        alex = self.sign_up("Alex")
        for cookie, expected in ((alex, 403), (None, 401)):
            self.assertEqual(self.call("GET", "/api/admin/users", cookie=cookie)[0].status, expected)
            self.assertEqual(self.call("POST", "/api/admin/reset", {"id": 1}, cookie)[0].status, expected)
            self.assertEqual(self.call("DELETE", "/api/admin/users?id=1", cookie=cookie)[0].status, expected)

    def test_admin_deletes_an_account(self):
        sam, alex = self.sign_up("Sam"), self.sign_up("Alex")
        self.call("POST", "/api/stations", {"link": link(OTHER), "place": "43.53, 1.49"}, alex)
        self.assertEqual(self.call("DELETE", "/api/admin/users?id=2", cookie=sam)[0].status, 200)
        self.assertEqual(self.call("GET", "/api/data", cookie=alex)[0].status, 401)
        self.assertEqual(self.accounts.count(), 1)
        self.assertEqual([s["id"] for s in self.dashboard.stations], [FIRST])

    def test_admin_cannot_delete_or_reset_themselves(self):
        sam = self.sign_up("Sam")
        self.assertEqual(self.call("DELETE", "/api/admin/users?id=1", cookie=sam)[0].status, 422)
        self.assertEqual(self.call("POST", "/api/admin/reset", {"id": 1}, sam)[0].status, 422)
        self.assertEqual(self.accounts.count(), 1)

    def test_temporary_password_works_and_closes_old_sessions(self):
        sam, alex = self.sign_up("Sam"), self.sign_up("Alex")
        answer = json.loads(self.call("POST", "/api/admin/reset", {"id": 2}, sam)[1])
        self.assertEqual(self.call("GET", "/api/data", cookie=alex)[0].status, 401)
        login = self.call("POST", "/api/login", {"name": "Alex", "password": answer["password"]})
        self.assertEqual(login[0].status, 200)

    def test_user_changes_their_password(self):
        sam = self.sign_up("Sam")
        form = {"current": PASSWORD, "new": "nouveau-mot-de-passe"}
        self.assertEqual(self.call("POST", "/api/password", form, sam)[0].status, 200)
        self.assertEqual(self.call("POST", "/api/login", {"name": "Sam", "password": PASSWORD})[0].status, 401)
        self.assertEqual(self.call("POST", "/api/login", {"name": "Sam", "password": form["new"]})[0].status, 200)

    def test_password_change_closes_the_other_devices(self):
        sam = self.sign_up("Sam")
        login = self.call("POST", "/api/login", {"name": "Sam", "password": PASSWORD})[0]
        phone = login.getheader("Set-Cookie").split(";")[0]
        form = {"current": PASSWORD, "new": "nouveau-mot-de-passe"}
        self.assertEqual(self.call("POST", "/api/password", form, sam)[0].status, 200)
        self.assertEqual(self.call("GET", "/api/data", cookie=sam)[0].status, 200)
        self.assertEqual(self.call("GET", "/api/data", cookie=phone)[0].status, 401)

    def test_password_change_needs_the_current_one(self):
        sam = self.sign_up("Sam")
        form = {"current": "faux-mot-de-passe", "new": "nouveau-mot-de-passe"}
        self.assertEqual(self.call("POST", "/api/password", form, sam)[0].status, 422)
        self.assertEqual(self.call("POST", "/api/password", form)[0].status, 401)

    def test_page_cannot_be_framed(self):
        self.assertEqual(self.call("GET", "/login")[0].getheader("X-Frame-Options"), "DENY")


if __name__ == "__main__":
    unittest.main()
