import json
import smtplib
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import app
import netatmo
import notify
import webpush

STATION = {"id": "70:ee:50:12:34:56", "name": "Maison", "lat": 44.93, "lon": 4.89}
NOW = 1_800_000_000


def settings(**changes):
    return {**notify.default_settings(), "events": list(notify.EVENTS), **changes}


def reading(age=600, **values):
    return {**dict.fromkeys(netatmo.FIELDS), "ts": NOW - age, **values}


def events(reading_, warnings=(), active=(), **changes):
    return notify.weather_events(settings(**changes), STATION, reading_, list(warnings), NOW, set(active))


class Triggers(unittest.TestCase):
    def test_frost_starts_at_the_threshold_and_ends_one_degree_above(self):
        self.assertIn("frost", events(reading(temperature=0)))
        self.assertNotIn("frost", events(reading(temperature=0.5)))
        self.assertIn("frost", events(reading(temperature=0.5), active={"frost"}))
        self.assertNotIn("frost", events(reading(temperature=1.5), active={"frost"}))

    def test_heat_rain_and_gusts(self):
        found = events(reading(temperature=36, rain_60min=12, gust_strength=80))
        self.assertEqual(set(found), {"heat", "rain", "wind"})
        self.assertIn("80 km/h", found["wind"][1])
        self.assertIn("rain", events(reading(rain_60min=6), active={"rain"}))  # half the threshold keeps it going

    def test_only_chosen_events_and_own_thresholds(self):
        own = {**notify.default_settings()["thresholds"], "frost": -5.0}
        self.assertEqual(events(reading(temperature=-3), thresholds=own), {})
        self.assertEqual(events(reading(temperature=-30), events=["heat"]), {})

    def test_orange_and_red_warnings_only(self):
        warnings = [
            {"area": "Haute-Garonne", "level": "orange", "hazard": "vent", "icon": "💨", "start": NOW - 10, "end": NOW + 99},
            {"area": "Haute-Garonne", "level": "yellow", "hazard": "pluie", "icon": "🌧️", "start": NOW - 10, "end": NOW + 99},
            {"area": "Haute-Garonne", "level": "red", "hazard": "crues", "icon": "🌊", "start": NOW - 99, "end": NOW - 1},
        ]
        found = events(reading(), warnings)
        self.assertEqual(list(found), [f"vigilance:vent:orange:{NOW - 10}"])
        self.assertIn("Vigilance orange vent", found[f"vigilance:vent:orange:{NOW - 10}"][0])

    def test_silent_station(self):
        found = events(reading(age=4 * 3600, temperature=-9))
        self.assertEqual(set(found), {"offline"})  # an old reading triggers no weather event
        self.assertNotIn("offline", events(reading(age=3600)))
        self.assertIn("offline", events(reading(age=2 * 3600), active={"offline"}))

    def test_station_without_any_reading(self):
        self.assertEqual(events(None), {})


class Settings(unittest.TestCase):
    def test_everything_is_off_at_first(self):
        defaults = notify.default_settings()
        self.assertEqual((defaults["events"], defaults["stations"], defaults["email"]), ([], [], False))
        self.assertEqual(defaults["thresholds"]["frost"], 0.0)

    def test_choices_are_kept(self):
        cleaned = notify.clean_settings({"events": ["frost"], "thresholds": {"wind": 80}}, notify.default_settings())
        self.assertEqual((cleaned["events"], cleaned["thresholds"]["wind"]), (["frost"], 80.0))

    def test_refused(self):
        current = notify.default_settings()
        for form in (
            {"email": True, "address": "pas-une-adresse"},
            {"address": "a@b.fr\r\nBcc: x@y.fr"},
            {"events": ["inconnu"]},
            {"thresholds": {"wind": 5}},
            {"thresholds": {"frost": True}},
            {"thresholds": {"inconnu": 1}},
            [],
        ):
            with self.assertRaises(notify.NotifyError, msg=form):
                notify.clean_settings(form, current)

    def test_mailbox_from_config_page_or_environment(self):
        self.assertIsNone(notify.smtp_settings({}, {}))
        config = {"smtp": {"user": "meteo@example.org", "password": "a"}}
        self.assertEqual(notify.smtp_settings(config, {})["password"], "a")
        saved = {"user": "site@example.org", "password": "p"}
        self.assertEqual(notify.smtp_settings(config, {}, saved)["user"], "site@example.org")
        self.assertEqual(notify.smtp_settings(config, {"METEO_SMTP_PASSWORD": "b"}, saved)["password"], "b")

    def test_mailbox_is_checked_with_gmail(self):
        with mock.patch.object(smtplib, "SMTP_SSL") as server:
            mailbox = notify.check_mailbox(" site@example.org ", "abcd efgh ijkl mnop")
        self.assertEqual((mailbox["user"], mailbox["password"]), ("site@example.org", "abcdefghijklmnop"))
        self.assertEqual(server.call_args[0], ("smtp.gmail.com", 465))
        with mock.patch.object(smtplib, "SMTP_SSL") as server:
            server.return_value.__enter__.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b"no")
            with self.assertRaisesRegex(notify.NotifyError, "mot de passe d'application"):
                notify.check_mailbox("site@example.org", "abcdefghijklmnop")
        for user, password in (("pas-une-adresse", "abcdefghijklmnop"), ("site@example.org", "court")):
            with self.assertRaises(notify.NotifyError):
                notify.check_mailbox(user, password)


