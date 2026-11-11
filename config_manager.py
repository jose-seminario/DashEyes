"""
config_manager.py - Gestor de Persistencia y Configuración Atómica de DashEyes
Soporta lectura/escritura segura con bloqueo de archivos (msvcrt) y reemplazo atómico en Windows.
"""

import os
import sys
import json
import time

try:
    import msvcrt
except ImportError:
    msvcrt = None

DEFAULT_CONFIG = {
    "enable_level1_controls": True,
    "eye_contact": {
        "enabled": True,
        "intensity": 50,
        "vertical_shift_px": 5.0,
        "horizontal_inward_px": 1.0,
    },
    "teleprompter": {
        "scroll_speed": 3,
        "font_size": 24,
        "opacity": 215,
    }
}


def get_base_dir() -> str:
    """Retorna el directorio base según si corre congelado (PyInstaller) o como script."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_config_path() -> str:
    return os.path.join(get_base_dir(), "config.json")


def get_lock_path() -> str:
    return os.path.join(get_base_dir(), "config.lock")


def calculate_shifts(intensity: int) -> tuple:
    """
    Mapeo proporcional de Intensidad (0% - 100%) a píxeles de corrección:
    - vertical_shift_px: 0.0 px (0%) -> 5.0 px (50%) -> 10.0 px (100%)
    - horizontal_inward_px: 0.0 px (0%) -> 1.0 px (50%) -> 2.0 px (100%)
    """
    clamped = max(0, min(100, int(intensity)))
    factor = clamped / 100.0
    v_shift = round(factor * 10.0, 1)
    h_shift = round(factor * 2.0, 1)
    return v_shift, h_shift


def load_config() -> dict:
    """Carga config.json. Si no existe o es inválido, genera y guarda los valores por defecto."""
    cfg_path = get_config_path()

    if not os.path.exists(cfg_path):
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))

    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            merged = json.loads(json.dumps(DEFAULT_CONFIG))
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, dict) and k in merged and isinstance(merged[k], dict):
                        merged[k].update(v)
                    else:
                        merged[k] = v
            return merged
    except Exception:
        return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(config_data: dict) -> bool:
    """
    Escritura atómica de config.json en Windows usando archivo temporal y os.replace(),
    protegida con bloqueo no bloqueante mediante msvcrt.locking en config.lock.
    """
    cfg_path = get_config_path()
    tmp_path = cfg_path + ".tmp"
    lock_path = get_lock_path()

    lock_fd = None
    acquired = False

    if msvcrt is not None:
        for _ in range(4):
            try:
                lock_fd = open(lock_path, "r+b" if os.path.exists(lock_path) else "w+b")
                if os.path.getsize(lock_path) == 0:
                    lock_fd.write(b"L")
                    lock_fd.flush()
                lock_fd.seek(0)
                msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
                break
            except (OSError, IOError):
                if lock_fd:
                    try:
                        lock_fd.close()
                    except Exception:
                        pass
                    lock_fd = None
                time.sleep(0.025)

    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, cfg_path)
        return True

    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return False

    finally:
        if acquired and lock_fd and msvcrt is not None:
            try:
                lock_fd.seek(0)
                msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
        if lock_fd:
            try:
                lock_fd.close()
            except Exception:
                pass


def update_eye_contact_config(intensity: int = None, enabled: bool = None) -> dict:
    """Actualiza y persiste las variables de Eye Contact."""
    cfg = load_config()
    eye_cfg = cfg.setdefault("eye_contact", {})

    if intensity is not None:
        clamped_intensity = max(0, min(100, int(intensity)))
        eye_cfg["intensity"] = clamped_intensity
        v_shift, h_shift = calculate_shifts(clamped_intensity)
        eye_cfg["vertical_shift_px"] = v_shift
        eye_cfg["horizontal_inward_px"] = h_shift

    if enabled is not None:
        eye_cfg["enabled"] = bool(enabled)

    save_config(cfg)
    return cfg


def update_teleprompter_config(scroll_speed: int = None, font_size: int = None, opacity: int = None) -> dict:
    """Actualiza y persiste las preferencias del Teleprompter."""
    cfg = load_config()
    tele_cfg = cfg.setdefault("teleprompter", {})

    if scroll_speed is not None:
        tele_cfg["scroll_speed"] = max(1, min(10, int(scroll_speed)))
    if font_size is not None:
        tele_cfg["font_size"] = max(16, min(44, int(font_size)))
    if opacity is not None:
        tele_cfg["opacity"] = max(80, min(255, int(opacity)))

    save_config(cfg)
    return cfg
