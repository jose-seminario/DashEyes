"""Configuración portátil, validada y actualizada bajo un único cerrojo.

Leer no crea archivos. Las escrituras se limitan a .dasheyes junto al programa.
Los config.json anteriores se leen como respaldo, sin borrarlos ni modificarlos.
"""

import copy
import json
import math
import os
import shutil
import sys
import tempfile
import threading
import time
from contextlib import contextmanager

try:
    import msvcrt
except ImportError:
    msvcrt = None

DEFAULT_CONFIG = {
    "enable_level1_controls": True,
    "enable_level2_preview": True,
    "eye_contact": {"enabled": True, "intensity": 35},
    "teleprompter": {
        "scroll_speed": 3, "font_size": 24, "opacity": 215,
        "width": 560, "height": 340,
    },
    "video": {"camera_index": 0, "profile": "balanced"},
}
PROFILES = {
    "light": {"width": 640, "height": 480, "fps": 20, "detection_width": 320},
    "balanced": {"width": 1280, "height": 720, "fps": 30, "detection_width": 480},
}
_LOCAL_LOCKS = {}
_LOCK_REGISTRY = threading.Lock()


class ConfigError(RuntimeError):
    pass


def get_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_data_dir():
    return os.path.join(get_base_dir(), ".dasheyes")


def get_config_path():
    return os.path.join(get_data_dir(), "config.json")


def get_lock_path():
    return os.path.join(get_data_dir(), "config.lock")


def bounded_number(value, default, low, high, integer=False):
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
        if not math.isfinite(number):
            return default
        number = max(low, min(high, number))
        return int(number) if integer else number
    except (TypeError, ValueError, OverflowError):
        return default


def normalize_config(data):
    result = copy.deepcopy(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return result
    for flag_name in ("enable_level1_controls", "enable_level2_preview"):
        flag = data.get(flag_name)
        if isinstance(flag, bool):
            result[flag_name] = flag
    for section in ("eye_contact", "teleprompter", "video"):
        supplied = data.get(section)
        if not isinstance(supplied, dict):
            continue
        target = result[section]
        limits = {
            "intensity": (0, 100), "scroll_speed": (1, 10),
            "font_size": (16, 44), "opacity": (80, 255),
            "width": (420, 1000), "height": (240, 800), "camera_index": (0, 15),
        }
        for key, default in target.items():
            value = supplied.get(key, default)
            if key in limits:
                target[key] = bounded_number(value, default, *limits[key], integer=True)
            elif isinstance(default, bool):
                target[key] = value if isinstance(value, bool) else default
            elif key == "profile":
                target[key] = value if isinstance(value, str) and value in PROFILES else default
    return result


def calculate_shifts(intensity):
    """Compatibilidad para integraciones anteriores; el motor usa escala ocular."""
    factor = bounded_number(intensity, 35, 0, 100) / 100.0
    return round(factor * 10, 1), round(factor * 2, 1)


def level1_controls_enabled(data=None):
    """Indica si los controles manuales de nivel 1 están habilitados en la configuración."""
    source = normalize_config(data) if isinstance(data, dict) else load_config()
    return bool(source.get("enable_level1_controls", True))


def level2_preview_enabled(data=None):
    """Indica si la vista previa en memoria compartida de nivel 2 está habilitada."""
    source = normalize_config(data) if isinstance(data, dict) else load_config()
    return bool(source.get("enable_level2_preview", True))


@contextmanager
def file_lock(name="config.lock", timeout=0.6):
    """Nunca continúa sin bloqueo. Incluye la espera del cerrojo local en el plazo."""
    os.makedirs(get_data_dir(), exist_ok=True)
    deadline = time.monotonic() + max(0.0, float(timeout))
    with _LOCK_REGISTRY:
        local_lock = _LOCAL_LOCKS.setdefault(name, threading.Lock())
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        acquired_local = local_lock.acquire(blocking=False)
    else:
        acquired_local = local_lock.acquire(blocking=True, timeout=remaining)
    if not acquired_local:
        raise ConfigError("Otro proceso está usando los ajustes o la sesión.")
    try:
        handle = open(os.path.join(get_data_dir(), name), "a+b")
        acquired = False
        try:
            while True:
                try:
                    handle.seek(0)
                    if msvcrt is not None:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise ConfigError("Otro proceso está usando los ajustes o la sesión.") from exc
                    time.sleep(min(0.02, max(0.001, deadline - time.monotonic())))
            yield
        finally:
            try:
                if acquired:
                    handle.seek(0)
                    if msvcrt is not None:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
    finally:
        local_lock.release()


def _read_unlocked(strict=False):
    path = get_config_path()
    if not os.path.exists(path):
        path = os.path.join(get_base_dir(), "config.json")
    try:
        with open(path, encoding="utf-8-sig") as handle:
            text = handle.read(65_537)
            if len(text) > 65_536:
                raise ValueError("Configuración demasiado grande")
            return normalize_config(json.loads(text))
    except FileNotFoundError:
        return copy.deepcopy(DEFAULT_CONFIG)
    except (ValueError, UnicodeError):
        if strict and path == get_config_path():
            try:
                backup = os.path.join(get_data_dir(), f"config-corrupt-{time.time_ns()}.json")
                shutil.copy2(path, backup)
            except OSError as exc:
                raise ConfigError("No se pudo preservar la configuración corrupta; no se sobrescribirá.") from exc
        return copy.deepcopy(DEFAULT_CONFIG)
    except OSError as exc:
        if strict:
            raise ConfigError("No se pudieron leer los ajustes existentes.") from exc
        return copy.deepcopy(DEFAULT_CONFIG)


def load_config():
    return _read_unlocked()


def _write_unlocked(data):
    path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=get_data_dir(),
            prefix="config-", suffix=".tmp", delete=False,
        ) as handle:
            path = handle.name
            json.dump(normalize_config(data), handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        # Windows puede mantener abierto brevemente un lector ajeno al cerrojo.
        for attempt in range(6):
            try:
                os.replace(path, get_config_path())
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.02)
    finally:
        if path and os.path.exists(path):
            os.remove(path)


def update_config(patch):
    """Transacción completa: bloquear, leer la última versión, mezclar y guardar."""
    if not isinstance(patch, dict):
        raise ConfigError("La actualización debe ser un objeto.")
    try:
        with file_lock():
            data = _read_unlocked(strict=True)
            for key, value in patch.items():
                if isinstance(value, dict) and isinstance(data.get(key), dict):
                    data[key].update(value)
                else:
                    data[key] = value
            data = normalize_config(data)
            _write_unlocked(data)
            return data
    except (OSError, ValueError) as exc:
        raise ConfigError("No se guardaron los ajustes; comprueba permisos y espacio en disco.") from exc


def save_config(config_data):
    """API de compatibilidad. Preferir update_config para cambios parciales."""
    try:
        update_config(config_data)
        return True
    except ConfigError:
        return False


def update_eye_contact_config(intensity=None, enabled=None):
    patch = {}
    if intensity is not None:
        patch["intensity"] = intensity
    if enabled is not None:
        patch["enabled"] = enabled
    return update_config({"eye_contact": patch})


def update_teleprompter_config(scroll_speed=None, font_size=None, opacity=None):
    patch = {key: value for key, value in {
        "scroll_speed": scroll_speed, "font_size": font_size, "opacity": opacity,
    }.items() if value is not None}
    return update_config({"teleprompter": patch})
