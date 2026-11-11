"""Persistencia agrupada fuera del hilo gráfico, sin instalaciones ni red."""

import queue
import threading
import time

from PyQt6.QtCore import QThread, pyqtSignal
import config_manager


class SettingsWriter(QThread):
    saved = pyqtSignal(bool, str)
    idle = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pending = queue.Queue()
        self.finishing = threading.Event()
        self.state_lock = threading.Lock()
        self.idle_event = threading.Event()
        self.idle_event.set()

    def submit(self, patch):
        with self.state_lock:
            if self.finishing.is_set():
                return False
            self.idle_event.clear()
            self.pending.put_nowait(patch)
            return True

    def is_idle(self):
        return self.idle_event.is_set()

    def finish(self):
        with self.state_lock:
            self.finishing.set()

    def run(self):
        batch = {}
        retry_at = 0.0
        while True:
            try:
                patch = self.pending.get(timeout=0.1)
            except queue.Empty:
                if batch and (self.finishing.is_set() or time.monotonic() >= retry_at):
                    patch = {}
                elif self.finishing.is_set():
                    return
                else:
                    continue
            deadline = time.monotonic() + 0.3
            while True:
                for section, values in patch.items():
                    if isinstance(values, dict):
                        batch.setdefault(section, {}).update(values)
                    else:
                        batch[section] = values
                try:
                    if self.finishing.is_set():
                        patch = self.pending.get_nowait()
                    else:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        patch = self.pending.get(timeout=remaining)
                except queue.Empty:
                    break
            try:
                config_manager.update_config(batch)
                batch = {}
                self.saved.emit(True, "Ajustes guardados")
                with self.state_lock:
                    is_idle = self.pending.empty()
                    if is_idle:
                        self.idle_event.set()
                if is_idle:
                    self.idle.emit()
            except config_manager.ConfigError as exc:
                self.saved.emit(False, str(exc))
                retry_at = time.monotonic() + 1
                if self.finishing.is_set():
                    return
