import datetime
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import netatmo

STATION = {"id": "70:ee:50:12:34:56", "lat": 44.93, "lon": 4.89}
ITEM = {
    "_id": "70:ee:50:12:34:56",
    "measures": {
        "02:00:00:aa:bb:cc": {"res": {"1793790000": [8.3, 85]}, "type": ["temperature", "humidity"]},
        "70:ee:50:12:34:56": {"res": {"1793790100": [1017.2]}, "type": ["pressure"]},
        "05:00:00:aa:bb:cc": {"rain_60min": 0, "rain_24h": 0.51, "rain_live": 0, "rain_timeutc": 1793790200},
        "06:00:00:aa:bb:cc": {
            "wind_strength": 10, "wind_angle": 259, "gust_strength": 17,
            "gust_angle": 270, "wind_timeutc": 1793790300,
        },
    },
}


class ParseStation(unittest.TestCase):
    def test_all_modules(self):
        reading = netatmo.parse_station(ITEM)
        self.assertEqual(reading["ts"], 1793790300)
        self.assertEqual(reading["temperature"], 8.3)
        self.assertEqual(reading["humidity"], 85)
        self.assertEqual(reading["pressure"], 1017.2)
        self.assertEqual(reading["rain_24h"], 0.51)
        self.assertEqual(reading["gust_strength"], 17)

    def test_official_example_spelling(self):
        reading = netatmo.parse_station({"measures": {
            "a": {"res": {"100": [20, 60]}, "type": ["temperature"]},
            "b": {"res": {"100": [1011.3]}, "type": "pressure"},
            "c": {"wind_strengh": 10, "gust_strenght": 17, "wind_angle": 259,
                  "gust_angle": 270, "wind_timeutc": 100},
        }})
        self.assertEqual(reading["humidity"], 60)
        self.assertEqual(reading["pressure"], 1011.3)
        self.assertEqual(reading["wind_strength"], 10)
        self.assertEqual(reading["gust_strength"], 17)

    def test_missing_modules_stay_empty(self):
        reading = netatmo.parse_station({"measures": {
            "a": {"res": {"100": [20, 60]}, "type": ["temperature", "humidity"]},
        }})
        self.assertIsNone(reading["wind_strength"])
        self.assertIsNone(reading["rain_60min"])

    def test_no_measure(self):
        self.assertIsNone(netatmo.parse_station({"measures": {}}))


