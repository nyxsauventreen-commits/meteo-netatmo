import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mettre_a_jour_vps
import preparer_vps


class Preparer(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.project = Path(folder.name)
        (self.project / "data").mkdir()
        (self.project / "config.json").write_text(
            json.dumps({"client_id": "pc", "client_secret": "pc-secret", "stations": [{"id": "s"}]}),
            encoding="utf-8-sig",
        )
        db = sqlite3.connect(self.project / "data" / "meteo.db")
        db.execute("CREATE TABLE measures (ts INTEGER)")
        db.execute("INSERT INTO measures VALUES (1)")
        db.commit()
        db.close()
        for name, value in (("PROJECT", self.project), ("WORK", self.project / "vps-data")):
            patcher = mock.patch.object(preparer_vps, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_config_gets_the_vps_identifiers_and_keeps_the_stations(self):
        preparer_vps.prepare_data("vps", "vps-secret")
        config = json.loads((preparer_vps.WORK / "config.json").read_text(encoding="utf-8"))
        self.assertEqual((config["client_id"], config["client_secret"]), ("vps", "vps-secret"))
        self.assertEqual(config["stations"], [{"id": "s"}])

    def test_stations_of_the_pc_page_go_to_the_server(self):
        db = sqlite3.connect(self.project / "data" / "meteo.db")
        db.execute("CREATE TABLE stations (id TEXT, lat REAL, lon REAL, box REAL, place TEXT)")
        db.execute("CREATE TABLE user_stations (user_id INT, station_id TEXT, name TEXT, slot INT, position INT)")
        db.executemany("INSERT INTO stations VALUES (?, ?, ?, ?, ?)", [("a", 1.0, 2.0, None, "A"), ("b", 3.0, 4.0, 0.05, "B")])
        db.executemany(
            "INSERT INTO user_stations VALUES (?, ?, ?, ?, ?)",
            [(0, "a", "Maison", 2, 1), (0, "b", "Boulot", 0, 0), (5, "a", "Autre", 1, 0)],
        )
        db.commit()
        db.close()
        preparer_vps.prepare_data("vps", "vps-secret")
        config = json.loads((preparer_vps.WORK / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["stations"], [
            {"id": "b", "lat": 3.0, "lon": 4.0, "box": 0.05, "place": "B", "name": "Boulot", "slot": 0},
            {"id": "a", "lat": 1.0, "lon": 2.0, "place": "A", "name": "Maison", "slot": 2},
        ])

    def test_pc_config_is_left_alone(self):
        before = (self.project / "config.json").read_bytes()
        preparer_vps.prepare_data("vps", "vps-secret")
        self.assertEqual((self.project / "config.json").read_bytes(), before)

    def test_measures_are_copied(self):
        preparer_vps.prepare_data("vps", "vps-secret")
        db = sqlite3.connect(preparer_vps.WORK / "meteo.db")
        self.assertEqual(db.execute("SELECT COUNT(*) FROM measures").fetchone()[0], 1)
        db.close()

    def test_token_detection(self):
        preparer_vps.WORK.mkdir()
        self.assertFalse(preparer_vps.has_token())
        (preparer_vps.WORK / "token.json").write_text('{"refresh_token": "r"}', encoding="utf-8")
        self.assertTrue(preparer_vps.has_token())

    def test_every_program_file_exists(self):
        for name in preparer_vps.PROGRAM:
            self.assertTrue((preparer_vps.PROGRAM_FOLDER / name).exists(), name)

    def test_upload_order_ends_by_giving_files_to_the_container(self):
        commands = preparer_vps.upload_commands("me@vps")
        self.assertEqual([c[0] for c in commands], ["ssh", "scp", "scp", "ssh"])
        self.assertTrue(commands[1][-1].endswith("/app/"))
        self.assertTrue(commands[2][-1].endswith("/data/"))
        self.assertIn("1000:1000", commands[3][-1])

    def test_upload_stops_at_the_first_failure(self):
        with mock.patch.object(preparer_vps.subprocess, "run") as run:
            run.return_value.returncode = 1
            self.assertFalse(preparer_vps.upload("me@vps"))
        self.assertEqual(run.call_count, 1)


class Update(unittest.TestCase):
    def test_same_program_files_as_the_installation(self):
        self.assertEqual(mettre_a_jour_vps.PROGRAM, preparer_vps.PROGRAM)

    def test_data_is_never_touched(self):
        for command in mettre_a_jour_vps.commands("me@vps"):
            self.assertNotIn("/data", " ".join(command))

    def test_ends_by_restarting_the_container(self):
        last = mettre_a_jour_vps.commands("me@vps")[-1][-1]
        self.assertIn("1000:1000", last)
        self.assertIn("docker restart meteo-perso", last)

    def test_stops_at_the_first_failure(self):
        with mock.patch.object(mettre_a_jour_vps.subprocess, "run") as run:
            run.return_value.returncode = 1
            self.assertFalse(mettre_a_jour_vps.update("me@vps"))
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
