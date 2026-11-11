"""Pruebas offscreen, sin abrir webcam ni ejecutar diagnósticos del entorno."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    from PyQt6.QtWidgets import QApplication
    from teleprompter_ui import InvisibleTeleprompter
except ImportError:
    QApplication = None

import config_manager


@unittest.skipIf(QApplication is None, "PyQt6 no disponible")
class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix="tmp-ui-")
        self.base_patch = patch.object(config_manager, "get_base_dir", return_value=self.directory.name)
        self.base_patch.start()
        self.window = InvisibleTeleprompter()

    def tearDown(self):
        self.window.close()
        deadline = time.monotonic() + 4
        while self.window.writer.isRunning() and time.monotonic() < deadline:
            self.app.processEvents()
            self.window.writer.wait(20)
        self.app.processEvents()
        self.assertFalse(self.window.writer.isRunning(), "El escritor no cerró")
        self.base_patch.stop()
        self.directory.cleanup()

    def test_reading_mode_has_no_camera_controls(self):
        self.assertFalse(self.window.eye_mode)
        self.assertIn("sin cámara", self.window.led_badge.text())
        self.assertFalse(hasattr(self.window, "eye_slider"))

    def test_pause_and_speed_have_visible_controls(self):
        self.assertTrue(self.window.is_paused)
        self.window._toggle_pause()
        self.assertFalse(self.window.is_paused)
        self.window._change_speed(1)
        self.assertEqual(self.window.scroll_speed, 4)

    def test_unreadable_script_does_not_replace_text(self):
        before = self.window.text_editor.toPlainText()
        with patch("teleprompter_ui.QMessageBox.warning") as warning:
            self.window._load_script(str(Path(self.directory.name) / "missing.txt"))
        warning.assert_called_once()
        self.assertEqual(self.window.text_editor.toPlainText(), before)


if __name__ == "__main__":
    unittest.main()
