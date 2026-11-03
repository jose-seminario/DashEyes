"""
video_worker.py - Módulo B: Motor de Video y Contacto Visual en Tiempo Real
Diseñado para operar como Proceso 2 independiente dentro de DashEyes.
"""

import sys
import time
import queue
from multiprocessing import Queue
import cv2
import numpy as np
import mediapipe as mp
import pyvirtualcam

# ==============================================================================
# CONSTANTES ANATÓMICAS DE MEDIAPIPE FACE MESH (478 Landmarks)
# ==============================================================================

# Contornos completos de los párpados
LEFT_EYE_CONTOUR = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246]
RIGHT_EYE_CONTOUR = [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398]

# Puntos clave para cálculo de Eye Aspect Ratio (EAR) - Detección de parpadeo
# Ojo izquierdo: extremos (33, 133), verticales (160, 144) y (158, 153)
LEFT_EAR_POINTS = {'p1': 33, 'p4': 133, 'p2': 160, 'p6': 144, 'p3': 158, 'p5': 153}
# Ojo derecho: extremos (362, 263), verticales (385, 380) y (387, 373)
RIGHT_EAR_POINTS = {'p1': 362, 'p4': 263, 'p2': 385, 'p6': 380, 'p3': 387, 'p5': 373}

# Centros anatómicos de iris (MediaPipe v2 con refine_landmarks=True)
LEFT_IRIS_CENTER = 468
RIGHT_IRIS_CENTER = 473


