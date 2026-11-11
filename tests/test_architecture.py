"""Regresiones de separación de dependencias y arranque; sin lanzar procesos."""

import ast
from pathlib import Path
import unittest
from unittest.mock import patch

import main

ROOT = Path(__file__).resolve().parents[1]


class ArchitectureTests(unittest.TestCase):
    def test_orchestrator_has_no_top_level_heavy_imports(self):
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        modules = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                modules.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.append(node.module.split(".")[0])
        for name in ("cv2", "numpy", "mediapipe", "pyvirtualcam", "PyQt6", "video_worker", "teleprompter_ui"):
            self.assertNotIn(name, modules)

    def test_modes_are_explicit_and_unknown_options_fail(self):
        self.assertEqual(main.parse_arguments([]).mode, "teleprompter")
        self.assertEqual(main.parse_arguments(["--mode", "eye_contact", "--camera", "2"]).camera, 2)
        with self.assertRaises(SystemExit), patch("sys.stderr"):
            main.parse_arguments(["--eye-contact-enabled", "7"])

    def test_diagnostics_failure_has_nonzero_exit_code(self):
        with patch("diagnostics.diagnose", return_value={"type": "DIAGNOSTICS", "packages_ok": False}), patch.object(main, "emit"):
            self.assertEqual(main.main(["--diagnose"]), 1)

    def test_packaging_has_single_entry(self):
        tree = ast.parse((ROOT / "pyinstaller.spec").read_text(encoding="utf-8"))
        analyses = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Analysis"]
        self.assertEqual(len(analyses), 1)
        self.assertEqual(ast.literal_eval(analyses[0].args[0]), ["dashboard.py"])

    def test_packaging_collects_mediapipe_native_libraries(self):
        tree = ast.parse((ROOT / "pyinstaller.spec").read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
        self.assertTrue(any(node.func.id == "collect_dynamic_libs" and node.args and ast.literal_eval(node.args[0]) == "mediapipe" for node in calls))
        self.assertTrue(any(node.func.id == "collect_delvewheel_libs_directory" for node in calls))
        analysis = next(node for node in calls if node.func.id == "Analysis")
        hooks = next(keyword.value for keyword in analysis.keywords if keyword.arg == "runtime_hooks")
        self.assertIn("pyi_rth_native_paths.py", ast.literal_eval(hooks))

    def test_python_sources_parse(self):
        for path in ROOT.rglob("*.py"):
            if any(part in {".git", ".dasheyes", "build", "dist", ".venv"} for part in path.parts):
                continue
            with self.subTest(path=path.name):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


if __name__ == "__main__":
    unittest.main()
