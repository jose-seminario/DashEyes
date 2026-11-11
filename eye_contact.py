"""Corrección ocular conservadora en CPU; no es reconstrucción generativa.

Se calibra por sesión. Los límites son relativos al ojo, no a la resolución.
Nunca se deforma fuera del contorno ocular ni se corrige una pose no fiable.
"""

import math
import time

import cv2
import numpy as np

LEFT_EYE_CONTOUR = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246]
RIGHT_EYE_CONTOUR = [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398]
EYES = [(LEFT_EYE_CONTOUR, 33, 133, 159, 145, 468), (RIGHT_EYE_CONTOUR, 362, 263, 386, 374, 473)]
POSE_INDICES = [1, 152, 33, 263, 61, 291]
# X derecha, Y abajo, Z hacia el interior de la cara respecto a la nariz.
CANONICAL_FACE_3D = np.array([
    (0, 0, 0), (0, 330, 65), (-225, -170, 135),
    (225, -170, 135), (-150, 150, 125), (150, 150, 125),
], dtype=np.float64)


def pose_estimate(points, width, height):
    """Retorna rotación y ángulos, o None. Una estimación fallida nunca habilita el efecto."""
    image = np.asarray(points, dtype=np.float64)
    if image.shape != (6, 2) or not np.isfinite(image).all():
        return None
    camera = np.array([[width, 0, width / 2], [0, width, height / 2], [0, 0, 1]], dtype=np.float64)
    try:
        ok, vector, translation = cv2.solvePnP(
            CANONICAL_FACE_3D, image, camera, np.zeros(4), flags=cv2.SOLVEPNP_ITERATIVE,
        )
        if not ok or not np.isfinite(vector).all() or not np.isfinite(translation).all():
            return None
        rotation, _ = cv2.Rodrigues(vector)
        depths = (rotation @ CANONICAL_FACE_3D.T + translation)[2]
        projected, _ = cv2.projectPoints(CANONICAL_FACE_3D, vector, translation, camera, np.zeros(4))
        scale = np.linalg.norm(image[2] - image[3])
        error = np.linalg.norm(projected.reshape(-1, 2) - image, axis=1).mean()
        if np.any(depths <= 0) or scale < 15 or error / scale > 0.12:
            return None
        angles = np.asarray(cv2.RQDecomp3x3(rotation)[0], dtype=np.float64)
        return (rotation, angles) if np.isfinite(angles).all() else None
    except cv2.error:
        return None


def pose_gate(angles):
    if not np.isfinite(angles).all():
        return 0.0
    # No plegar 180° a 0°: son poses distintas.
    largest = max(abs(float(angle)) for angle in angles)
    return float(np.clip((24 - largest) / 12, 0, 1))


