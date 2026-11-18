import unittest

import barometer

NOW = 1_800_000_000


def falling_by(change, minutes=(180, 150, 120, 90, 60, 30, 0), start=1015.0):
    """Points moving by `change` hPa over 3 hours, ending now."""
    return [(NOW - m * 60, start + change * (180 - m) / 180) for m in minutes]


class Trend(unittest.TestCase):
    def test_measures_the_change_over_three_hours(self):
        self.assertAlmostEqual(barometer.trend(falling_by(-2.4), NOW), -2.4)

    def test_shorter_span_is_scaled_to_three_hours(self):
        points = falling_by(-3, minutes=(120, 90, 60, 30, 0))
        self.assertAlmostEqual(barometer.trend(points, NOW), -3)

    def test_single_odd_reading_does_not_decide(self):
        points = falling_by(0)
        points[-1] = (points[-1][0], 1016.0)
        self.assertLess(abs(barometer.trend(points, NOW)), 0.8)

    def test_a_constant_offset_changes_nothing(self):
        normal = barometer.trend(falling_by(-2, start=1015), NOW)
        offset = barometer.trend(falling_by(-2, start=1030), NOW)
        self.assertAlmostEqual(normal, offset)

    def test_needs_enough_recent_data(self):
        self.assertIsNone(barometer.trend([], NOW))
        self.assertIsNone(barometer.trend(falling_by(1, minutes=(30, 20, 10, 0)), NOW))
        self.assertIsNone(barometer.trend(falling_by(1), NOW + 2 * 3600))


class Outlook(unittest.TestCase):
    def test_levels(self):
        for change, level in (
            (-5, "fall_fast"), (-2, "fall"), (-1, "fall_slow"), (0, "stable"),
            (1, "rise_slow"), (2, "rise"), (5, "rise_fast"),
        ):
            self.assertEqual(barometer.outlook(falling_by(change), 10, 70, NOW)["level"], level)

    def test_limits_between_levels(self):
        self.assertEqual(barometer.outlook(falling_by(0.4), 10, 70, NOW)["level"], "stable")
        self.assertEqual(barometer.outlook(falling_by(-0.6), 10, 70, NOW)["level"], "fall_slow")

    def test_no_outlook_without_data(self):
        self.assertIsNone(barometer.outlook([], 10, 70, NOW))


class Fog(unittest.TestCase):
    def test_dew_point(self):
        self.assertAlmostEqual(barometer.dew_point(20, 50), 9.3, delta=0.1)
        self.assertAlmostEqual(barometer.dew_point(10, 100), 10, delta=0.01)

    def test_very_humid_air(self):
        self.assertTrue(barometer.fog_is_possible(8, 95))
        self.assertFalse(barometer.fog_is_possible(8, 80))

    def test_missing_values(self):
        self.assertFalse(barometer.fog_is_possible(None, 95))
        self.assertFalse(barometer.fog_is_possible(8, None))


if __name__ == "__main__":
    unittest.main()