class EyeContactEngine:
    """
    Motor de procesamiento de visión que implementa aislamiento del iris,
    detección de parpadeo (EAR), desplazamiento cinemático y fusión pluma.
    """
    def __init__(self, vertical_shift_px=5, horizontal_inward_px=1, ear_threshold=0.19, ema_alpha=0.75):
        self.vertical_shift_px = vertical_shift_px
        self.horizontal_inward_px = horizontal_inward_px
        self.ear_threshold = ear_threshold
        self.ema_alpha = ema_alpha  # Coeficiente de suavizado temporal (0: máx estabilidad, 1: sin filtro)
        self.correction_enabled = True

        # Memoria para el filtro de Media Móvil Exponencial (EMA)
        self.prev_left_offset = np.array([0.0, 0.0], dtype=np.float32)
        self.prev_right_offset = np.array([0.0, 0.0], dtype=np.float32)

    @staticmethod
    def calculate_ear(landmarks, ear_dict, img_w, img_h) -> float:
        """
        Calcula el Eye Aspect Ratio (EAR) para determinar si el ojo está abierto o parpadeando.
        EAR = (||p2 - p6|| + ||p3 - p5||) / (2 * ||p1 - p4||)
        """
        def to_coord(idx):
            return np.array([landmarks[idx].x * img_w, landmarks[idx].y * img_h], dtype=np.float32)

        p1 = to_coord(ear_dict['p1'])
        p4 = to_coord(ear_dict['p4'])
        p2 = to_coord(ear_dict['p2'])
        p6 = to_coord(ear_dict['p6'])
        p3 = to_coord(ear_dict['p3'])
        p5 = to_coord(ear_dict['p5'])

        vert_dist_1 = np.linalg.norm(p2 - p6)
        vert_dist_2 = np.linalg.norm(p3 - p5)
        horiz_dist = np.linalg.norm(p1 - p4)

        if horiz_dist == 0:
            return 0.0

        ear = (vert_dist_1 + vert_dist_2) / (2.0 * horiz_dist)
        return float(ear)

    @staticmethod
    def get_square_bbox(landmarks, indices, img_w, img_h, padding_ratio=0.35):
        """
        Calcula un delimitador estrictamente cuadrado alrededor del ojo
        para mantener la proporción espacial sin distorsiones.
        """
        pts = np.array([[landmarks[idx].x * img_w, landmarks[idx].y * img_h] for idx in indices], dtype=np.float32)
        min_x, min_y = pts.min(axis=0)
        max_x, max_y = pts.max(axis=0)

        cx = (min_x + max_x) / 2.0
        cy = (min_y + max_y) / 2.0

        w = max_x - min_x
        h = max_y - min_y
        size = max(w, h) * (1.0 + padding_ratio * 2)

        x1 = int(round(cx - size / 2.0))
        y1 = int(round(cy - size / 2.0))
        x2 = int(round(cx + size / 2.0))
        y2 = int(round(cy + size / 2.0))

        # Delimitar dentro del encuadre del fotograma
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(img_w, x2), min(img_h, y2)

        return x1, y1, x2, y2

    @staticmethod
    def create_feathered_mask(h, w, blur_kernel=(15, 15)) -> np.ndarray:
        """Crea una máscara elíptica de difusión gaussiana para evitar costuras cuadradas."""
        mask = np.zeros((h, w), dtype=np.float32)
        center = (w // 2, h // 2)
        axes = (int(w * 0.44), int(h * 0.38))
        cv2.ellipse(mask, center, axes, 0, 0, 360, 1.0, -1)
        mask = cv2.GaussianBlur(mask, blur_kernel, 0)
        return mask[..., np.newaxis]

    def correct_iris_patch(self, eye_crop: np.ndarray, iris_center_local, is_left: bool) -> np.ndarray:
        """
        Aísla el disco del iris y desplaza su textura hacia la lente de la cámara
        utilizando una traslación afín y una máscara pluma radial.
        """
        h, w = eye_crop.shape[:2]
        ix, iy = int(round(iris_center_local[0])), int(round(iris_center_local[1]))

        # El radio anatómico del iris representa aproximadamente el 16% del ancho del ojo
        radius = max(3, int(w * 0.16))

        # Vector de compensación (hacia arriba = -Y; hacia la nariz/cámara = X convergente)
        dx = self.horizontal_inward_px if is_left else -self.horizontal_inward_px
        dy = -self.vertical_shift_px
        raw_offset = np.array([dx, dy], dtype=np.float32)

        # Suavizado temporal EMA sobre el vector de desplazamiento
        if is_left:
            smoothed_offset = self.ema_alpha * raw_offset + (1.0 - self.ema_alpha) * self.prev_left_offset
            self.prev_left_offset = smoothed_offset
        else:
            smoothed_offset = self.ema_alpha * raw_offset + (1.0 - self.ema_alpha) * self.prev_right_offset
            self.prev_right_offset = smoothed_offset

        # 1. Crear máscara radial del iris con bordes difuminados
        iris_mask = np.zeros((h, w), dtype=np.float32)
        cv2.circle(iris_mask, (ix, iy), radius, 1.0, -1)
        iris_mask = cv2.GaussianBlur(iris_mask, (9, 9), 0)[..., np.newaxis]

        # 2. Desplazamiento afín del recorte
        M = np.float32([
            [1.0, 0.0, smoothed_offset[0]],
            [0.0, 1.0, smoothed_offset[1]]
        ])
        shifted_crop = cv2.warpAffine(eye_crop, M, (w, h), borderMode=cv2.BORDER_REFLECT)

        # 3. Fusión selectiva: solo se desplaza el interior del iris, párpados y piel quedan estáticos
        corrected_patch = (shifted_crop * iris_mask + eye_crop * (1.0 - iris_mask)).astype(np.uint8)
        return corrected_patch

    def process_frame(self, frame: np.ndarray, landmarks, img_w, img_h) -> np.ndarray:
        """Aplica las compuertas lógicas y procesa ambos ojos si no hay parpadeo."""
        if not self.correction_enabled:
            return frame

        # Compuerta 1: Comprobación de parpadeo mediante EAR
        left_ear = self.calculate_ear(landmarks, LEFT_EAR_POINTS, img_w, img_h)
        right_ear = self.calculate_ear(landmarks, RIGHT_EAR_POINTS, img_w, img_h)

        # Delimitadores de ambos ojos
        lx1, ly1, lx2, ly2 = self.get_square_bbox(landmarks, LEFT_EYE_CONTOUR, img_w, img_h)
        rx1, ry1, rx2, ry2 = self.get_square_bbox(landmarks, RIGHT_EYE_CONTOUR, img_w, img_h)

        # Procesar ojo izquierdo solo si está abierto
        if left_ear > self.ear_threshold and (lx2 - lx1 > 10) and (ly2 - ly1 > 10):
            left_crop = frame[ly1:ly2, lx1:lx2]
            l_iris_global = (landmarks[LEFT_IRIS_CENTER].x * img_w, landmarks[LEFT_IRIS_CENTER].y * img_h)
            l_iris_local = (l_iris_global[0] - lx1, l_iris_global[1] - ly1)

            corr_left = self.correct_iris_patch(left_crop, l_iris_local, is_left=True)
            mask_l = self.create_feathered_mask(ly2 - ly1, lx2 - lx1)
            frame[ly1:ly2, lx1:lx2] = (corr_left * mask_l + left_crop * (1.0 - mask_l)).astype(np.uint8)

        # Procesar ojo derecho solo si está abierto
        if right_ear > self.ear_threshold and (rx2 - rx1 > 10) and (ry2 - ry1 > 10):
            right_crop = frame[ry1:ry2, rx1:rx2]
            r_iris_global = (landmarks[RIGHT_IRIS_CENTER].x * img_w, landmarks[RIGHT_IRIS_CENTER].y * img_h)
            r_iris_local = (r_iris_global[0] - rx1, r_iris_global[1] - ry1)

            corr_right = self.correct_iris_patch(right_crop, r_iris_local, is_left=False)
            mask_r = self.create_feathered_mask(ry2 - ry1, rx2 - rx1)
            frame[ry1:ry2, rx1:rx2] = (corr_right * mask_r + right_crop * (1.0 - mask_r)).astype(np.uint8)

        return frame


def run_video_worker(ipc_queue: Queue = None, cam_index=0, width=1280, height=720, fps=30):
    """
    Punto de entrada exportable del Proceso 2.
    Lee comandos de la cola IPC opcional y transmite a pyvirtualcam.
    """
    cap = cv2.VideoCapture(cam_index)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)

    if not cap.isOpened():
        print(f"[ERROR] No se pudo acceder a la webcam física en índice {cam_index}.")
        return

    # Detector FaceMesh refinado para landmarks de iris
    mp_face_mesh = mp.solutions.face_mesh
    face_mesh = mp_face_mesh.FaceMesh(
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5
    )

    engine = EyeContactEngine(vertical_shift_px=5, horizontal_inward_px=1)

    print(f"[VIDEO WORKER] Iniciando pyvirtualcam ({width}x{height} @ {fps} FPS)...")
    try:
        with pyvirtualcam.Camera(width=width, height=height, fps=fps, fmt=pyvirtualcam.PixelFormat.BGR) as vcam:
            print(f"[VIDEO WORKER] Dispositivo virtual activo: {vcam.device}")
            print("[VIDEO WORKER] Transmitiendo video. Presiona Ctrl+C para detener.\n")

            while True:
                # Comprobar mensajes de control desde la cola IPC (Fase 3)
                if ipc_queue is not None:
                    try:
                        msg = ipc_queue.get_nowait()
                        if isinstance(msg, dict):
                            cmd = msg.get("cmd")
                            if cmd == "STOP":
                                print("[VIDEO WORKER] Señal de terminación recibida vía IPC.")
                                break
                            elif cmd == "TOGGLE_CORRECTION":
                                engine.correction_enabled = msg.get("enabled", True)
                                print(f"[VIDEO WORKER] Corrección de mirada: {engine.correction_enabled}")
                            elif cmd == "SET_SHIFT":
                                engine.vertical_shift_px = msg.get("shift_px", 5)
                                print(f"[VIDEO WORKER] Compensación vertical ajustada a: {engine.vertical_shift_px}px")
                    except queue.Empty:
                        pass

                ret, frame = cap.read()
                if not ret:
                    time.sleep(0.01)
                    continue

                # MediaPipe requiere formato RGB
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                results = face_mesh.process(rgb_frame)

                if results.multi_face_landmarks:
                    landmarks = results.multi_face_landmarks[0].landmark
                    frame = engine.process_frame(frame, landmarks, width, height)

                # Envío de fotograma a la cámara virtual
                vcam.send(frame)
                vcam.sleep_until_next_frame()

    except KeyboardInterrupt:
        print("\n[VIDEO WORKER] Detenido por el usuario.")
    except Exception as e:
        print(f"\n[VIDEO WORKER ERROR] {e}")
        print("Tip: Verifica que OBS Studio o el driver OBS Virtual Camera esté instalado en Windows.")
    finally:
        cap.release()
        face_mesh.close()
        print("[VIDEO WORKER] Recursos liberados con éxito.")


if __name__ == "__main__":
    run_video_worker()
