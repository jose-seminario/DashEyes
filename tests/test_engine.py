import unittest
from unittest.mock import patch
from types import SimpleNamespace

try:
    import cv2
    import numpy as np
    from eye_contact import CANONICAL_FACE_3D, EYES, EyeContactEngine, pose_estimate, pose_gate
except ImportError:
    cv2 = None


@unittest.skipIf(cv2 is None, "OpenCV y NumPy no disponibles")
class EngineTests(unittest.TestCase):
    def _landmarks(self, pupil_down=0):
        landmarks = [SimpleNamespace(x=0.5, y=0.5) for _ in range(478)]
        for center, definition in zip((50, 150), EYES):
            contour, first, second, upper, lower, iris = definition
            for index, angle in zip(contour, np.linspace(0, 2 * np.pi, len(contour), endpoint=False)):
                landmarks[index] = SimpleNamespace(x=(center + 30 * np.cos(angle)) / 200,
                                                   y=(40 + 10 * np.sin(angle)) / 80)
            for index, x, y in ((first, center - 30, 40), (second, center + 30, 40),
                                (upper, center, 30), (lower, center, 50), (iris, center, 40 + pupil_down)):
                landmarks[index] = SimpleNamespace(x=x / 200, y=y / 80)
        return landmarks

    def test_pose_gate_does_not_fold_backward_head(self):
        self.assertEqual(pose_gate([180, 0, 0]), 0)
        self.assertEqual(pose_gate([0, 160, 0]), 0)
        self.assertEqual(pose_gate([0, 0, 30]), 0)
        self.assertEqual(pose_gate([0, 0, 0]), 1)
        self.assertGreater(pose_gate([18, 0, 0]), 0)
        self.assertLess(pose_gate([18, 0, 0]), 1)

    def test_non_finite_pose_is_rejected(self):
        self.assertEqual(pose_gate([float("nan"), 0, 0]), 0)
        self.assertIsNone(pose_estimate(np.full((6, 2), np.nan), 640, 480))

    def test_known_frontal_projection_is_frontal(self):
        camera = np.array([[640, 0, 320], [0, 640, 240], [0, 0, 1]], dtype=np.float64)
        projected, _ = cv2.projectPoints(CANONICAL_FACE_3D, np.zeros(3), np.array([0., 0., 1000.]), camera, np.zeros(4))
        pose = pose_estimate(projected.reshape(-1, 2), 640, 480)
        self.assertIsNotNone(pose)
        self.assertGreater(pose_gate(pose[1]), 0.99)

    def test_failed_pose_is_not_accepted(self):
        with patch("eye_contact.cv2.solvePnP", return_value=(False, None, None)):
            self.assertIsNone(pose_estimate(np.ones((6, 2)), 640, 480))

    def test_disabled_or_zero_intensity_is_exact_original(self):
        image = np.arange(300, dtype=np.uint8).reshape(10, 10, 3)
        for engine in (EyeContactEngine(enabled=False), EyeContactEngine(intensity=0)):
            before = image.copy()
            engine.offsets[:] = 1
            result = engine.process_frame(image, None)
            np.testing.assert_array_equal(result, before)
            np.testing.assert_array_equal(engine.offsets, np.zeros((2, 2)))
            self.assertEqual(engine.current_state, "PAUSED")

    def test_lost_face_resets_tracking(self):
        engine = EyeContactEngine()
        engine.offsets[:] = 1
        frame = np.zeros((20, 20, 3), dtype=np.uint8)
        self.assertIs(engine.process_frame(frame, None), frame)
        self.assertEqual(engine.current_state, "NO_FACE")
        self.assertFalse(engine.offsets.any())

    def test_deformation_does_not_change_pixels_outside_eye(self):
        engine = EyeContactEngine()
        frame = np.zeros((60, 100, 3), dtype=np.uint8)
        frame[26:34, 44:52] = 255
        before = frame.copy()
        contour = np.array([[20, 30], [35, 20], [65, 20], [80, 30], [65, 40], [35, 40]], dtype=np.float32)
        engine._deform_eye(frame, {"contour": contour, "iris": np.array([48, 30]), "width": 60}, np.array([1., -1.]))
        polygon = np.zeros(frame.shape[:2], dtype=np.uint8)
        cv2.fillPoly(polygon, [contour.astype(np.int32)], 1)
        np.testing.assert_array_equal(frame[polygon == 0], before[polygon == 0])
        self.assertTrue(np.any(frame != before))

    def test_reset_calibration_discards_old_reference(self):
        engine = EyeContactEngine()
        engine.baseline = np.ones((2, 2))
        engine.baseline_rotation = np.eye(3)
        engine.reset_calibration()
        self.assertIsNone(engine.baseline)
        self.assertIsNone(engine.baseline_rotation)
        self.assertEqual(engine.current_state, "UNCALIBRATED")

    def test_calibration_and_dead_zone_preserve_natural_gaze(self):
        engine = EyeContactEngine(intensity=100)
        frame = np.full((80, 200, 3), 127, dtype=np.uint8)
        landmarks = self._landmarks()
        with patch("eye_contact.pose_estimate", return_value=(np.eye(3), np.zeros(3))):
            engine.process_frame(frame, landmarks)
            self.assertEqual(engine.current_state, "UNCALIBRATED")
            engine.start_calibration()
            for _ in range(25):
                engine.process_frame(frame, landmarks)
            self.assertIsNotNone(engine.baseline)
            before = frame.copy()
            engine.process_frame(frame, landmarks)
            self.assertEqual(engine.current_state, "NATURAL")
            np.testing.assert_array_equal(frame, before)
            engine.last_time -= 0.1  # Simular un intervalo real entre fotogramas.
            engine.process_frame(frame, self._landmarks(pupil_down=2))
            self.assertEqual(engine.current_state, "CORRECTING")


if __name__ == "__main__":
    unittest.main()
