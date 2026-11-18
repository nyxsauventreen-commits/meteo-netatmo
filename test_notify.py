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

STATION = {"id": "70:ee:50:12:34:56", "name": "Maison", "lat": 44.93, "lon": 4.89}
NOW = 1_800_000_000


def settings(**changes):
    return {**notify.default_settings(), "ntfy": True, "events": list(notify.EVENTS), **changes}


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
    def test_defaults_have_a_long_private_topic(self):
        first, second = notify.default_settings(), notify.default_settings()
        self.assertRegex(first["topic"], notify.TOPIC)
        self.assertNotEqual(first["topic"], second["topic"])
        self.assertFalse(first["ntfy"] or first["email"])

    def test_page_cannot_choose_the_topic(self):
        current = notify.default_settings()
        cleaned = notify.clean_settings({"ntfy": True, "topic": "public", "events": ["frost"]}, current)
        self.assertEqual(cleaned["topic"], current["topic"])
        self.assertEqual(cleaned["events"], ["frost"])

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

    def test_mailbox_from_config_or_environment(self):
        self.assertIsNone(notify.smtp_settings({}, {}))
        config = {"smtp": {"user": "meteo@example.org", "password": "a"}}
        self.assertEqual(notify.smtp_settings(config, {})["password"], "a")
        self.assertEqual(notify.smtp_settings(config, {"METEO_SMTP_PASSWORD": "b"})["password"], "b")


class Sending(unittest.TestCase):
    def test_ntfy_message_keeps_accents(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            notify.send_ntfy("meteo-sujet", "Gel : Été", "−2 °C", urgent=True)
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://ntfy.sh")
        self.assertEqual(json.loads(request.data), {
            "topic": "meteo-sujet", "title": "Gel : Été", "message": "−2 °C", "priority": 4, "tags": ["partly_sunny"],
        })

    def test_ntfy_failure(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            with self.assertRaisesRegex(notify.NotifyError, "ntfy"):
                notify.send_ntfy("meteo-sujet", "t", "m")

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
        self.dashboard.save_notifications(self.user, {"ntfy": True, "events": ["frost", "offline"]})
        self.sent = []

    def measure(self, temperature, age=60):
        with self.dashboard.connect() as db:
            self.dashboard.insert(db, STATION["id"], {**dict.fromkeys(netatmo.FIELDS), "ts": int(time.time()) - age,
                                                      "temperature": temperature})

    def check(self, side_effect=None):
        def send(topic, title, message, server, urgent):
            self.sent.append(title)
        with mock.patch.object(notify, "send_ntfy", side_effect=side_effect or send):
            self.dashboard.check_notifications()

    def test_one_message_per_frost_episode(self):
        self.measure(-2, age=90)
        self.check()
        self.check()
        self.assertEqual(self.sent, ["Gel : Maison"])
        self.measure(3, age=60)
        self.check()
        self.measure(-1, age=30)
        self.check()
        self.assertEqual(self.sent, ["Gel : Maison", "Gel : Maison"])

    def test_failure_is_retried_and_reported(self):
        self.measure(-2)
        self.check(side_effect=notify.NotifyError("ntfy ne répond pas"))
        self.assertIn("ntfy", self.dashboard.status["notify_error"])
        self.check()
        self.assertEqual(self.sent, ["Gel : Maison"])
        self.assertIsNone(self.dashboard.status["notify_error"])

    def test_nothing_without_a_channel(self):
        self.dashboard.save_notifications(self.user, {"ntfy": False, "events": ["frost"]})
        self.measure(-2)
        self.check()
        self.assertEqual(self.sent, [])

    def test_email_needs_the_mailbox_of_the_server(self):
        with self.assertRaisesRegex(notify.NotifyError, "pas configuré"):
            self.dashboard.save_notifications(self.user, {"email": True, "address": "moi@example.fr"})

    def test_topic_stays_the_same(self):
        topic = self.dashboard.notification_settings(self.user)["topic"]
        self.dashboard.save_notifications(self.user, {"ntfy": False})
        self.assertEqual(self.dashboard.notifications_page(self.user)["topic"], topic)

    def test_one_test_message_a_minute(self):
        with mock.patch.object(notify, "send_ntfy") as send:
            self.assertEqual(self.dashboard.send_test(self.user), {"ntfy": None})
            with self.assertRaisesRegex(notify.NotifyError, "minute"):
                self.dashboard.send_test(self.user)
        send.assert_called_once()

    def test_poll_sends_after_collecting(self):
        self.measure(-2)
        with mock.patch.object(self.dashboard, "collect_forecast"), mock.patch.object(self.dashboard, "collect_alerts"), \
                mock.patch.object(netatmo, "fetch_station", side_effect=netatmo.AuthRequired("x")), \
                mock.patch.object(notify, "send_ntfy") as send:
            self.dashboard.tokens.data = {"refresh_token": "r"}
            self.dashboard.poll()
        send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
