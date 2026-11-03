"""
video_worker.py - Módulo B: Motor de Video y Contacto Visual Blindado (V2.0 Final)
BUG-03 Corregido: Registro explícito de excepciones y trazado de fallos sin supresión ciega.
"""

import sys
import time
import queue
import signal
import threading
import traceback
from multiprocessing import Queue
from multiprocessing.connection import Connection
import cv2
import numpy as np
import mediapipe as mp
import pyvirtualcam

# ==============================================================================
# CONSTANTES ANATÓMICAS (MediaPipe Face Mesh 478 Landmarks)
# ==============================================================================

LEFT_EYE_CONTOUR = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246]
RIGHT_EYE_CONTOUR = [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398]

LEFT_EAR_POINTS = {'p1': 33, 'p4': 133, 'p2': 160, 'p6': 144, 'p3': 158, 'p5': 153}
RIGHT_EAR_POINTS = {'p1': 362, 'p4': 263, 'p2': 385, 'p6': 380, 'p3': 387, 'p5': 373}

LEFT_IRIS_CENTER = 468
RIGHT_IRIS_CENTER = 473

CANONICAL_FACE_3D = np.array([
    (0.0, 0.0, 0.0),
    (0.0, -330.0, -65.0),
    (-225.0, 170.0, -135.0),
    (225.0, 170.0, -135.0),
    (-150.0, -150.0, -125.0),
    (150.0, -150.0, -125.0)
], dtype=np.float64)


# ==============================================================================
# HILO DE CAPTURA CON CONTADOR DE FRAME ID ATÓMICO
# ==============================================================================

class ThreadedCameraCapture:
    def __init__(self, cam_index=0, width=1280, height=720, fps=30):
        self.cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            self.cap = cv2.VideoCapture(cam_index)

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)

        self.lock = threading.Lock()
        self.stopped = False
        self.latest_frame = None
        self.ret = False
        self.frame_id = 0

        self.ret, self.latest_frame = self.cap.read()
        if self.ret:
            self.frame_id = 1

        self.thread = threading.Thread(target=self._capture_loop, daemon=True, name="CameraGrabberThread")
        self.thread.start()

    def _capture_loop(self):
        while not self.stopped:
            ret, frame = self.cap.read()
            if ret:
                with self.lock:
                    self.latest_frame = frame
                    self.ret = True
                    self.frame_id += 1
            else:
                time.sleep(0.005)

    def read(self):
        with self.lock:
            if self.latest_frame is None or not self.ret:
                return False, None, self.frame_id
            return True, self.latest_frame.copy(), self.frame_id

    def stop(self):
        self.stopped = True
        if self.thread.is_alive():
            self.thread.join(timeout=1.0)
        if self.cap.isOpened():
            self.cap.release()


def find_working_camera(max_indices_to_test=4) -> int:
    for idx in range(max_indices_to_test):
        test_cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
        if not test_cap.isOpened():
            test_cap = cv2.VideoCapture(idx)

        if test_cap.isOpened():
            ret, _ = test_cap.read()
            test_cap.release()
            if ret:
                return idx
    return 0


# ==============================================================================
# MOTOR DE VISIÓN CON RESET ADAPTATIVO Y HEAD POSE GATING
# ==============================================================================