class Sending(unittest.TestCase):
    def test_email_goes_over_verified_tls(self):
        smtp = {"user": "meteo@example.org", "password": "secret", "sender_name": "Météo"}
        with mock.patch.object(smtplib, "SMTP_SSL") as server:
            notify.send_email(smtp, "moi@example.fr", "Gel", "−2 °C")
        host, port = server.call_args[0]
        self.assertEqual((host, port), ("smtp.gmail.com", 465))
        self.assertTrue(server.call_args[1]["context"].check_hostname)
        session = server.return_value.__enter__.return_value
        session.login.assert_called_once_with("meteo@example.org", "secret")
        mail = session.send_message.call_args[0][0]
        self.assertEqual((mail["To"], mail["Subject"]), ("moi@example.fr", "Gel"))

    def test_email_refused_password_and_missing_setup(self):
        with mock.patch.object(smtplib, "SMTP_SSL") as server:
            server.return_value.__enter__.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b"no")
            with self.assertRaisesRegex(notify.NotifyError, "mot de passe d'application"):
                notify.send_email({"user": "u", "password": "p"}, "moi@example.fr", "t", "m")
        with self.assertRaisesRegex(notify.NotifyError, "pas configuré"):
            notify.send_email(None, "moi@example.fr", "t", "m")


