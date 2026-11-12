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


def _normalized_sigmoid_falloff(value, low, high, steepness=0.45):
    """Curva logística suave entre 1.0 (<= low) y 0.0 (>= high), estilo NVIDIA AR gating."""
    if value <= low:
        return 1.0
    if value >= high:
        return 0.0
    midpoint = 0.5 * (low + high)
    raw = 1.0 / (1.0 + math.exp(steepness * (value - midpoint)))
    high_val = 1.0 / (1.0 + math.exp(steepness * (low - midpoint)))
    low_val = 1.0 / (1.0 + math.exp(steepness * (high - midpoint)))
    return float(np.clip((raw - low_val) / max(1e-6, high_val - low_val), 0.0, 1.0))


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
    # No plegar 180° a 0°: son poses distintas. Transición sigmoidea suave entre 12° y 24°.
    largest = max(abs(float(angle)) for angle in angles)
    return _normalized_sigmoid_falloff(largest, 12.0, 24.0, steepness=0.45)


def dynamic_gaze_gate(errors):
    """Atenuación sigmoidea externa y coherencia binocular para evitar mirada forzada."""
    max_err = float(np.max(np.abs(errors)))
    disparity = float(np.linalg.norm(errors[0] - errors[1]))
    if max_err > 0.18 or disparity > 0.085:
        return 0.0
    outer_gate = _normalized_sigmoid_falloff(max_err, 0.11, 0.18, steepness=85.0)
    disparity_gate = _normalized_sigmoid_falloff(disparity, 0.045, 0.085, steepness=140.0)
    return float(outer_gate * disparity_gate)


