"""Captura con dueño único, detección reducida y telemetría acotada."""

import os
import threading
import time

import cv2
import numpy as np

import config_manager
from eye_contact import EyeContactEngine
from protocol import coalesce_commands, drain, offer, offer_latest, valid_command
from runtime import get_logger


class ThreadedCameraCapture:
    """Solo este hilo abre, lee, reconecta y libera el VideoCapture.

    Si un controlador se bloquea, stop() no libera el recurso desde otro hilo:
    el orquestador dispone de terminación de respaldo del proceso completo.
    """
    def __init__(self, camera_index=0, width=1280, height=720, fps=30):
        self.camera_index = camera_index
        self.width, self.height, self.fps = width, height, fps
        self.condition = threading.Condition()
        self.stopped = threading.Event()
        self.latest_frame = None
        self.frame_id = 0
        self.last_frame_at = 0.0
        self.error = None
        self.thread = threading.Thread(target=self._capture_loop, daemon=True, name="CameraCapture")
        self.thread.start()

    def _capture_loop(self):
        cap = None
        failures = 0
        backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF] if os.name == "nt" else [cv2.CAP_ANY]
        backend_index = 0
        try:
            while not self.stopped.is_set():
                if cap is None:
                    # No se enumeran cámaras: evitar seleccionar por accidente una virtual.
                    backend = backends[backend_index % len(backends)]
                    cap = cv2.VideoCapture(self.camera_index, backend)
                    if not cap.isOpened():
                        cap.release()
                        cap = None
                        backend_index += 1
                        self.stopped.wait(0.75)
                        continue
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                    cap.set(cv2.CAP_PROP_FPS, self.fps)
                ok, frame = cap.read()
                if ok and frame is not None and frame.ndim == 3 and frame.shape[2] == 3:
                    with self.condition:
                        self.latest_frame = frame
                        self.frame_id += 1
                        self.last_frame_at = time.monotonic()
                        self.condition.notify_all()
                    failures = 0
                else:
                    failures += 1
                    if failures >= 10:
                        cap.release()
                        cap = None
                        backend_index += 1
                        failures = 0
                    self.stopped.wait(0.05)
        except Exception:
            self.error = "El controlador de cámara falló."
            get_logger("video").exception("Error de captura")
        finally:
            if cap is not None:
                cap.release()
            with self.condition:
                self.condition.notify_all()

    def read(self, after_id=0, timeout=0.05):
        with self.condition:
            self.condition.wait_for(
                lambda: self.frame_id != after_id or self.stopped.is_set() or self.error,
                timeout=timeout,
            )
            if self.latest_frame is None or self.frame_id == after_id:
                return False, None, self.frame_id
            # OpenCV devuelve un array nuevo en cada read. No copiar hasta usarlo.
            return True, self.latest_frame, self.frame_id

    def stop(self):
        self.stopped.set()
        with self.condition:
            self.condition.notify_all()
        self.thread.join(timeout=1.5)
        return not self.thread.is_alive()


def require_face_mesh():
    import mediapipe
    solutions = getattr(mediapipe, "solutions", None)
    if not hasattr(getattr(solutions, "face_mesh", None), "FaceMesh"):
        raise RuntimeError("MediaPipe incompatible: falta la API FaceMesh. Usa las versiones de requirements.txt.")
    return solutions.face_mesh.FaceMesh