class Collection(unittest.TestCase):
    """Sending from the dashboard, in local mode (user 0)."""

    ENDPOINT = "https://fcm.googleapis.com/fcm/send/appareil"

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        for patcher in (mock.patch.object(app, "DATA", root), mock.patch.object(app, "CONFIG", root / "config.json")):
            patcher.start()
            self.addCleanup(patcher.stop)
        config = {"client_id": "id", "client_secret": "s", "stations": [{**STATION, "slot": 0}]}
        self.dashboard = app.Dashboard(config, local=True)
        self.user = app.LOCAL_USER["id"]
        self.dashboard.save_notifications(self.user, {"events": ["frost", "offline"], "stations": [STATION["id"], "pas-a-moi"]})
        self.dashboard.subscribe(self.user, self.ENDPOINT, "mailto:meteo@example.invalid")
        self.sent = []

    def measure(self, temperature, age=60):
        with self.dashboard.connect() as db:
            self.dashboard.insert(db, STATION["id"], {**dict.fromkeys(netatmo.FIELDS), "ts": int(time.time()) - age,
                                                      "temperature": temperature})

    def check(self, side_effect=None):
        def send(keys, endpoint, subject, urgent):
            self.sent.append(endpoint)
        with mock.patch.object(webpush, "send", side_effect=side_effect or send):
            self.dashboard.check_notifications()

    def titles(self):
        return [message["title"] for message in self.dashboard.pending_messages(self.user)]

    def test_one_message_per_frost_episode(self):
        self.measure(-2, age=90)
        self.check()
        self.check()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.titles(), ["Gel : Maison"])
        self.measure(3, age=60)
        self.check()
        self.measure(-1, age=30)
        self.check()
        self.assertEqual(len(self.sent), 2)

    def test_the_device_fetches_the_text(self):
        self.measure(-2)
        self.check()
        message = self.dashboard.pending_messages(self.user)[0]
        self.assertEqual((message["title"], message["body"]), ("Gel : Maison", "-2 °C en ce moment."))
        later = int(time.time()) + app.PUSH_SHOWN + 1
        self.assertEqual(self.dashboard.pending_messages(self.user, now=later), [])

    def test_failure_is_retried_and_reported(self):
        self.measure(-2)
        self.check(side_effect=webpush.PushError("Service de notification : erreur 500"))
        self.assertIn("erreur 500", self.dashboard.status["notify_error"])
        self.check()
        self.assertEqual(len(self.sent), 1)
        self.assertIsNone(self.dashboard.status["notify_error"])

    def test_expired_device_is_forgotten(self):
        self.measure(-2)
        self.check(side_effect=webpush.Expired("gone"))
        self.assertEqual(self.dashboard.devices(self.user), [])
        self.assertIn("réactivez", self.dashboard.status["notify_error"])

    def test_only_chosen_stations_and_only_mine(self):
        self.assertEqual(self.dashboard.notification_settings(self.user)["stations"], [STATION["id"]])
        self.dashboard.save_notifications(self.user, {"events": ["frost"], "stations": []})
        self.measure(-2)
        self.check()
        self.assertEqual(self.sent, [])

    def test_nothing_without_a_device_or_email(self):
        self.dashboard.unsubscribe(self.user, self.ENDPOINT)
        self.measure(-2)
        self.check()
        self.assertEqual(self.sent, [])

    def test_only_push_services_and_ten_devices(self):
        with self.assertRaises(notify.NotifyError):
            self.dashboard.subscribe(self.user, "https://evil.example/x", "mailto:x@example.invalid")
        for number in range(app.MAX_DEVICES - 1):
            self.dashboard.subscribe(self.user, f"{self.ENDPOINT}{number}", "mailto:x@example.invalid")
        with self.assertRaisesRegex(notify.NotifyError, "appareils au plus"):
            self.dashboard.subscribe(self.user, f"{self.ENDPOINT}-trop", "mailto:x@example.invalid")

    def test_email_needs_a_mailbox(self):
        with self.assertRaisesRegex(notify.NotifyError, "pas configuré"):
            self.dashboard.save_notifications(self.user, {"email": True, "address": "moi@example.fr"})

    def test_mailbox_saved_from_the_page_is_never_shown_back(self):
        with mock.patch.object(smtplib, "SMTP_SSL") as server:
            self.dashboard.save_mailbox("site@example.org", "abcd efgh ijkl mnop")
            self.dashboard.test_mailbox("moi@example.fr")
            with self.assertRaisesRegex(notify.NotifyError, "minute"):
                self.dashboard.test_mailbox("moi@example.fr")
        mail = server.return_value.__enter__.return_value.send_message.call_args[0][0]
        self.assertEqual(mail["To"], "moi@example.fr")
        page = self.dashboard.notifications_page(self.user, True)
        self.assertEqual(page["mailbox"], {"address": "site@example.org", "may_change": True, "from_page": True})
        self.assertNotIn("abcdefghijklmnop", json.dumps(page))
        restarted = app.Dashboard({"client_id": "id", "client_secret": "s"}, local=True)
        self.assertEqual(restarted.smtp["password"], "abcdefghijklmnop")
        self.dashboard.save_notifications(self.user, {"email": True, "address": "moi@example.fr"})
        self.dashboard.remove_mailbox()
        self.assertIsNone(self.dashboard.smtp)

    def test_one_test_message_a_minute(self):
        with mock.patch.object(webpush, "send") as send:
            self.assertEqual(self.dashboard.send_test(self.user), {"push": None})
            with self.assertRaisesRegex(notify.NotifyError, "minute"):
                self.dashboard.send_test(self.user)
        send.assert_called_once()

    def test_poll_sends_after_collecting(self):
        self.measure(-2)
        with mock.patch.object(self.dashboard, "collect_forecast"), mock.patch.object(self.dashboard, "collect_alerts"), \
                mock.patch.object(netatmo, "fetch_station", side_effect=netatmo.AuthRequired("x")), \
                mock.patch.object(webpush, "send") as send:
            self.dashboard.tokens.data = {"refresh_token": "r"}
            self.dashboard.poll()
        send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
