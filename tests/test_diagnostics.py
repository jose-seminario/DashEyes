"""El diagnóstico de CI no necesita abrir una cámara física ni virtual."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import diagnostics
import main


class DiagnosticsTests(unittest.TestCase):
    def _module(self, name):
        if name == "mediapipe":
            return SimpleNamespace(__version__="test", solutions=SimpleNamespace(
                face_mesh=SimpleNamespace(FaceMesh=object),
            ))
        if name == "pyvirtualcam":
            def unexpected_camera(**_):
                raise AssertionError("El diagnóstico sin cámara no debe abrir dispositivos")
            return SimpleNamespace(__version__="test", Camera=unexpected_camera)
        return SimpleNamespace(__version__="test")

    def test_native_imports_precede_qt(self):
        with patch("diagnostics.importlib.import_module", side_effect=self._module) as imports:
            report = diagnostics.diagnose(check_camera=False)
        names = [call.args[0] for call in imports.call_args_list]
        self.assertLess(names.index("mediapipe"), names.index("PyQt6.QtWidgets"))
        self.assertLess(names.index("cv2"), names.index("mediapipe"))
        self.assertTrue(report["packages_ok"])
        self.assertIn("omitida", report["obs_details"])

    def test_native_dll_failure_remains_a_failure(self):
        def import_module(name):
            if name == "mediapipe":
                raise ImportError("DLL initialization failed")
            return self._module(name)
        with patch("diagnostics.importlib.import_module", side_effect=import_module):
            report = diagnostics.diagnose(check_camera=False)
        self.assertFalse(report["packages_ok"])
        self.assertIn(["mediapipe", False, "DLL initialization failed"], report["packages_details"])

    def test_cli_explicitly_disables_camera_check(self):
        with patch("diagnostics.diagnose", return_value={"packages_ok": True}) as diagnose, patch.object(main, "emit"):
            self.assertEqual(main.main(["--diagnose", "--no-camera"]), 0)
        diagnose.assert_called_once_with(check_camera=False, native_first=False)

    def test_comparison_imports_mediapipe_first_when_requested(self):
        with patch("diagnostics.importlib.import_module", side_effect=self._module) as imports:
            report = diagnostics.diagnose(check_camera=False, native_first=True)
        self.assertEqual(imports.call_args_list[0].args[0], "mediapipe")
        self.assertTrue(report["packages_ok"])


if __name__ == "__main__":
    unittest.main()
