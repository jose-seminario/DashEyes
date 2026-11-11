import threading
import time
import unittest
from unittest.mock import patch

try:
    import numpy as np
    from video_worker import ThreadedCameraCapture
except ImportError:
    ThreadedCameraCapture = None


@unittest.skipIf(ThreadedCameraCapture is None, "OpenCV y NumPy no disponibles")
class CaptureTests(unittest.TestCase):
    def test_capture_is_owned_and_released_by_same_thread(self):
        calls = []

        class FakeCapture:
            def __init__(self, *_):
                calls.append(("open", threading.get_ident()))

            def isOpened(self):
                return True

            def set(self, *_):
                return True

            def read(self):
                time.sleep(0.01)
                calls.append(("read", threading.get_ident()))
                return True, np.zeros((20, 30, 3), dtype=np.uint8)

            def release(self):
                calls.append(("release", threading.get_ident()))

        with patch("video_worker.cv2.VideoCapture", FakeCapture):
            capture = ThreadedCameraCapture()
            try:
                ok, frame, identifier = capture.read(timeout=1)
                self.assertTrue(ok)
                self.assertEqual(frame.shape, (20, 30, 3))
                self.assertGreater(identifier, 0)
            finally:
                self.assertTrue(capture.stop())
        identifiers = {identifier for _, identifier in calls}
        self.assertEqual(len(identifiers), 1)
        self.assertNotIn(threading.get_ident(), identifiers)
        self.assertEqual(calls[-1][0], "release")


if __name__ == "__main__":
    unittest.main()
