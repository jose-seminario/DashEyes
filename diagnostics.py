"""Diagnóstico aislado: nunca instala paquetes ni solicita privilegios."""

import importlib
import os
import sys
import traceback


def runtime_context():
    context = {"frozen": bool(getattr(sys, "frozen", False)), "python": sys.version,
               "crt_fds": {}, "python_fds": {}}
    for descriptor in (0, 1, 2):
        try:
            os.fstat(descriptor)
            context["crt_fds"][str(descriptor)] = "valid"
        except OSError as exc:
            context["crt_fds"][str(descriptor)] = str(exc)
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name)
        try:
            context["python_fds"][name] = stream.fileno() if stream is not None else None
        except (OSError, ValueError):
            context["python_fds"][name] = "not a native stream"
    return context


def diagnose(check_camera=True, native_first=False):
    result = {"type": "DIAGNOSTICS", "packages_ok": True, "packages_details": [],
              "obs_driver_ok": False, "obs_details": "No comprobado",
              "runtime": runtime_context(), "import_errors": {}, "face_mesh_ok": False}
    # Reproducir el orden del proceso de vídeo. Qt pertenece a la UI y no debe
    # precargar sus DLL antes de comprobar los bindings nativos de MediaPipe.
    order = ("numpy", "cv2", "mediapipe", "pyvirtualcam", "PyQt6.QtWidgets")
    if native_first:
        order = ("mediapipe", "numpy", "cv2", "pyvirtualcam", "PyQt6.QtWidgets")
    result["import_order"] = list(order)
    for name in order:
        try:
            module = importlib.import_module(name)
            if name == "mediapipe":
                factory = getattr(getattr(getattr(module, "solutions", None), "face_mesh", None), "FaceMesh", None)
                if factory is None:
                    raise RuntimeError("API FaceMesh ausente; usa las versiones de requirements.txt")
                # Importar no basta: validar también los grafos y modelos del paquete.
                # No se abre ninguna cámara ni se procesa vídeo del usuario.
                try:
                    with factory(static_image_mode=True, max_num_faces=1, refine_landmarks=True):
                        result["face_mesh_ok"] = True
                except Exception as exc:
                    raise RuntimeError(f"FaceMesh no pudo inicializar sus modelos: {exc}") from exc
            result["packages_details"].append([name, True, getattr(module, "__version__", "Disponible")])
        except Exception as exc:
            result["packages_ok"] = False
            result["packages_details"].append([name, False, str(exc)])
            result["import_errors"][name] = traceback.format_exc()
    if not check_camera:
        result["obs_details"] = "Comprobación de cámara omitida explícitamente"
    elif result["packages_ok"]:
        try:
            import pyvirtualcam
            with pyvirtualcam.Camera(width=640, height=480, fps=20, backend="obs") as camera:
                result["obs_driver_ok"] = True
                result["obs_details"] = camera.device
        except Exception:
            result["obs_details"] = "OBS Virtual Camera no disponible o en uso"
    return result