class EyeContactEngine:
    def __init__(self, intensity=35, enabled=True):
        self.intensity = int(np.clip(intensity, 0, 100))
        self.correction_enabled = bool(enabled)
        self.baseline = None
        self.baseline_rotation = None
        self.samples = []
        self.calibration_deadline = None
        self.current_state = "UNCALIBRATED"
        self.message = "Mira al objetivo y pulsa Calibrar."
        self.offsets = np.zeros((2, 2), dtype=np.float32)
        self.last_time = None
        self.last_centers = None
        self._grids = {}

    def reset_tracking(self):
        self.offsets.fill(0)
        self.last_time = None
        self.last_centers = None

    def start_calibration(self):
        self.reset_calibration()
        self.calibration_deadline = time.monotonic() + 8
        self.current_state = "CALIBRATING"
        self.message = "Mira al objetivo y mantén la cabeza quieta unos segundos."

    def reset_calibration(self):
        self.baseline = None
        self.baseline_rotation = None
        self.samples = []
        self.calibration_deadline = None
        self.reset_tracking()
        self.current_state = "UNCALIBRATED"
        self.message = "Calibración pendiente."

    def bypass(self, frame, state, message):
        self.current_state, self.message = state, message
        self.reset_tracking()
        if self.calibration_deadline and time.monotonic() > self.calibration_deadline:
            self.reset_calibration()
            self.message = "No se obtuvo una referencia estable. Repite la calibración."
        return frame

    @staticmethod
    def eye_geometry(points, definition):
        contour, first, second, upper, lower, iris = definition
        a, b = points[first], points[second]
        # Orientación consistente incluso en el segundo ojo.
        if a[0] > b[0]:
            a, b = b, a
        axis = b - a
        width = np.linalg.norm(axis)
        if width < 12:
            return None
        horizontal = axis / width
        vertical = np.array([-horizontal[1], horizontal[0]])
        opening = abs(np.dot(points[lower] - points[upper], vertical)) / width
        center = (a + b) / 2
        relative = points[iris] - center
        gaze = np.array([np.dot(relative, horizontal), np.dot(relative, vertical)]) / width
        if opening < 0.16 or abs(gaze[0]) > 0.4 or abs(gaze[1]) > 0.25:
            return None
        return {"contour": points[contour], "iris": points[iris], "width": width,
                "horizontal": horizontal, "vertical": vertical, "gaze": gaze}

    def process_frame(self, frame, landmarks, img_w=None, img_h=None):
        height, width = frame.shape[:2]
        if self.calibration_deadline is None and (not self.correction_enabled or self.intensity == 0):
            return self.bypass(frame, "PAUSED", "Corrección desactivada; la cámara sigue encendida.")
        if landmarks is None or len(landmarks) < 478:
            return self.bypass(frame, "NO_FACE", "No se detectan ojos con suficiente confianza.")
        points = np.array([(p.x * width, p.y * height) for p in landmarks], dtype=np.float32)
        if not np.isfinite(points).all():
            return self.bypass(frame, "UNCERTAIN", "Puntos faciales no válidos.")
        pose = pose_estimate(points[POSE_INDICES], width, height)
        if pose is None:
            return self.bypass(frame, "POSE", "Orientación no fiable; vídeo original.")
        rotation, angles = pose
        relative_angles = angles
        if self.baseline_rotation is not None:
            relative_angles = cv2.RQDecomp3x3(rotation @ self.baseline_rotation.T)[0]
        gate = min(pose_gate(angles), pose_gate(relative_angles))
        if gate <= 0:
            return self.bypass(frame, "POSE", "Cabeza girada; vídeo original.")
        eyes = [self.eye_geometry(points, definition) for definition in EYES]
        if any(eye is None for eye in eyes):
            return self.bypass(frame, "BLINK", "Parpadeo u ojo no visible; vídeo original.")
        gaze = np.array([eye["gaze"] for eye in eyes])
        if self.calibration_deadline is not None:
            if time.monotonic() > self.calibration_deadline:
                return self.bypass(frame, "UNCALIBRATED", "Repite la calibración.")
            if gate < 0.95:
                self.samples.clear()
                self.current_state, self.message = "CALIBRATING", "Coloca la cabeza frente a la cámara."
                return frame
            self.samples.append((gaze, rotation))
            if len(self.samples) >= 25:
                measurements = np.array([sample[0] for sample in self.samples])
                rotations = np.array([cv2.RQDecomp3x3(sample[1])[0] for sample in self.samples])
                if np.max(measurements.std(axis=0)) > 0.025 or np.max(rotations.std(axis=0)) > 2.5:
                    self.samples = self.samples[-10:]
                else:
                    self.baseline = np.median(measurements, axis=0)
                    self.baseline_rotation = self.samples[len(self.samples) // 2][1]
                    self.calibration_deadline = None
                    self.samples.clear()
                    self.reset_tracking()
                    self.current_state, self.message = "NATURAL", "Referencia frontal calibrada."
                    return frame
            self.current_state = "CALIBRATING"
            self.message = f"Mira al objetivo: {min(len(self.samples), 25)}/25 muestras estables."
            return frame
        if self.baseline is None:
            return self.bypass(frame, "UNCALIBRATED", "Mira al objetivo y pulsa Calibrar.")
        errors = self.baseline - gaze
        if np.max(np.abs(errors)) > 0.16 or np.linalg.norm(errors[0] - errors[1]) > 0.08:
            return self.bypass(frame, "UNCERTAIN", "Desviación grande o inconsistente; vídeo original.")
        # Zona muerta: no tocar ojos que ya miran cerca del objetivo.
        target = np.sign(errors) * np.maximum(np.abs(errors) - 0.015, 0)
        target = np.clip(target, [-0.025, -0.045], [0.025, 0.045])
        target *= self.intensity / 100.0 * gate
        centers = np.array([eye["iris"] for eye in eyes]) / [width, height]
        if self.last_centers is not None and np.max(np.abs(centers - self.last_centers)) > 0.08:
            self.reset_tracking()
        self.last_centers = centers
        now = time.monotonic()
        dt = 1 / 30 if self.last_time is None else min(now - self.last_time, 0.1)
        self.last_time = now
        alpha = 1 - math.exp(-dt / 0.12)
        self.offsets += alpha * (target - self.offsets)
        changed = False
        for index, eye in enumerate(eyes):
            offset = (eye["horizontal"] * self.offsets[index, 0] + eye["vertical"] * self.offsets[index, 1]) * eye["width"]
            if np.linalg.norm(offset) >= 0.08:
                self._deform_eye(frame, eye, offset)
                changed = True
        self.current_state = "CORRECTING" if changed else "NATURAL"
        self.message = "Corrección sutil aplicada." if changed else "Mirada cerca del objetivo; sin corrección."
        return frame

    def _deform_eye(self, frame, eye, offset):
        height, width = frame.shape[:2]
        contour = eye["contour"]
        x1, y1 = np.maximum(np.floor(contour.min(axis=0) - 4), 0).astype(int)
        x2, y2 = np.minimum(np.ceil(contour.max(axis=0) + 5), [width, height]).astype(int)
        if x2 <= x1 or y2 <= y1:
            return
        crop = frame[y1:y2, x1:x2]
        h, w = crop.shape[:2]
        key = (h, w)
        if key not in self._grids:
            if len(self._grids) >= 8:
                self._grids.clear()
            self._grids[key] = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
        grid_x, grid_y = self._grids[key]
        polygon = np.round(contour - [x1, y1]).astype(np.int32)
        interior = np.zeros((h, w), dtype=np.uint8)
        cv2.fillPoly(interior, [polygon], 1)
        interior = cv2.erode(interior, np.ones((3, 3), dtype=np.uint8))
        edge = np.minimum(cv2.distanceTransform(interior, cv2.DIST_L2, 3) / 2, 1)
        iris = eye["iris"] - [x1, y1] + offset * 0.5
        radius = max(2, eye["width"] * 0.20)
        radial = np.exp(-((grid_x - iris[0]) ** 2 + (grid_y - iris[1]) ** 2) / (2 * radius ** 2))
        mask = (edge * radial).astype(np.float32)
        shifted = cv2.remap(
            crop, grid_x - offset[0] * mask, grid_y - offset[1] * mask,
            cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101,
        )
        # Escribir solo dentro del ojo; fuera de él se conserva cada byte.
        active = mask > 0
        blended = np.rint(shifted * mask[..., None] + crop * (1 - mask[..., None]))
        crop[active] = np.clip(blended[active], 0, 255).astype(np.uint8)
