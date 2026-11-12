"""Persistencia agrupada fuera del hilo gráfico, sin instalaciones ni red."""

import copy
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
        self._unsaved_batch = {}
        self.last_error = ""
        self._discard_epoch = 0

    @staticmethod
    def _merge_patch(target, patch):
        for section, values in patch.items():
            if isinstance(values, dict) and isinstance(target.get(section), dict):
                target[section].update(values)
            elif isinstance(values, dict):
                target[section] = copy.deepcopy(values)
            else:
                target[section] = values

    def adopt_unsaved(self, other):
        if other is None:
            return
        with other.state_lock:
            unsaved = copy.deepcopy(other._unsaved_batch)
            while True:
                try:
                    patch = other.pending.get_nowait()
                    self._merge_patch(unsaved, patch)
                except queue.Empty:
                    break
            error = other.last_error
        if unsaved:
            with self.state_lock:
                self._merge_patch(self._unsaved_batch, unsaved)
                self.last_error = error

    def submit(self, patch):
        with self.state_lock:
            if self.finishing.is_set():
                return False
            self.idle_event.clear()
            self.pending.put_nowait(patch)
            return True

    def is_idle(self):
        return self.idle_event.is_set()

    def has_unsaved_changes(self):
        with self.state_lock:
            return bool(self._unsaved_batch) or not self.pending.empty() or not self.idle_event.is_set()

    def discard_unsaved(self):
        with self.state_lock:
            self._discard_epoch += 1
            while True:
                try:
                    self.pending.get_nowait()
                except queue.Empty:
                    break
            self._unsaved_batch.clear()
            self.last_error = ""
            self.idle_event.set()
        self.idle.emit()

    def flush_sync(self):
        with self.state_lock:
            while True:
                try:
                    patch = self.pending.get_nowait()
                    self._merge_patch(self._unsaved_batch, patch)
                except queue.Empty:
                    break
            batch = copy.deepcopy(self._unsaved_batch)
            epoch = self._discard_epoch
        if not batch:
            with self.state_lock:
                self.last_error = ""
                self.idle_event.set()
            self.idle.emit()
            return True
        try:
            config_manager.update_config(batch)
            with self.state_lock:
                if self._discard_epoch == epoch:
                    self._unsaved_batch.clear()
                    self.last_error = ""
                if self.pending.empty():
                    self.idle_event.set()
            self.saved.emit(True, "Ajustes guardados")
            self.idle.emit()
            return True
        except config_manager.ConfigError as exc:
            with self.state_lock:
                if self._discard_epoch == epoch:
                    self.last_error = str(exc)
                if self.pending.empty():
                    self.idle_event.set()
            self.saved.emit(False, str(exc))
            return False

    def finish(self):
        with self.state_lock:
            self.finishing.set()

    def run(self):
        while True:
            try:
                patch = self.pending.get(timeout=0.05)
            except queue.Empty:
                if self.finishing.is_set():
                    with self.state_lock:
                        has_unsaved = bool(self._unsaved_batch)
                    if has_unsaved:
                        patch = {}
                    else:
                        with self.state_lock:
                            if self.pending.empty():
                                self.idle_event.set()
                        return
                else:
                    continue
            deadline = time.monotonic() + 0.3
            incoming = {}
            while True:
                self._merge_patch(incoming, patch)
                try:
                    if self.finishing.is_set():
                        patch = self.pending.get_nowait()
                    else:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        patch = self.pending.get(timeout=min(0.05, remaining))
                except queue.Empty:
                    if self.finishing.is_set() or time.monotonic() >= deadline:
                        break
            with self.state_lock:
                self._merge_patch(self._unsaved_batch, incoming)
                batch = copy.deepcopy(self._unsaved_batch)
                epoch = self._discard_epoch
            if not batch:
                with self.state_lock:
                    if self.pending.empty():
                        self.idle_event.set()
                continue
            try:
                config_manager.update_config(batch)
                with self.state_lock:
                    if self._discard_epoch == epoch:
                        self._unsaved_batch.clear()
                        self.last_error = ""
                    is_idle = self.pending.empty()
                    if is_idle:
                        self.idle_event.set()
                self.saved.emit(True, "Ajustes guardados")
                if is_idle:
                    self.idle.emit()
            except config_manager.ConfigError as exc:
                with self.state_lock:
                    if self._discard_epoch == epoch:
                        self.last_error = str(exc)
                    if self.pending.empty():
                        self.idle_event.set()
                self.saved.emit(False, str(exc))
                if self.finishing.is_set():
                    return