class EyeContactEngine:
    def __init__(self, vertical_shift_px=5, horizontal_inward_px=1, ear_threshold=0.19, ema_alpha=0.75):
        self.vertical_shift_px = vertical_shift_px
        self.horizontal_inward_px = horizontal_inward_px
        self.ear_threshold = ear_threshold
        self.ema_alpha = ema_alpha
        self.correction_enabled = True

        self.prev_left_offset = np.array([0.0, 0.0], dtype=np.float32)
        self.prev_right_offset = np.array([0.0, 0.0], dtype=np.float32)

        self.last_left_iris_center = None
        self.last_right_iris_center = None
        self.jump_threshold_px = 20.0

    @staticmethod
    def calculate_ear(landmarks, ear_dict, img_w, img_h) -> float:
        def to_coord(idx):
            return np.array([landmarks[idx].x * img_w, landmarks[idx].y * img_h], dtype=np.float32)

        p1, p4 = to_coord(ear_dict['p1']), to_coord(ear_dict['p4'])
        p2, p6 = to_coord(ear_dict['p2']), to_coord(ear_dict['p6'])
        p3, p5 = to_coord(ear_dict['p3']), to_coord(ear_dict['p5'])

        vert_1 = np.linalg.norm(p2 - p6)
        vert_2 = np.linalg.norm(p3 - p5)
        horiz = np.linalg.norm(p1 - p4)

        if horiz == 0:
            return 0.0
        return float((vert_1 + vert_2) / (2.0 * horiz))

    @staticmethod
    def calculate_head_pose_gating(landmarks, img_w, img_h) -> float:
        image_points = np.array([
            (landmarks[1].x * img_w, landmarks[1].y * img_h),
            (landmarks[152].x * img_w, landmarks[152].y * img_h),
            (landmarks[33].x * img_w, landmarks[33].y * img_h),
            (landmarks[263].x * img_w, landmarks[263].y * img_h),
            (landmarks[61].x * img_w, landmarks[61].y * img_h),
            (landmarks[291].x * img_w, landmarks[291].y * img_h)
        ], dtype=np.float64)

        focal_length = img_w
        center = (img_w / 2.0, img_h / 2.0)
        camera_matrix = np.array([
            [focal_length, 0.0, center[0]],
            [0.0, focal_length, center[1]],
            [0.0, 0.0, 1.0]
        ], dtype=np.float64)
        dist_coeffs = np.zeros((4, 1))

        success, rvec, _ = cv2.solvePnP(
            CANONICAL_FACE_3D, image_points, camera_matrix, dist_coeffs, flags=cv2.SOLVEPNP_ITERATIVE
        )
        if not success:
            return 1.0

        rmat, _ = cv2.Rodrigues(rvec)
        angles, _, _, _, _, _ = cv2.RQDecomp3x3(rmat)
        pitch, yaw = abs(angles[0]), abs(angles[1])

        max_angle = max(pitch, yaw)
        if max_angle >= 22.0:
            return 0.0
        elif max_angle <= 15.0:
            return 1.0
        else:
            return float((22.0 - max_angle) / (22.0 - 15.0))

    @staticmethod
    def get_square_bbox(landmarks, indices, img_w, img_h, padding_ratio=0.35):
        pts = np.array([[landmarks[idx].x * img_w, landmarks[idx].y * img_h] for idx in indices], dtype=np.float32)
        min_x, min_y = pts.min(axis=0)
        max_x, max_y = pts.max(axis=0)

        cx, cy = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
        w, h = max_x - min_x, max_y - min_y
        size = max(w, h) * (1.0 + padding_ratio * 2)

        x1 = max(0, int(round(cx - size / 2.0)))
        y1 = max(0, int(round(cy - size / 2.0)))
        x2 = min(img_w, int(round(cx + size / 2.0)))
        y2 = min(img_h, int(round(cy + size / 2.0)))
        return x1, y1, x2, y2

    @staticmethod
    def create_feathered_mask(h, w, blur_kernel=(15, 15)) -> np.ndarray:
        mask = np.zeros((h, w), dtype=np.float32)
        center = (w // 2, h // 2)
        axes = (int(w * 0.44), int(h * 0.38))
        cv2.ellipse(mask, center, axes, 0, 0, 360, 1.0, -1)
        mask = cv2.GaussianBlur(mask, blur_kernel, 0)
        return mask[..., np.newaxis]

    def correct_iris_patch(self, eye_crop: np.ndarray, iris_center_local, iris_center_global, is_left: bool, gating_factor: float) -> np.ndarray:
        h, w = eye_crop.shape[:2]
        ix, iy = int(round(iris_center_local[0])), int(round(iris_center_local[1]))
        radius = max(3, int(w * 0.16))

        dx = (self.horizontal_inward_px if is_left else -self.horizontal_inward_px) * gating_factor
        dy = (-self.vertical_shift_px) * gating_factor
        raw_offset = np.array([dx, dy], dtype=np.float32)

        last_center = self.last_left_iris_center if is_left else self.last_right_iris_center
        current_center = np.array(iris_center_global, dtype=np.float32)

        if last_center is not None:
            spatial_jump = np.linalg.norm(current_center - last_center)
            if spatial_jump > self.jump_threshold_px:
                if is_left:
                    self.prev_left_offset = raw_offset.copy()
                else:
                    self.prev_right_offset = raw_offset.copy()

        if is_left:
            self.last_left_iris_center = current_center
            smoothed_offset = self.ema_alpha * raw_offset + (1.0 - self.ema_alpha) * self.prev_left_offset
            self.prev_left_offset = smoothed_offset
        else:
            self.last_right_iris_center = current_center
            smoothed_offset = self.ema_alpha * raw_offset + (1.0 - self.ema_alpha) * self.prev_right_offset
            self.prev_right_offset = smoothed_offset

        iris_mask = np.zeros((h, w), dtype=np.float32)
        cv2.circle(iris_mask, (ix, iy), radius, 1.0, -1)
        iris_mask = cv2.GaussianBlur(iris_mask, (9, 9), 0)[..., np.newaxis]

        M = np.float32([[1.0, 0.0, smoothed_offset[0]], [0.0, 1.0, smoothed_offset[1]]])
        shifted_crop = cv2.warpAffine(eye_crop, M, (w, h), borderMode=cv2.BORDER_REFLECT)

        corrected_patch = (shifted_crop * iris_mask + eye_crop * (1.0 - iris_mask)).astype(np.uint8)
        return corrected_patch

    def process_frame(self, frame: np.ndarray, landmarks, img_w, img_h) -> np.ndarray:
        if not self.correction_enabled:
            return frame

        gating_factor = self.calculate_head_pose_gating(landmarks, img_w, img_h)
        if gating_factor <= 0.01:
            return frame

        left_ear = self.calculate_ear(landmarks, LEFT_EAR_POINTS, img_w, img_h)
        right_ear = self.calculate_ear(landmarks, RIGHT_EAR_POINTS, img_w, img_h)

        lx1, ly1, lx2, ly2 = self.get_square_bbox(landmarks, LEFT_EYE_CONTOUR, img_w, img_h)
        rx1, ry1, rx2, ry2 = self.get_square_bbox(landmarks, RIGHT_EYE_CONTOUR, img_w, img_h)

        if left_ear > self.ear_threshold and (lx2 - lx1 > 10) and (ly2 - ly1 > 10):
            left_crop = frame[ly1:ly2, lx1:lx2]
            l_iris_global = (landmarks[LEFT_IRIS_CENTER].x * img_w, landmarks[LEFT_IRIS_CENTER].y * img_h)
            l_iris_local = (l_iris_global[0] - lx1, l_iris_global[1] - ly1)

            corr_left = self.correct_iris_patch(left_crop, l_iris_local, l_iris_global, is_left=True, gating_factor=gating_factor)
            mask_l = self.create_feathered_mask(ly2 - ly1, lx2 - lx1)
            frame[ly1:ly2, lx1:lx2] = (corr_left * mask_l + left_crop * (1.0 - mask_l)).astype(np.uint8)

        if right_ear > self.ear_threshold and (rx2 - rx1 > 10) and (ry2 - ry1 > 10):
            right_crop = frame[ry1:ry2, rx1:rx2]
            r_iris_global = (landmarks[RIGHT_IRIS_CENTER].x * img_w, landmarks[RIGHT_IRIS_CENTER].y * img_h)
            r_iris_local = (r_iris_global[0] - rx1, r_iris_global[1] - ry1)

            corr_right = self.correct_iris_patch(right_crop, r_iris_local, r_iris_global, is_left=False, gating_factor=gating_factor)
            mask_r = self.create_feathered_mask(ry2 - ry1, rx2 - rx1)
            frame[ry1:ry2, rx1:rx2] = (corr_right * mask_r + right_crop * (1.0 - mask_r)).astype(np.uint8)

        return frame


# ==============================================================================
# BUCLE PRINCIPAL CON DIAGNÓSTICO DE EXCEPCIONES Y PROTOCOLO LIMPIO
# ==============================================================================

def run_video_worker(cmd_pipe=None, ack_pipe=None, ipc_queue: Queue = None, width=1280, height=720, fps=30):
    stop_event = threading.Event()

    def signal_handler(signum, frame):
        stop_event.set()

    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    except (ValueError, AttributeError):
        pass

    cam_index = find_working_camera()
    grabber = ThreadedCameraCapture(cam_index=cam_index, width=width, height=height, fps=fps)

    mp_face_mesh = mp.solutions.face_mesh
    face_mesh = mp_face_mesh.FaceMesh(
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5
    )

    engine = EyeContactEngine(vertical_shift_px=5, horizontal_inward_px=1)
    last_processed_frame_id = -1
    vcam_instance = None

    try:
        with pyvirtualcam.Camera(width=width, height=height, fps=fps, fmt=pyvirtualcam.PixelFormat.BGR) as vcam:
            vcam_instance = vcam

            while not stop_event.is_set():
                if cmd_pipe is not None:
                    try:
                        if cmd_pipe.poll():
                            msg = cmd_pipe.recv()
                            if isinstance(msg, dict):
                                cmd = msg.get("cmd")
                                if cmd == "STOP":
                                    stop_event.set()
                                    break
                                elif cmd == "TOGGLE_CORRECTION":
                                    engine.correction_enabled = msg.get("enabled", True)
                                elif cmd == "SET_SHIFT":
                                    engine.vertical_shift_px = msg.get("shift_px", 5)
                    except Exception:
                        pass

                elif ipc_queue is not None:
                    try:
                        msg = ipc_queue.get_nowait()
                        if isinstance(msg, dict):
                            cmd = msg.get("cmd")
                            if cmd == "STOP":
                                stop_event.set()
                                break
                            elif cmd == "TOGGLE_CORRECTION":
                                engine.correction_enabled = msg.get("enabled", True)
                            elif cmd == "SET_SHIFT":
                                engine.vertical_shift_px = msg.get("shift_px", 5)
                    except queue.Empty:
                        pass

                ret, frame, current_frame_id = grabber.read()
                if not ret or frame is None:
                    time.sleep(0.005)
                    continue

                if current_frame_id == last_processed_frame_id:
                    time.sleep(0.001)
                    continue

                last_processed_frame_id = current_frame_id

                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                results = face_mesh.process(rgb_frame)

                if results.multi_face_landmarks:
                    landmarks = results.multi_face_landmarks[0].landmark
                    frame = engine.process_frame(frame, landmarks, width, height)

                vcam.send(frame)
                vcam.sleep_until_next_frame()

    except BaseException as critical_err:
        # BUG-03 Corregido: Trazado explícito de errores sin supresión ciega
        if not isinstance(critical_err, (KeyboardInterrupt, SystemExit)):
            print(f"[VIDEO WORKER ERROR CRÍTICO] {critical_err}", file=sys.stderr)
            traceback.print_exc()

    finally:
        grabber.stop()
        face_mesh.close()
        if vcam_instance is not None:
            try:
                vcam_instance.close()
            except Exception:
                pass

        if ack_pipe is not None:
            try:
                ack_pipe.send({"cmd": "ACK_STOP"})
                ack_pipe.close()
            except Exception:
                pass

        if ipc_queue is not None:
            try:
                ipc_queue.put_nowait({"cmd": "ACK_STOP"})
                ipc_queue.cancel_join_thread()
            except Exception:
                pass


if __name__ == "__main__":
    run_video_worker()