class EyeContactEngine:
    def __init__(self, intensity=35, enabled=True):
        self.intensity = int(np.clip(intensity, 0, 100))
        self.correction_enabled = bool(enabled)
        self.baseline = None
        self.baseline_rotation = None
        self.baseline_scale = None
        self.face_lost_since = None
        self.samples = []
        self.calibration_deadline = None
        self.current_state = "UNCALIBRATED"
        self.message = "Mira al objetivo y pulsa Calibrar."
        self.offsets = np.zeros((2, 2), dtype=np.float32)
        self.last_time = None
        self.last_centers = None
        self.last_gate = 1.0
        self._saccade_vec = np.zeros(2, dtype=np.float32)
        self._saccade_start = 0.0
        self._saccade_end = 0.0
        self._next_saccade_at = 0.0
        self._saccade_step = 0
        self._grids = {}

    def reset_tracking(self):
        self.offsets.fill(0)
        self.last_time = None
        self.last_centers = None
        self.last_gate = 1.0
        self._saccade_vec.fill(0)
        self._saccade_start = 0.0
        self._saccade_end = 0.0
        self._next_saccade_at = 0.0

    def _physiological_micro_motion(self, now, active_weight):
        """Deriva lenta + micro-sacadas elípticas conjugadas (modelo de Engbert)."""
        if active_weight <= 0.05:
            self._saccade_vec.fill(0)
            return np.zeros(2, dtype=np.float32)
        # Deriva basal continua con predominio horizontal 2:1 (elipse de velocidades de Engbert).
        drift_x = 0.0014 * math.sin(now * 2.3) + 0.0008 * math.cos(now * 3.7)
        drift_y = 0.0007 * math.sin(now * 1.9 + 0.8)
        pulse = np.zeros(2, dtype=np.float32)
        if self._next_saccade_at <= 0.0:
            self._next_saccade_at = now + 1.1
        elif now >= self._next_saccade_at:
            self._saccade_step += 1
            # Secuencia determinista de baja discrepancia: evita estado global de RNG y es reproducible.
            angle = (self._saccade_step * 2.399963229728653) % (2.0 * math.pi)
            # Elipse de Engbert: semieje horizontal mayor que el vertical.
            self._saccade_vec[0] = float(0.0032 * math.cos(angle))
            self._saccade_vec[1] = float(0.0016 * math.sin(angle))
            self._saccade_start = now
            self._saccade_end = now + 0.14
            interval = 1.0 + 0.9 * abs(math.sin(self._saccade_step * 1.6180339887))
            self._next_saccade_at = self._saccade_end + interval
        if self._saccade_start < now < self._saccade_end:
            phase = (now - self._saccade_start) / max(1e-4, self._saccade_end - self._saccade_start)
            # Perfil balístico suave de ida y retorno (0 -> 1 -> 0).
            envelope = math.sin(math.pi * phase) ** 2
            pulse = self._saccade_vec * float(envelope)
        return (np.array([drift_x, drift_y], dtype=np.float32) + pulse) * float(active_weight)

    def start_calibration(self):
        self.reset_calibration()
        self.calibration_deadline = time.monotonic() + 8
        self.current_state = "CALIBRATING"
        self.message = "Mira al objetivo y mantén la cabeza quieta unos segundos."

    def reset_calibration(self):
        self.baseline = None
        self.baseline_rotation = None
        self.baseline_scale = None
        self.face_lost_since = None
        self.samples = []
        self.calibration_deadline = None
        self.reset_tracking()
        self.current_state = "UNCALIBRATED"
        self.message = "Calibración pendiente."

    def bypass(self, frame, state, message):
        self.current_state, self.message = state, message
        self.reset_tracking()
        if self.calibration_deadline is not None:
            self.samples.clear()
            if time.monotonic() > self.calibration_deadline:
                self.reset_calibration()
                self.message = "No se obtuvo una referencia estable. Repite la calibración."
        if state == "NO_FACE":
            now = time.monotonic()
            if self.face_lost_since is None:
                self.face_lost_since = now
            elif self.baseline is not None and now - self.face_lost_since > 3.0:
                self.reset_calibration()
                self.current_state = "NO_FACE"
                self.message = "Rostro ausente demasiado tiempo; vuelve a calibrar."
        else:
            self.face_lost_since = None
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
        self.face_lost_since = None
        face_scale = float(np.linalg.norm(points[33] - points[263]))
        gaze = np.array([eye["gaze"] for eye in eyes])
        if self.calibration_deadline is not None:
            if time.monotonic() > self.calibration_deadline:
                return self.bypass(frame, "UNCALIBRATED", "Repite la calibración.")
            if gate < 0.95:
                self.samples.clear()
                self.reset_tracking()
                self.current_state, self.message = "CALIBRATING", "Coloca la cabeza frente a la cámara."
                return frame
            self.samples.append((gaze, rotation, face_scale))
            if len(self.samples) >= 25:
                measurements = np.array([sample[0] for sample in self.samples])
                rotations = np.array([cv2.RQDecomp3x3(sample[1])[0] for sample in self.samples])
                if np.max(measurements.std(axis=0)) > 0.025 or np.max(rotations.std(axis=0)) > 2.5:
                    self.samples = self.samples[-10:]
                else:
                    self.baseline = np.median(measurements, axis=0)
                    self.baseline_rotation = self.samples[len(self.samples) // 2][1]
                    self.baseline_scale = float(np.median([sample[2] for sample in self.samples]))
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
        if self.baseline_scale is not None and self.baseline_scale > 0:
            if abs(face_scale - self.baseline_scale) / self.baseline_scale > 0.28:
                self.reset_calibration()
                return self.bypass(frame, "UNCALIBRATED", "Posición o sujeto cambió; vuelve a calibrar.")
        errors = self.baseline - gaze
        gaze_gate = dynamic_gaze_gate(errors)
        if gaze_gate <= 0.0:
            return self.bypass(frame, "UNCERTAIN", "Desviación grande o inconsistente; vídeo original.")
        combined_gate = float(gate * gaze_gate)
        # Zona muerta interna con rampa sigmoidea suave para no tocar ojos que ya miran al objetivo.
        abs_err = np.abs(errors)
        excess = np.maximum(abs_err - 0.015, 0.0)
        inner_smooth = excess / (excess + 0.004)
        target = np.sign(errors) * excess * np.where(excess > 0.0, inner_smooth, 0.0)
        target = np.clip(target, [-0.025, -0.045], [0.025, 0.045])
        target *= (self.intensity / 100.0) * combined_gate
        now = time.monotonic()
        # Inyectar micro-sacadas conjugadas solo cuando existe corrección efectiva fuera de la zona muerta.
        active_strength = float(np.clip(np.max(np.abs(target)) / 0.012, 0.0, 1.0)) * combined_gate
        if active_strength > 0.05:
            micro = self._physiological_micro_motion(now, active_strength * (self.intensity / 100.0))
            target = target + micro[None, :]
        else:
            self._saccade_vec.fill(0)
        centers = np.array([eye["iris"] for eye in eyes]) / [width, height]
        if self.last_centers is not None and np.max(np.abs(centers - self.last_centers)) > 0.08:
            self.reset_tracking()
        self.last_centers = centers
        dt = 1 / 30 if self.last_time is None else min(now - self.last_time, 0.1)
        self.last_time = now
        # Amortiguación adaptativa: retorno más rápido si la compuerta cae por giro de cabeza.
        tau = 0.045 if combined_gate < self.last_gate - 0.05 else 0.12
        self.last_gate = combined_gate
        alpha = 1 - math.exp(-dt / tau)
        self.offsets += alpha * (target - self.offsets)
        changed = False
        for index, eye in enumerate(eyes):
            offset = (eye["horizontal"] * self.offsets[index, 0] + eye["vertical"] * self.offsets[index, 1]) * eye["width"]
            if np.linalg.norm(offset) >= 0.08 and self._deform_eye(frame, eye, offset):
                changed = True
        self.current_state = "CORRECTING" if changed else "NATURAL"
        self.message = "Corrección sutil aplicada." if changed else "Mirada cerca del objetivo; sin corrección."
        return frame

    @staticmethod
    def _extract_specular_layer(crop, interior, radial):
        """Extrae reflejos corneales reales (LCM); retorna None en baja luz o sin reflejo."""
        valid = interior > 0
        if not np.any(valid):
            return None
        c_max = np.max(crop, axis=2).astype(np.float32)
        c_min = np.min(crop, axis=2).astype(np.float32)
        interior_vals = c_max[valid]
        median_lum = float(np.median(interior_vals))
        # Doble guarda física: brillo absoluto alto (>= 185) y contraste claro sobre el fondo ocular (+38).
        threshold = max(185.0, median_lum + 38.0, float(np.percentile(interior_vals, 93)))
        chroma = c_max - c_min
        spec_candidates = (c_max >= threshold) & (chroma <= 75.0) & valid & (radial > 0.15)
        if not np.any(spec_candidates):
            return None
        strength = np.clip((c_max - threshold) / max(12.0, 255.0 - threshold), 0.0, 1.0)
        spec_weight = np.where(spec_candidates, strength * radial, 0.0).astype(np.float32)
        spec_weight = cv2.GaussianBlur(spec_weight, (3, 3), 0) * interior.astype(np.float32)
        if float(np.max(spec_weight)) < 0.05:
            return None
        return spec_weight[..., None], crop.astype(np.float32)

    def _deform_eye(self, frame, eye, offset):
        height, width = frame.shape[:2]
        contour = eye["contour"]
        x1, y1 = np.maximum(np.floor(contour.min(axis=0) - 4), 0).astype(int)
        x2, y2 = np.minimum(np.ceil(contour.max(axis=0) + 5), [width, height]).astype(int)
        if x2 <= x1 or y2 <= y1:
            return False
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
        if not np.any(interior):
            return False
        edge = np.minimum(cv2.distanceTransform(interior, cv2.DIST_L2, 3) / 2, 1)
        iris = eye["iris"] - [x1, y1] + offset * 0.5
        radius = max(2, eye["width"] * 0.20)
        radial = np.exp(-((grid_x - iris[0]) ** 2 + (grid_y - iris[1]) ** 2) / (2 * radius ** 2))
        mask = (edge * radial).astype(np.float32)
        active = mask > 0.01
        if not np.any(active) or float(np.linalg.norm(offset) * np.max(mask)) < 0.05:
            return False
        specular = self._extract_specular_layer(crop, interior, radial)
        source_crop = crop
        if specular is not None:
            spec_weight, orig_float = specular
            # Suavizar el pico especular antes del remap para no arrastrar un reflejo duplicado.
            local_blur = cv2.GaussianBlur(crop, (5, 5), 0).astype(np.float32)
            dampened = orig_float * (1.0 - 0.65 * spec_weight) + local_blur * (0.65 * spec_weight)
            source_crop = np.clip(np.rint(dampened), 0, 255).astype(np.uint8)
        map_x = grid_x - offset[0] * mask
        map_y = grid_y - offset[1] * mask
        src_x = np.clip(np.rint(map_x).astype(np.int32), 0, w - 1)
        src_y = np.clip(np.rint(map_y).astype(np.int32), 0, h - 1)
        outside_src = interior[src_y, src_x] == 0
        if np.any(outside_src):
            map_x = np.where(outside_src, grid_x, map_x)
            map_y = np.where(outside_src, grid_y, map_y)
        shifted = cv2.remap(
            source_crop, map_x, map_y,
            cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101,
        )
        # Escribir solo dentro del ojo; fuera de él se conserva cada byte.
        feather = edge.astype(np.float32)[..., None]
        blended = shifted.astype(np.float32) * feather + crop.astype(np.float32) * (1.0 - feather)
        if specular is not None:
            # Re-inyección estacionaria del reflejo especular corneal en sus coordenadas originales (LCM).
            spec_weight, orig_float = specular
            stationary_weight = spec_weight * feather
            blended = blended * (1.0 - stationary_weight) + orig_float * stationary_weight
        crop[active] = np.clip(np.rint(blended[active]), 0, 255).astype(np.uint8)
        return True