class History(unittest.TestCase):
    def setUp(self):
        self.tokens = mock.Mock()
        self.tokens.access_token.return_value = "token"
        patcher = mock.patch.object(netatmo.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_find_modules(self):
        self.assertEqual(netatmo.parse_station(ITEM)["modules"], {
            "base": "70:ee:50:12:34:56",
            "outdoor": "02:00:00:aa:bb:cc",
            "rain": "05:00:00:aa:bb:cc",
            "wind": "06:00:00:aa:bb:cc",
        })

    def test_values_land_on_the_grid_with_their_names(self):
        answer = {"body": {"1793796814": [8.7, 85]}}
        with mock.patch.object(netatmo, "_send", return_value=answer):
            rows = netatmo.fetch_history(self.tokens, "d", "outdoor", "m", 0, 10)
        self.assertEqual(rows, {1793796814 // 1800 * 1800: {"temperature": 8.7, "humidity": 85}})

    def test_long_periods_are_read_in_several_calls(self):
        full = {"body": {str(i * 1800): [1000] for i in range(1, netatmo.HISTORY_LIMIT + 1)}}
        rest = {"body": {str((netatmo.HISTORY_LIMIT + 1) * 1800): [1001]}}
        with mock.patch.object(netatmo, "_send", side_effect=[full, rest]) as send:
            rows = netatmo.fetch_history(self.tokens, "d", "base", "d", 0, 10 ** 9)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(len(rows), netatmo.HISTORY_LIMIT + 1)
        second_url = send.call_args_list[1][0][0].full_url
        self.assertIn(f"date_begin={netatmo.HISTORY_LIMIT * 1800 + 1}", second_url)

    def test_short_outage_is_retried(self):
        replies = [netatmo.ApiError(503, "unavailable"), {"body": {"1800": [1000]}}]
        with mock.patch.object(netatmo, "_send", side_effect=replies):
            rows = netatmo.fetch_history(self.tokens, "d", "base", "d", 0, 10)
        self.assertEqual(rows, {1800: {"pressure": 1000}})

    def test_long_outage_gives_up(self):
        error = netatmo.ApiError(503, "unavailable")
        with mock.patch.object(netatmo, "_send", side_effect=error) as send:
            with self.assertRaises(netatmo.ApiError):
                netatmo.fetch_history(self.tokens, "d", "base", "d", 0, 10)
        self.assertEqual(send.call_count, len(netatmo.HISTORY_RETRY_DELAYS) + 1)

    def test_daily_rain_uses_the_local_day(self):
        noon = int(datetime.datetime(2026, 10, 25, 11, 0).timestamp())
        with mock.patch.object(netatmo, "_send", return_value={"body": {str(noon): [0.8]}}) as send:
            days = netatmo.fetch_daily_rain(self.tokens, "d", "r", 0, 10)
        self.assertEqual(days, {"2026-10-25": 0.8})
        self.assertIn("scale=1day", send.call_args[0][0].full_url)
        self.assertIn("type=sum_rain", send.call_args[0][0].full_url)

    def test_empty_answer(self):
        with mock.patch.object(netatmo, "_send", return_value={"body": []}):
            self.assertEqual(netatmo.fetch_history(self.tokens, "d", "base", "d", 0, 10), {})

    def test_refused_station_does_not_burn_a_token_refresh(self):
        error = netatmo.ApiError(403, "Device not found", code=9)
        with mock.patch.object(netatmo, "_send", side_effect=error):
            with self.assertRaises(netatmo.ApiError):
                netatmo.fetch_history(self.tokens, "d", "base", "d", 0, 10)
        self.tokens.access_token.assert_called_once_with(force_refresh=False)


class TokensTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "token.json"

    def test_rotated_refresh_token_is_saved(self):
        tokens = netatmo.Tokens(self.path, "id", "secret", "old")
        answer = {"access_token": "a2", "refresh_token": "new", "expires_in": 10800}
        with mock.patch.object(netatmo, "_send", return_value=answer) as send:
            self.assertEqual(tokens.access_token(), "a2")
            self.assertEqual(tokens.access_token(), "a2")
        self.assertEqual(send.call_count, 1)
        self.assertEqual(json.loads(self.path.read_text())["refresh_token"], "new")
        self.assertIn(b"refresh_token=old", send.call_args[0][0].data)

    def test_saved_token_beats_config_seed(self):
        self.path.write_text(json.dumps({"refresh_token": "saved"}))
        self.assertEqual(netatmo.Tokens(self.path, "id", "s", "seed").data["refresh_token"], "saved")

    def test_invalid_grant_asks_to_reconnect(self):
        tokens = netatmo.Tokens(self.path, "id", "secret", "old")
        error = netatmo.ApiError(400, "invalid_grant")
        with mock.patch.object(netatmo, "_send", side_effect=error):
            with self.assertRaises(netatmo.AuthRequired):
                tokens.access_token()
        self.assertFalse(tokens.connected)

    def test_network_failure_keeps_refresh_token(self):
        tokens = netatmo.Tokens(self.path, "id", "secret", "old")
        error = netatmo.ApiError(503, "unavailable")
        with mock.patch.object(netatmo, "_send", side_effect=error):
            with self.assertRaises(netatmo.ApiError):
                tokens.access_token()
        self.assertTrue(tokens.connected)


class FetchStation(unittest.TestCase):
    def tokens(self):
        tokens = mock.Mock()
        tokens.access_token.return_value = "token"
        return tokens

    def test_short_outage_is_retried(self):
        replies = [netatmo.ApiError(503, "unavailable"), {"body": [ITEM]}]
        with mock.patch.object(netatmo, "_send", side_effect=replies), \
                mock.patch.object(netatmo.time, "sleep"):
            self.assertEqual(netatmo.fetch_station(self.tokens(), STATION)["temperature"], 8.3)

    def test_exact_position_comes_back_and_narrows_the_area(self):
        item = {**ITEM, "place": {"location": [1.49, 43.53]}}
        with mock.patch.object(netatmo, "_send", return_value={"body": [item]}) as send:
            reading = netatmo.fetch_station(self.tokens(), {**STATION, "box": 0.01})
        self.assertEqual(reading["location"], [1.49, 43.53])
        self.assertIn("lat_ne=44.94", send.call_args[0][0].full_url)

    def test_picks_requested_station_among_neighbours(self):
        other = {"_id": "70:ee:50:00:00:00", "measures": {"a": {"res": {"1": [1, 2]}, "type": ["temperature", "humidity"]}}}
        with mock.patch.object(netatmo, "_send", return_value={"body": [other, ITEM]}):
            self.assertEqual(netatmo.fetch_station(self.tokens(), STATION)["temperature"], 8.3)

    def test_station_absent(self):
        with mock.patch.object(netatmo, "_send", return_value={"body": []}):
            with self.assertRaises(netatmo.StationNotFound):
                netatmo.fetch_station(self.tokens(), STATION)

    def test_rejected_token_is_refreshed_once(self):
        tokens = self.tokens()
        replies = [netatmo.ApiError(403, "expired"), {"body": [ITEM]}]
        with mock.patch.object(netatmo, "_send", side_effect=replies):
            netatmo.fetch_station(tokens, STATION)
        self.assertEqual(tokens.access_token.call_args_list[1], mock.call(force_refresh=True))


if __name__ == "__main__":
    unittest.main()
