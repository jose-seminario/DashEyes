"""Teleprompter con controles visibles, estado real y previsualización opcional."""

import ctypes
from ctypes import wintypes
import os
import sys

from PyQt6.QtCore import QEvent, QTimer, Qt
from PyQt6.QtGui import QImage, QPixmap, QTextCharFormat, QTextCursor, QColor
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPushButton, QSizeGrip, QSlider, QTextEdit, QVBoxLayout, QWidget,
)

import config_manager
from protocol import drain, offer, offer_latest
from settings_writer import SettingsWriter

WDA_EXCLUDEFROMCAPTURE = 0x11
WM_HOTKEY = 0x0312
HOTKEYS = {101: (0x77, "F8"), 102: (0x78, "F9"), 103: (0x79, "F10"), 104: (0x71, "F2")}


def set_window_capture_invisibility(hwnd):
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        function = user32.SetWindowDisplayAffinity
        function.argtypes = [wintypes.HWND, wintypes.DWORD]
        function.restype = wintypes.BOOL
        return bool(function(hwnd, WDA_EXCLUDEFROMCAPTURE))
    except (OSError, AttributeError):
        return False


class InvisibleTeleprompter(QMainWindow):
    def __init__(self, commands=None, events=None, previews=None, stop=None,
                 script_path=None, settings=None, eye_mode=False):
        super().__init__()
        self.commands, self.events, self.previews, self.stop = commands, events, previews, stop
        self.eye_mode = eye_mode
        self.config = config_manager.normalize_config(settings or config_manager.load_config())
        tele = self.config["teleprompter"]
        self.scroll_speed = tele["scroll_speed"]
        self.font_size = tele["font_size"]
        self.opacity = tele["opacity"]
        self._applied_intensity = self.config["eye_contact"]["intensity"]
        self._applied_enabled = self.config["eye_contact"]["enabled"]
        self._pending_intensity = None
        self._pending_enabled = None
        self._applied_preview = False
        self.is_paused = True
        self._closing = False
        self._force_discard = False
        self._drag_pos = None
        self._hotkeys = set()
        self._slider_labels = {}
        self._screen_connected = False
        self._ready_sent = False
        self._save_failed = False
        self._current_block = -1
        self.writer = SettingsWriter(self)
        self.writer.saved.connect(self._settings_saved)
        self.writer.finished.connect(self._writer_finished)
        self.setWindowTitle("DashEyes — Teleprompter")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(420, 240)
        self.setMaximumSize(1000, 800)
        self.resize(tele["width"], tele["height"])
        self.setAcceptDrops(True)
        self.setStyleSheet("""
            QWidget#panel QLabel { color: #CBD5E1; font-family: 'Segoe UI'; font-size: 11px; }
            QWidget#panel QPushButton { background: #334155; color: white; padding: 4px 8px; border: none; border-radius: 3px; }
            QWidget#panel QPushButton:hover { background: #475569; }
            QWidget#panel QCheckBox { color: #E2E8F0; }
        """)
        self._init_ui()
        self.scroll_timer = QTimer(self)
        self.scroll_timer.timeout.connect(self._scroll_tick)
        self._update_speed()
        self.scroll_timer.start()
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start(100)
        self.geometry_timer = QTimer(self)
        self.geometry_timer.setSingleShot(True)
        self.geometry_timer.timeout.connect(self._save_geometry)
        QApplication.instance().installEventFilter(self)
        self.text_editor.setPlainText(
            "Bienvenido a DashEyes\n\nColoca esta ventana cerca de la webcam. "
            "Usa una columna estrecha para reducir el movimiento lateral de los ojos.\n\n"
            "F8: pausa · F9/F10: velocidad · F2: controles · Esc: cerrar\n\n"
            "En Eye Contact, abre los controles y calibra mirando al objetivo. "
            "El filtro es sutil y no reconstruye miradas muy desviadas."
        )
        if script_path:
            QTimer.singleShot(0, lambda: self._load_script(script_path))
        self._update_pause()
        self.writer.start()

    def _init_ui(self):
        container = QWidget()
        self.panel = container
        container.setObjectName("panel")
        root = QVBoxLayout(container)
        root.setContentsMargins(10, 8, 10, 8)
        row = QHBoxLayout()
        self.led_badge = QLabel("Iniciando cámara…" if self.eye_mode else "Modo lectura · sin cámara")
        row.addWidget(self.led_badge, 1)
        self.pause_btn = QPushButton("Reanudar")
        self.pause_btn.clicked.connect(self._toggle_pause)
        row.addWidget(self.pause_btn)
        hud_btn = QPushButton("Controles")
        hud_btn.clicked.connect(self._toggle_hud)
        row.addWidget(hud_btn)
        close_btn = QPushButton("✕")
        close_btn.setToolTip("Cerrar (Esc)")
        close_btn.clicked.connect(self.close)
        row.addWidget(close_btn)
        root.addLayout(row)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        self.protection_label = QLabel("Protección de captura pendiente")
        self.protection_label.setWordWrap(True)
        root.addWidget(self.protection_label)
        self.text_editor = QTextEdit()
        self.text_editor.setReadOnly(True)
        self.text_editor.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.text_editor.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.text_editor.setAcceptDrops(False)
        self.text_editor.viewport().setAcceptDrops(False)
        self._style_editor()
        root.addWidget(self.text_editor, 1)
        self.hud_frame = QFrame()
        hud = QVBoxLayout(self.hud_frame)
        self.font_slider = self._slider(hud, "Fuente", 16, 44, self.font_size, self._font_changed)
        self.opacity_slider = self._slider(hud, "Fondo", 80, 255, self.opacity, self._opacity_changed)
        self.speed_slider = self._slider(hud, "Velocidad", 1, 10, self.scroll_speed, self._speed_changed)
        self.width_slider = self._slider(hud, "Ancho de lectura", 420, 1000, self.width(), self._width_changed)
        if self.eye_mode:
            eye = self.config["eye_contact"]
            level1 = config_manager.level1_controls_enabled(self.config)
            self.eye_slider = self._slider(hud, "Intensidad máxima", 0, 100, eye["intensity"], self._intensity_changed)
            self.eye_slider.setEnabled(level1)
            self.eye_slider.setVisible(level1)
            if self.eye_slider in self._slider_labels:
                self._slider_labels[self.eye_slider][0].setVisible(level1)
            eye_row = QHBoxLayout()
            self.eye_checkbox = QCheckBox("Habilitar corrección")
            self.eye_checkbox.setChecked(eye["enabled"])
            self.eye_checkbox.toggled.connect(self._eye_toggled)
            eye_row.addWidget(self.eye_checkbox)
            calibrate = QPushButton("Calibrar mirando al objetivo")
            calibrate.clicked.connect(lambda: self._send({"cmd": "CALIBRATE"}))
            eye_row.addWidget(calibrate)
            hud.addLayout(eye_row)
            self.preview_checkbox = QCheckBox("Ver original / corregido (consume CPU adicional)")
            self.preview_checkbox.toggled.connect(self._preview_toggled)
            hud.addWidget(self.preview_checkbox)
        self.hud_frame.hide()
        root.addWidget(self.hud_frame)
        self.preview_container = QWidget()
        preview_layout = QVBoxLayout(self.preview_container)
        preview_layout.addWidget(QLabel("Original (izquierda) / Corregido (derecha)"))
        self.preview_label = QLabel("Esperando imagen…")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(110)
        self.preview_label.setMaximumHeight(150)
        preview_layout.addWidget(self.preview_label)
        self.preview_container.hide()
        root.addWidget(self.preview_container)
        self.notice_label = QLabel("F8 pausa · F9/F10 velocidad · F2 controles · Esc cerrar")
        self.notice_label.setWordWrap(True)
        root.addWidget(self.notice_label)
        grip_row = QHBoxLayout()
        grip_row.addStretch()
        grip_row.addWidget(QSizeGrip(self))
        root.addLayout(grip_row)
        self.setCentralWidget(container)

    def _slider(self, layout, label, low, high, value, callback):
        row = QHBoxLayout()
        text = QLabel(f"{label}: {value}")
        row.addWidget(text)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(low, high)
        slider.setValue(value)
        slider.valueChanged.connect(lambda v: text.setText(f"{label}: {v}"))
        slider.valueChanged.connect(callback)
        self._slider_labels[slider] = (text, label)
        row.addWidget(slider, 1)
        layout.addLayout(row)
        return slider

    def _style_editor(self):
        if hasattr(self, "panel"):
            self.panel.setStyleSheet(
                f"QWidget#panel {{ background: rgba(15, 23, 42, {self.opacity}); border-radius: 8px; }}"
            )
        self.text_editor.setStyleSheet(f"""
            QTextEdit {{ background: rgba(15, 23, 42, 0); color: #CBD5E1;
            font-family: 'Segoe UI'; font-size: {self.font_size}px;
            border: 1px solid #334155; border-radius: 6px; padding: 12px; }}
        """)

    def _settings_saved(self, success, message):
        if not success:
            self._save_failed = True
            self.notice_label.setText(message)
            self.notice_label.setStyleSheet("color: #FBBF24")
        elif self._save_failed:
            self._save_failed = False
            self.notice_label.setText("Ajustes guardados")
            self.notice_label.setStyleSheet("color: #94A3B8")

    def _send(self, message):
        cmd = message.get("cmd") if isinstance(message, dict) else None
        sent = offer_latest(self.commands, message) if cmd in {"SET_INTENSITY", "TOGGLE_CORRECTION", "PREVIEW"} else offer(self.commands, message)
        if not sent:
            self.notice_label.setText("No se pudo enviar el control; vuelve a intentarlo.")
            return False
        return True

    def _reconcile_eye_controls(self, intensity=None, enabled=None):
        if intensity is not None and hasattr(self, "eye_slider"):
            self._applied_intensity = int(intensity)
            if self._pending_intensity == self._applied_intensity:
                self._pending_intensity = None
            if self._pending_intensity is None and not self.eye_slider.isSliderDown() and self.eye_slider.value() != self._applied_intensity:
                self.eye_slider.blockSignals(True)
                self.eye_slider.setValue(self._applied_intensity)
                self.eye_slider.blockSignals(False)
                if hasattr(self, "_slider_labels") and self.eye_slider in self._slider_labels:
                    label_widget, prefix = self._slider_labels[self.eye_slider]
                    label_widget.setText(f"{prefix}: {self._applied_intensity}")
        if enabled is not None and hasattr(self, "eye_checkbox"):
            self._applied_enabled = bool(enabled)
            if self._pending_enabled == self._applied_enabled:
                self._pending_enabled = None
            if self._pending_enabled is None and self.eye_checkbox.isChecked() != self._applied_enabled:
                self.eye_checkbox.blockSignals(True)
                self.eye_checkbox.setChecked(self._applied_enabled)
                self.eye_checkbox.blockSignals(False)

    def _font_changed(self, value):
        self.font_size = value
        self._style_editor()
        self.writer.submit({"teleprompter": {"font_size": value}})

    def _opacity_changed(self, value):
        self.opacity = value
        self._style_editor()
        self.writer.submit({"teleprompter": {"opacity": value}})

    def _width_changed(self, value):
        self.resize(value, self.height())

    def _speed_changed(self, value):
        self.scroll_speed = value
        self._update_speed()
        self._update_pause()
        self.writer.submit({"teleprompter": {"scroll_speed": value}})

    def _change_speed(self, delta):
        self.speed_slider.setValue(max(1, min(10, self.scroll_speed + delta)))

    def _update_speed(self):
        self.scroll_timer.setInterval(max(12, 70 - self.scroll_speed * 5))

    def _toggle_pause(self):
        self.is_paused = not self.is_paused
        self._update_pause()

    def _update_pause(self):
        self.pause_btn.setText("Reanudar" if self.is_paused else "Pausar")
        if not self.eye_mode:
            self.status_label.setText("Lectura pausada" if self.is_paused else f"Lectura · velocidad {self.scroll_speed}")

    def _toggle_hud(self):
        self.hud_frame.setVisible(not self.hud_frame.isVisible())

    def _intensity_changed(self, value):
        if self._send({"cmd": "SET_INTENSITY", "intensity": value}):
            self._pending_intensity = value
            self.writer.submit({"eye_contact": {"intensity": value}})
        else:
            self._pending_intensity = None
            self._reconcile_eye_controls(intensity=self._applied_intensity)

    def _eye_toggled(self, enabled):
        if self._send({"cmd": "TOGGLE_CORRECTION", "enabled": enabled}):
            self._pending_enabled = enabled
            self.writer.submit({"eye_contact": {"enabled": enabled}})
        else:
            self._pending_enabled = None
            self._reconcile_eye_controls(enabled=self._applied_enabled)

    def _preview_toggled(self, enabled):
        if self._send({"cmd": "PREVIEW", "enabled": enabled}):
            self._applied_preview = enabled
            self.preview_container.setVisible(enabled)
            if not enabled:
                self.preview_label.clear()
        elif hasattr(self, "preview_checkbox"):
            self.preview_checkbox.blockSignals(True)
            self.preview_checkbox.setChecked(self._applied_preview)
            self.preview_checkbox.blockSignals(False)
            self.preview_container.setVisible(self._applied_preview)

    def _scroll_tick(self):
        scrollbar = self.text_editor.verticalScrollBar()
        if not self.is_paused:
            scrollbar.setValue(scrollbar.value() + 1)
            if scrollbar.maximum() > 0 and scrollbar.value() >= scrollbar.maximum():
                self.is_paused = True
                self._update_pause()
        cursor = self.text_editor.cursorForPosition(self.text_editor.viewport().rect().topLeft())
        block = cursor.blockNumber()
        if block != self._current_block:
            self._current_block = block
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
            selection.format = QTextCharFormat()
            selection.format.setForeground(QColor("#38BDF8"))
            self.text_editor.setExtraSelections([selection])

    def _poll(self):
        if self.stop is not None and self.stop.is_set():
            self.close()
            return
        for message in drain(self.events):
            kind = message.get("type")
            if kind == "STATUS":
                state = message.get("state", "UNCERTAIN")
                names = {"CORRECTING": "Corrección aplicada", "NATURAL": "Mirada natural",
                         "UNCALIBRATED": "Sin calibrar", "CALIBRATING": "Calibrando",
                         "PAUSED": "Corrección apagada", "NO_FACE": "Sin rostro",
                         "POSE": "Pose no fiable", "BLINK": "Parpadeo / ojo no visible",
                         "UNCERTAIN": "Sin corrección", "RECONNECTING": "Reconectando"}
                self.led_badge.setText(names.get(state, state))
                color = "#22C55E" if state in ("CORRECTING", "NATURAL") else "#FBBF24"
                self.led_badge.setStyleSheet(f"color: {color}")
                self.status_label.setText(f"{message.get('message', '')}  {message.get('fps', 0)} FPS · {message.get('processing_ms', 0)} ms")
                self._reconcile_eye_controls(
                    intensity=message.get("intensity") if isinstance(message.get("intensity"), int) else None,
                    enabled=message.get("enabled") if isinstance(message.get("enabled"), bool) else None,
                )
            elif kind in ("WARNING", "ERROR", "READY"):
                self.status_label.setText(message.get("message", ""))
                if kind == "WARNING" and message.get("reconcile"):
                    self._pending_intensity = None
                    self._pending_enabled = None
                    self._reconcile_eye_controls(intensity=self._applied_intensity, enabled=self._applied_enabled)
        image = None
        for data in drain(self.previews, limit=2):
            image = data
        if image and self.preview_container.isVisible():
            decoded = QImage.fromData(image, "JPG")
            if not decoded.isNull():
                pixmap = QPixmap.fromImage(decoded)
                self.preview_label.setPixmap(pixmap.scaled(self.preview_label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def _load_script(self, path):
        try:
            if not path.lower().endswith((".txt", ".md")):
                raise ValueError("Selecciona un archivo .txt o .md.")
            if os.path.getsize(path) > 2_000_000:
                raise ValueError("El guion supera el límite de 2 MB.")
            with open(path, encoding="utf-8-sig") as handle:
                text = handle.read(2_000_001)
            self.text_editor.setPlainText(text)
            self.text_editor.verticalScrollBar().setValue(0)
            self.is_paused = True
            self._current_block = -1
            self._update_pause()
        except (OSError, ValueError, UnicodeError) as exc:
            QMessageBox.warning(self, "No se pudo cargar el guion", f"{exc}\nEl texto anterior se conserva. Usa codificación UTF-8.")

    def dragEnterEvent(self, event):
        if any(url.isLocalFile() and url.toLocalFile().lower().endswith((".txt", ".md")) for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith((".txt", ".md")):
                self._load_script(url.toLocalFile())
                event.acceptProposedAction()
                return

    def eventFilter(self, obj, event):
        if self._closing:
            return super().eventFilter(obj, event)
        if self.isActiveWindow() and event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            key = event.key()
            actions = {Qt.Key.Key_Space: self._toggle_pause, Qt.Key.Key_Escape: self.close,
                       Qt.Key.Key_Up: lambda: self._change_speed(1), Qt.Key.Key_Down: lambda: self._change_speed(-1),
                       Qt.Key.Key_F2: self._toggle_hud, Qt.Key.Key_F8: self._toggle_pause,
                       Qt.Key.Key_F9: lambda: self._change_speed(-1), Qt.Key.Key_F10: lambda: self._change_speed(1)}
            native = {Qt.Key.Key_F8: 101, Qt.Key.Key_F9: 102, Qt.Key.Key_F10: 103, Qt.Key.Key_F2: 104}
            if key in actions and native.get(key) not in self._hotkeys:
                if event.type() == QEvent.Type.ShortcutOverride:
                    event.accept()
                elif not event.isAutoRepeat() or key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                    actions[key]()
                return True
        if obj == self.text_editor.viewport():
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if event.type() == QEvent.Type.MouseMove and self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
                self.move(event.globalPosition().toPoint() - self._drag_pos)
                return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._drag_pos = None
        return super().eventFilter(obj, event)

    def _protect(self):
        protected = set_window_capture_invisibility(int(self.winId()))
        self.protection_label.setText("Exclusión de captura aceptada por Windows; verifica tu app de pantalla compartida." if protected else "AVISO: protección de captura no disponible. El guion podría ser visible.")
        self.protection_label.setStyleSheet("color: #94A3B8" if protected else "color: #FBBF24")

    def showEvent(self, event):
        super().showEvent(event)
        self._protect()
        if not self._screen_connected:
            self.windowHandle().screenChanged.connect(lambda _: self._protect())
            self._screen_connected = True
            geometry = self.screen().availableGeometry()
            self.move(geometry.center().x() - self.width() // 2, geometry.top() + 20)
        try:
            self.user32 = ctypes.WinDLL("user32", use_last_error=True)
            self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            self.user32.RegisterHotKey.restype = wintypes.BOOL
            self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            self.user32.UnregisterHotKey.restype = wintypes.BOOL
            failed = []
            for identifier, (vk, name) in HOTKEYS.items():
                if identifier not in self._hotkeys:
                    if self.user32.RegisterHotKey(int(self.winId()), identifier, 0x4000, vk):
                        self._hotkeys.add(identifier)
                    else:
                        failed.append(name)
            if failed:
                self.notice_label.setText("Atajos globales ocupados: " + ", ".join(failed) + ". Funcionan al enfocar esta ventana.")
        except (OSError, AttributeError):
            self.notice_label.setText("Atajos disponibles solo con esta ventana enfocada.")
        if not self._ready_sent:
            offer(self.commands, {"type": "UI_READY"})
            self._ready_sent = True

    def nativeEvent(self, event_type, message):
        if event_type in (b"windows_generic_MSG", "windows_generic_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                actions = {101: self._toggle_pause, 102: lambda: self._change_speed(-1),
                           103: lambda: self._change_speed(1), 104: self._toggle_hud}
                action = actions.get(msg.wParam)
                if action:
                    action()
                    return True, 0
        return super().nativeEvent(event_type, message)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "width_slider") and self.width_slider.value() != self.width():
            self.width_slider.blockSignals(True)
            self.width_slider.setValue(self.width())
            if self.width_slider in self._slider_labels:
                label_widget, prefix = self._slider_labels[self.width_slider]
                label_widget.setText(f"{prefix}: {self.width()}")
            self.width_slider.blockSignals(False)
        if hasattr(self, "geometry_timer") and not self._closing:
            self.geometry_timer.start(400)

    def _save_geometry(self):
        self.writer.submit({"teleprompter": {"width": self.width(), "height": self.height()}})

    def _writer_finished(self):
        if self._closing:
            self.close()

    def closeEvent(self, event):
        if not self._closing:
            self._closing = True
            self.poll_timer.stop()
            self.scroll_timer.stop()
            self.centralWidget().setEnabled(False)
            self.geometry_timer.stop()
            self._save_geometry()
            self.writer.finish()
        if self.writer.isRunning():
            event.ignore()
            return
        if self.writer.has_unsaved_changes() and not self._force_discard:
            if not self.writer.flush_sync():
                forced_by_supervisor = self.stop is not None and self.stop.is_set()
                if not forced_by_supervisor and self.isVisible():
                    box = QMessageBox(self)
                    box.setIcon(QMessageBox.Icon.Warning)
                    box.setWindowTitle("Ajustes sin guardar")
                    box.setText(f"No se pudieron guardar los últimos ajustes.\n{self.writer.last_error}")
                    retry_btn = box.addButton("Reintentar", QMessageBox.ButtonRole.AcceptRole)
                    discard_btn = box.addButton("Cerrar sin guardar", QMessageBox.ButtonRole.DestructiveRole)
                    cancel_btn = box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
                    box.exec()
                    clicked = box.clickedButton()
                    if clicked == retry_btn and self.writer.flush_sync():
                        pass
                    elif clicked == cancel_btn or (clicked == retry_btn and self.writer.has_unsaved_changes()):
                        self._closing = False
                        self.centralWidget().setEnabled(True)
                        old_writer = self.writer
                        self.writer = SettingsWriter(self)
                        self.writer.adopt_unsaved(old_writer)
                        self.writer.saved.connect(self._settings_saved)
                        self.writer.finished.connect(self._writer_finished)
                        self.writer.start()
                        self.poll_timer.start(100)
                        self.scroll_timer.start()
                        event.ignore()
                        return
                    elif clicked == discard_btn:
                        self._force_discard = True
                        offer(self.commands, {"type": "WARNING", "message": "Teleprompter cerrado descartando ajustes no guardados."})
                        self.writer.discard_unsaved()
                else:
                    self._force_discard = True
                    offer(self.commands, {"type": "ERROR", "message": "No se pudieron guardar los ajustes finales del teleprompter."})
                    self.writer.discard_unsaved()
        self.poll_timer.stop()
        self.scroll_timer.stop()
        QApplication.instance().removeEventFilter(self)
        for identifier in self._hotkeys:
            self.user32.UnregisterHotKey(int(self.winId()), identifier)
        self._hotkeys.clear()
        if self.stop is not None:
            self.stop.set()
        event.accept()
        QApplication.quit()


def run_teleprompter_process(commands=None, events=None, previews=None, stop=None,
                             script_path=None, settings=None, eye_mode=False):
    app = QApplication([sys.argv[0]])
    window = InvisibleTeleprompter(commands, events, previews, stop, script_path, settings, eye_mode)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run_teleprompter_process()
