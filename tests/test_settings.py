import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from PyQt6.QtWidgets import QApplication
    from settings_writer import SettingsWriter
except ImportError:
    QApplication = None

import config_manager


@unittest.skipIf(QApplication is None, "PyQt6 no disponible")
class SettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix="tmp-settings-")
        self.base_patch = patch.object(config_manager, "get_base_dir", return_value=self.directory.name)
        self.base_patch.start()
        self.writer = SettingsWriter()

    def tearDown(self):
        self.writer.finish()
        self.assertTrue(self.writer.wait(4000), "El guardado no terminó")
        self.base_patch.stop()
        self.directory.cleanup()

    def test_finish_drains_and_coalesces_all_pending_changes(self):
        for value in range(16, 40):
            self.writer.submit({"teleprompter": {"font_size": value}})
        self.writer.submit({"eye_contact": {"intensity": 27}})
        self.assertFalse(self.writer.is_idle())
        self.writer.finish()
        with patch("settings_writer.config_manager.update_config", wraps=config_manager.update_config) as save:
            self.writer.start()
            self.assertTrue(self.writer.wait(4000))
            self.assertEqual(save.call_count, 1)
        self.assertTrue(self.writer.is_idle())
        result = config_manager.load_config()
        self.assertEqual(result["teleprompter"]["font_size"], 39)
        self.assertEqual(result["eye_contact"]["intensity"], 27)
        self.assertFalse(self.writer.submit({"eye_contact": {"intensity": 99}}))


if __name__ == "__main__":
    unittest.main()