def fit_frame_to_output(captured, output_size):
    """Ajusta el fotograma al tamaño de salida conservando su proporción."""
    out_w, out_h = output_size
    h, w = captured.shape[:2]
    if (w, h) == (out_w, out_h):
        return captured.copy()
    if abs((w / max(1, h)) - (out_w / max(1, out_h))) <= 0.01:
        return cv2.resize(captured, (out_w, out_h), interpolation=cv2.INTER_AREA)
    scale = min(out_w / max(1, w), out_h / max(1, h))
    scaled_w = max(1, min(out_w, int(round(w * scale))))
    scaled_h = max(1, min(out_h, int(round(h * scale))))
    resized = cv2.resize(captured, (scaled_w, scaled_h), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((out_h, out_w, 3), dtype=np.uint8)
    y0 = (out_h - scaled_h) // 2
    x0 = (out_w - scaled_w) // 2
    canvas[y0:y0 + scaled_h, x0:x0 + scaled_w] = resized
    return canvas


def run_video_worker(commands, events, previews, stop, settings):
    logger = get_logger("video")
    grabber = face_mesh = virtual = None
    try:
        import pyvirtualcam
        factory = require_face_mesh()
        profile = config_manager.PROFILES[settings["video"]["profile"]]
        cv2.setNumThreads(1)
        engine = EyeContactEngine(**settings["eye_contact"])
        face_mesh = factory(max_num_faces=1, refine_landmarks=True,
                            min_detection_confidence=0.6, min_tracking_confidence=0.6)
        grabber = ThreadedCameraCapture(
            camera_index=settings["video"]["camera_index"], width=profile["width"],
            height=profile["height"], fps=profile["fps"],
        )
        start = time.monotonic()
        last_id = 0
        last_status = last_preview = 0.0
        want_preview = False
        output_size = None
        capture_shape = None
        last_output = None
        count = 0
        rate_start = start
        fps = 0.0
        detection_width = profile["detection_width"]
        slow_frames = 0
        while not stop.is_set():
            for message in coalesce_commands(list(drain(commands))):
                if not valid_command(message):
                    continue
                cmd = message["cmd"]
                if cmd == "STOP":
                    stop.set()
                elif cmd == "SET_INTENSITY":
                    engine.intensity = config_manager.bounded_number(message.get("intensity"), 35, 0, 100, integer=True)
                elif cmd == "TOGGLE_CORRECTION" and isinstance(message.get("enabled"), bool):
                    engine.correction_enabled = message["enabled"]
                elif cmd == "PREVIEW":
                    want_preview = message.get("enabled") is True
                elif cmd == "CALIBRATE":
                    engine.start_calibration()
                elif cmd == "RESET_CALIBRATION":
                    engine.reset_calibration()
            if stop.is_set():
                break
            ok, captured, frame_id = grabber.read(last_id)
            now = time.monotonic()
            if grabber.error:
                raise RuntimeError(grabber.error)
            if not ok:
                missing = now - (grabber.last_frame_at or start)
                if missing > (8 if output_size else 10):
                    raise RuntimeError("No hay señal de cámara. Comprueba permisos, conexión e índice de cámara.")
                if output_size and missing > 0.5:
                    engine.reset_calibration()
                    if now - last_status > 0.5:
                        offer_latest(events, {"type": "STATUS", "state": "RECONNECTING", "message": "Sin señal; intentando reconectar.",
                                              "intensity": engine.intensity, "enabled": engine.correction_enabled})
                        last_status = now
                    # No simular vídeo vivo repitiendo el rostro antiguo.
                    last_output = np.zeros((output_size[1], output_size[0], 3), dtype=np.uint8)
                if virtual is not None and last_output is not None:
                    virtual.send(last_output)
                    virtual.sleep_until_next_frame()
                continue
            last_id = frame_id
            if output_size is None:
                h, w = captured.shape[:2]
                capture_shape = (h, w)
                # Limitar salida a lo pedido y conservar proporción. Tamaños pares para vcam.
                scale = min(1, profile["width"] / w, profile["height"] / h)
                output_size = (max(2, int(w * scale) // 2 * 2), max(2, int(h * scale) // 2 * 2))
                try:
                    virtual = pyvirtualcam.Camera(width=output_size[0], height=output_size[1],
                                                  fps=profile["fps"], fmt=pyvirtualcam.PixelFormat.BGR,
                                                  backend="obs")
                except Exception as exc:
                    raise RuntimeError("No se pudo abrir OBS Virtual Camera. Comprueba que esté instalada y libre.") from exc
                offer(events, {"type": "READY", "width": output_size[0], "height": output_size[1],
                               "message": "Cámara lista. Calibra mirando al objetivo."})
            elif captured.shape[:2] != capture_shape:
                capture_shape = captured.shape[:2]
                engine.reset_calibration()
                offer_latest(events, {"type": "WARNING", "message": "Cambió el formato de la cámara; vuelve a calibrar."})
            frame = fit_frame_to_output(captured, output_size)
            original = frame.copy() if want_preview and now - last_preview >= 0.2 else None
            processing_start = time.monotonic()
            need_tracking = (engine.correction_enabled and engine.intensity > 0) or engine.calibration_deadline is not None
            landmarks = None
            if need_tracking:
                scale = min(1, detection_width / frame.shape[1])
                small = (
                    cv2.resize(frame, (int(frame.shape[1] * scale), max(1, int(frame.shape[0] * scale))), interpolation=cv2.INTER_AREA)
                    if scale < 1 else frame
                )
                rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
                rgb.flags.writeable = False
                results = face_mesh.process(rgb)
                landmarks = results.multi_face_landmarks[0].landmark if results.multi_face_landmarks else None
            frame = engine.process_frame(frame, landmarks)
            processing_ms = (time.monotonic() - processing_start) * 1000
            if processing_ms > 1000 / profile["fps"]:
                slow_frames += 1
                if slow_frames >= 30 and detection_width > 320:
                    detection_width = 320
                    offer_latest(events, {"type": "WARNING", "message": "Detección reducida a 320 px para aliviar la CPU."})
                    slow_frames = 0
            else:
                slow_frames = max(0, slow_frames - 1)
            virtual.send(frame)
            virtual.sleep_until_next_frame()
            last_output = frame
            count += 1
            elapsed = time.monotonic() - rate_start
            if elapsed >= 1:
                fps = count / elapsed
                count, rate_start = 0, time.monotonic()
            if now - last_status >= 0.3:
                offer_latest(events, {"type": "STATUS", "state": engine.current_state,
                                      "message": engine.message, "fps": round(fps, 1),
                                      "processing_ms": round(processing_ms, 1), "detection_width": detection_width,
                                      "intensity": engine.intensity, "enabled": engine.correction_enabled})
                last_status = now
            if original is not None:
                h = 150
                w = max(2, int(h * output_size[0] / output_size[1]))
                comparison = np.hstack((cv2.resize(original, (w, h)), cv2.resize(frame, (w, h))))
                encoded, jpg = cv2.imencode(".jpg", comparison, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if encoded:
                    offer_latest(previews, jpg.tobytes())
                last_preview = now
    except Exception as exc:
        logger.exception("Motor detenido por error")
        offer(events, {"type": "ERROR", "message": str(exc)})
        raise
    finally:
        if grabber is not None and not grabber.stop():
            logger.warning("Captura bloqueada; el proceso liberará el recurso al terminar.")
        for resource in (face_mesh, virtual):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    logger.exception("Error al cerrar recurso de vídeo")
        offer(events, {"type": "ACK_STOP"})
