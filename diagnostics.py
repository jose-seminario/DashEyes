"""Diagnóstico aislado: nunca instala paquetes ni solicita privilegios."""

import importlib


def diagnose():
    result = {"type": "DIAGNOSTICS", "packages_ok": True, "packages_details": [],
              "obs_driver_ok": False, "obs_details": "No comprobado"}
    for name in ("PyQt6.QtWidgets", "numpy", "cv2", "mediapipe", "pyvirtualcam"):
        try:
            module = importlib.import_module(name)
            if name == "mediapipe" and not hasattr(getattr(getattr(module, "solutions", None), "face_mesh", None), "FaceMesh"):
                raise RuntimeError("API FaceMesh ausente; se requiere MediaPipe 0.10.21")
            result["packages_details"].append([name, True, getattr(module, "__version__", "Disponible")])
        except Exception as exc:
            result["packages_ok"] = False
            result["packages_details"].append([name, False, str(exc)])
    if result["packages_ok"]:
        try:
            import pyvirtualcam
            with pyvirtualcam.Camera(width=640, height=480, fps=20, backend="obs") as camera:
                result["obs_driver_ok"] = True
                result["obs_details"] = camera.device
        except Exception:
            result["obs_details"] = "OBS Virtual Camera no disponible o en uso"
    return result
