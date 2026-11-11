"""Pruebas sin cámara. Los temporales se crean dentro del propio proyecto."""

from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing as mp
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import config_manager as config


def _process_update(base, section, values, start):
    config.get_base_dir = lambda: base
    if not start.wait(10):
        raise RuntimeError("La prueba no inició")
    for _ in range(5):
        config.update_config({section: values})


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix="tmp-config-")
        self.base = Path(self.directory.name)
        self.base_patch = patch.object(config, "get_base_dir", return_value=str(self.base))
        self.base_patch.start()

    def tearDown(self):
        self.base_patch.stop()
        self.directory.cleanup()

    def test_read_is_side_effect_free(self):
        self.assertEqual(config.load_config(), config.DEFAULT_CONFIG)
        self.assertEqual(list(self.base.iterdir()), [])

    def test_defaults_are_independent(self):
        first = config.load_config()
        first["eye_contact"]["intensity"] = 99
        self.assertEqual(config.load_config()["eye_contact"]["intensity"], 35)

    def test_invalid_sections_do_not_escape_validation(self):
        result = config.normalize_config({"eye_contact": None, "teleprompter": "bad", "video": []})
        self.assertEqual(result, config.DEFAULT_CONFIG)

    def test_numbers_are_bounded_and_booleans_are_not_numbers(self):
        result = config.normalize_config({
            "eye_contact": {"intensity": True, "enabled": "false"},
            "teleprompter": {"font_size": 999, "opacity": -50, "scroll_speed": float("nan")},
            "video": {"camera_index": float("inf"), "profile": []},
        })
        self.assertEqual(result["eye_contact"], config.DEFAULT_CONFIG["eye_contact"])
        self.assertEqual(result["teleprompter"]["font_size"], 44)
        self.assertEqual(result["teleprompter"]["opacity"], 80)
        self.assertEqual(result["teleprompter"]["scroll_speed"], 3)
        self.assertEqual(result["video"], config.DEFAULT_CONFIG["video"])

    def test_partial_updates_preserve_other_sections(self):
        config.update_config({"eye_contact": {"intensity": 72}})
        config.update_config({"teleprompter": {"font_size": 32}})
        result = config.load_config()
        self.assertEqual(result["eye_contact"]["intensity"], 72)
        self.assertEqual(result["teleprompter"]["font_size"], 32)
        self.assertEqual(list(Path(config.get_data_dir()).glob("*.tmp")), [])

    def test_concurrent_read_modify_write_preserves_both_writers(self):
        def update_eye():
            for value in range(40, 55):
                config.update_config({"eye_contact": {"intensity": value}})

        def update_font():
            for value in range(20, 35):
                config.update_config({"teleprompter": {"font_size": value}})

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [executor.submit(update_eye), executor.submit(update_font)]
            for future in results:
                future.result(timeout=10)
        result = config.load_config()
        self.assertEqual(result["eye_contact"]["intensity"], 54)
        self.assertEqual(result["teleprompter"]["font_size"], 34)

    def test_lock_failure_does_not_write(self):
        with patch.object(config, "file_lock", side_effect=config.ConfigError("ocupado")):
            with self.assertRaises(config.ConfigError):
                config.update_config({"eye_contact": {"intensity": 99}})
        self.assertFalse(Path(config.get_config_path()).exists())

    def test_two_processes_preserve_unrelated_updates(self):
        context = mp.get_context("spawn")
        start = context.Event()
        workers = [
            context.Process(target=_process_update, args=(str(self.base), "eye_contact", {"intensity": 61}, start)),
            context.Process(target=_process_update, args=(str(self.base), "teleprompter", {"font_size": 31}, start)),
        ]
        try:
            for worker in workers:
                worker.start()
            start.set()
            for worker in workers:
                worker.join(timeout=15)
                self.assertEqual(worker.exitcode, 0)
            result = config.load_config()
            self.assertEqual(result["eye_contact"]["intensity"], 61)
            self.assertEqual(result["teleprompter"]["font_size"], 31)
        finally:
            for worker in workers:
                if worker.pid is not None:
                    if worker.is_alive():
                        worker.terminate()
                        worker.join(timeout=2)
                    if not worker.is_alive():
                        worker.close()

    def test_legacy_config_is_read_without_modification(self):
        legacy = self.base / "config.json"
        original = '{"eye_contact":{"intensity":63}}'
        legacy.write_text(original, encoding="utf-8")
        self.assertEqual(config.load_config()["eye_contact"]["intensity"], 63)
        config.update_config({"teleprompter": {"font_size": 30}})
        self.assertEqual(legacy.read_text(encoding="utf-8"), original)
        self.assertEqual(config.load_config()["eye_contact"]["intensity"], 63)

    def test_corrupt_config_preserved_before_replacement(self):
        Path(config.get_data_dir()).mkdir()
        path = Path(config.get_config_path())
        path.write_text("{broken", encoding="utf-8")
        self.assertEqual(config.load_config(), config.DEFAULT_CONFIG)
        self.assertEqual(path.read_text(encoding="utf-8"), "{broken")
        config.update_config({"eye_contact": {"intensity": 20}})
        backups = list(path.parent.glob("config-corrupt-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "{broken")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["eye_contact"]["intensity"], 20)


if __name__ == "__main__":
    unittest.main()
