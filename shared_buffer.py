"""Doble búfer sin bloqueos (SeqLock por ranura) en memoria compartida para Nivel 2."""

from multiprocessing import shared_memory
import os
import secrets
import struct
import time

import cv2
import numpy as np

PREVIEW_WIDTH = 320
PREVIEW_HEIGHT = 180
MAX_PREVIEW_WIDTH = 640
MAX_PREVIEW_HEIGHT = 180
PREVIEW_CHANNELS = 3

# Cabecera global (64 bytes):
# published_seq (uint64), published_slot (uint64), created_at (double),
# slot0_seq (uint64), slot1_seq (uint64), reservado (24 bytes)
GLOBAL_HEADER_STRUCT = struct.Struct("<QQdQQ24x")
# Metadatos por ranura (32 bytes):
# slot_seq (uint64), width (uint32), height (uint32), channels (uint32), pad (uint32), timestamp (double)
SLOT_META_STRUCT = struct.Struct("<QIIIId")

HEADER_SIZE = GLOBAL_HEADER_STRUCT.size
SLOT_META_SIZE = SLOT_META_STRUCT.size
SLOT_PIXEL_SIZE = MAX_PREVIEW_WIDTH * MAX_PREVIEW_HEIGHT * PREVIEW_CHANNELS
SLOT_STRIDE = SLOT_META_SIZE + SLOT_PIXEL_SIZE
TOTAL_BUFFER_SIZE = HEADER_SIZE + (SLOT_STRIDE * 2)


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
    """Gestiona lectura y escritura sin cerrojos mediante secuencia impar/par por ranura."""

    def __init__(self, shm, owner=False):
        self.shm = shm
        self.owner = bool(owner)
        self.closed = False

    @property
    def name(self):
        return self.shm.name if self.shm is not None else None

    @classmethod
    def create(cls):
        shm = None
        for _ in range(4):
            candidate = f"dasheyes_p2_{os.getpid()}_{secrets.token_hex(4)}"
            try:
                shm = shared_memory.SharedMemory(name=candidate, create=True, size=TOTAL_BUFFER_SIZE)
                break
            except FileExistsError:
                continue
        if shm is None:
            shm = shared_memory.SharedMemory(create=True, size=TOTAL_BUFFER_SIZE)
        shm.buf[:HEADER_SIZE + (SLOT_STRIDE * 2)] = b"\x00" * TOTAL_BUFFER_SIZE
        GLOBAL_HEADER_STRUCT.pack_into(shm.buf, 0, 0, 0, time.monotonic(), 0, 0)
        return cls(shm, owner=True)

    @classmethod
    def attach(cls, name):
        if not name or not isinstance(name, str):
            return None
        shm = shared_memory.SharedMemory(name=name, create=False)
        _unregister_child_resource(shm)
        return cls(shm, owner=False)

    def write_bgr_frame(self, bgr_frame, original_bgr=None, now=None):
        """Escala a 320x180 (o 640x180 con comparativa), convierte a RGB y publica con SeqLock."""
        if self.closed or self.shm is None or bgr_frame is None:
            return False
        try:
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
            if nbytes <= 0 or nbytes > SLOT_PIXEL_SIZE:
                return False
            pub_seq, pub_slot, created_at, s0_seq, s1_seq = GLOBAL_HEADER_STRUCT.unpack_from(self.shm.buf, 0)
            next_slot = 0 if pub_seq == 0 else (1 - (pub_slot & 1))
            slot_offset = HEADER_SIZE + (next_slot * SLOT_STRIDE)
            pixel_offset = slot_offset + SLOT_META_SIZE

            # Paso 1: marcar la ranura destino como EN ESCRITURA (secuencia impar).
            writing_seq = (pub_seq * 2) + 1
            timestamp = time.monotonic() if now is None else float(now)
            SLOT_META_STRUCT.pack_into(self.shm.buf, slot_offset, writing_seq, w, h, c, 0, timestamp)

            # Paso 2: copiar el bloque de píxeles RGB pre-escalado.
            self.shm.buf[pixel_offset:pixel_offset + nbytes] = rgb.reshape(-1).tobytes()

            # Paso 3: marcar la ranura destino como COMPLETADA (secuencia par) y publicar en cabecera.
            completed_seq = writing_seq + 1
            SLOT_META_STRUCT.pack_into(self.shm.buf, slot_offset, completed_seq, w, h, c, 0, timestamp)
            new_pub_seq = pub_seq + 1
            if next_slot == 0:
                s0_seq = completed_seq
            else:
                s1_seq = completed_seq
            GLOBAL_HEADER_STRUCT.pack_into(
                self.shm.buf, 0,
                new_pub_seq, next_slot, created_at, s0_seq, s1_seq,
            )
            return True
        except (BufferError, ValueError, OSError):
            return False

    def read_latest(self, last_seq=0, max_age=1.5, now=None):
        """Lee la última ranura publicada verificando paridad e invariancia de secuencia."""
        if self.closed or self.shm is None:
            return "CLOSED", last_seq, None
        try:
            current_time = time.monotonic() if now is None else float(now)
            for _ in range(2):
                pub_seq, slot, created_at, _, _ = GLOBAL_HEADER_STRUCT.unpack_from(self.shm.buf, 0)
                if pub_seq == 0:
                    if max_age is not None and created_at > 0 and (current_time - created_at) > max_age:
                        return "STALE", 0, None
                    return "EMPTY", 0, None
                if slot not in (0, 1):
                    return "INVALID", last_seq, None
                slot_offset = HEADER_SIZE + (slot * SLOT_STRIDE)
                pixel_offset = slot_offset + SLOT_META_SIZE
                seq1, width, height, channels, _, timestamp = SLOT_META_STRUCT.unpack_from(self.shm.buf, slot_offset)
                # Si la secuencia de la ranura es 0 o impar, el escritor la está modificando ahora mismo.
                if seq1 == 0 or (seq1 & 1) != 0:
                    continue
                if max_age is not None and (current_time - timestamp) > max_age:
                    return "STALE", pub_seq, None
                if pub_seq == last_seq:
                    return "UNCHANGED", pub_seq, None
                if (
                    width <= 0 or width > MAX_PREVIEW_WIDTH
                    or height <= 0 or height > MAX_PREVIEW_HEIGHT
                    or channels != PREVIEW_CHANNELS
                ):
                    return "INVALID", last_seq, None
                nbytes = width * height * channels
                raw = bytes(self.shm.buf[pixel_offset:pixel_offset + nbytes])
                seq2 = SLOT_META_STRUCT.unpack_from(self.shm.buf, slot_offset)[0]
                if seq1 == seq2 and (seq2 & 1) == 0:
                    return "OK", pub_seq, (width, height, raw)
            return "UNCHANGED", last_seq, None
        except (BufferError, ValueError, OSError):
            return "CLOSED", last_seq, None

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
