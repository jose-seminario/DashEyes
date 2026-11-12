"""Doble búfer sin bloqueos en memoria compartida para la vista previa de Nivel 2."""

from multiprocessing import shared_memory
import struct
import time

import cv2
import numpy as np

PREVIEW_WIDTH = 320
PREVIEW_HEIGHT = 180
MAX_PREVIEW_WIDTH = 640
MAX_PREVIEW_HEIGHT = 180
PREVIEW_CHANNELS = 3

HEADER_STRUCT = struct.Struct("<QQIIId28x")
HEADER_SIZE = HEADER_STRUCT.size
SLOT_SIZE = MAX_PREVIEW_WIDTH * MAX_PREVIEW_HEIGHT * PREVIEW_CHANNELS
TOTAL_BUFFER_SIZE = HEADER_SIZE + (SLOT_SIZE * 2)


def _unregister_child_resource(shm):
    """Evita que un proceso hijo elimine el segmento que pertenece al orquestador."""
    try:
        from multiprocessing import resource_tracker
        name = getattr(shm, "_name", None) or getattr(shm, "name", None)
        if name:
            resource_tracker.unregister(name, "shared_memory")
    except Exception:
        pass


class SharedPreviewBuffer:
    """Gestiona lectura y escritura sin cerrojos sobre dos ranuras de imagen."""

    def __init__(self, shm, owner=False):
        self.shm = shm
        self.owner = bool(owner)
        self.closed = False

    @property
    def name(self):
        return self.shm.name if self.shm is not None else None

    @classmethod
    def create(cls):
        shm = shared_memory.SharedMemory(create=True, size=TOTAL_BUFFER_SIZE)
        shm.buf[:HEADER_SIZE] = b"\x00" * HEADER_SIZE
        return cls(shm, owner=True)

    @classmethod
    def attach(cls, name):
        if not name or not isinstance(name, str):
            return None
        shm = shared_memory.SharedMemory(name=name, create=False)
        _unregister_child_resource(shm)
        return cls(shm, owner=False)

    def write_bgr_frame(self, bgr_frame, original_bgr=None, now=None):
        """Escala a 320x180 (o 640x180 con comparativa), convierte a RGB y publica sin bloqueo."""
        if self.closed or self.shm is None or bgr_frame is None:
            return False
        target_h = PREVIEW_HEIGHT
        target_w = PREVIEW_WIDTH
        resized_current = cv2.resize(bgr_frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
        if original_bgr is not None:
            resized_orig = cv2.resize(original_bgr, (target_w, target_h), interpolation=cv2.INTER_AREA)
            composed_bgr = np.hstack((resized_orig, resized_current))
        else:
            composed_bgr = resized_current
        rgb = cv2.cvtColor(composed_bgr, cv2.COLOR_BGR2RGB)
        rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
        h, w, c = rgb.shape
        nbytes = h * w * c
        if nbytes <= 0 or nbytes > SLOT_SIZE:
            return False
        seq, current_slot, _, _, _, _ = HEADER_STRUCT.unpack_from(self.shm.buf, 0)
        next_slot = 0 if seq == 0 else (1 - (current_slot & 1))
        offset = HEADER_SIZE + (next_slot * SLOT_SIZE)
        self.shm.buf[offset:offset + nbytes] = rgb.reshape(-1).tobytes()
        timestamp = time.monotonic() if now is None else float(now)
        HEADER_STRUCT.pack_into(
            self.shm.buf, 0,
            seq + 1, next_slot, w, h, c, timestamp,
        )
        return True

    def read_latest(self, last_seq=0, max_age=1.5, now=None):
        """Lee la última ranura publicada verificando consistencia y antigüedad."""
        if self.closed or self.shm is None:
            return "CLOSED", last_seq, None
        for _ in range(2):
            seq1, slot, width, height, channels, timestamp = HEADER_STRUCT.unpack_from(self.shm.buf, 0)
            if seq1 == 0:
                return "EMPTY", 0, None
            current_time = time.monotonic() if now is None else float(now)
            if max_age is not None and (current_time - timestamp) > max_age:
                return "STALE", seq1, None
            if seq1 == last_seq:
                return "UNCHANGED", seq1, None
            if (
                slot not in (0, 1)
                or width <= 0 or width > MAX_PREVIEW_WIDTH
                or height <= 0 or height > MAX_PREVIEW_HEIGHT
                or channels != PREVIEW_CHANNELS
            ):
                return "INVALID", last_seq, None
            nbytes = width * height * channels
            offset = HEADER_SIZE + (slot * SLOT_SIZE)
            raw = bytes(self.shm.buf[offset:offset + nbytes])
            seq2 = HEADER_STRUCT.unpack_from(self.shm.buf, 0)[0]
            if seq1 == seq2:
                return "OK", seq1, (width, height, raw)
        return "UNCHANGED", last_seq, None

    def close(self):
        if self.closed or self.shm is None:
            return
        self.closed = True
        try:
            self.shm.close()
        except Exception:
            pass
        if self.owner:
            try:
                self.shm.unlink()
            except FileNotFoundError:
                pass
            except Exception:
                pass
        self.shm = None
