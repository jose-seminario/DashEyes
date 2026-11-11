"""
teleprompter_ui.py - Módulo A: Teleprompter Invisible para Windows (PyQt6)
Incluye Atajos Globales (RegisterHotKey F8/F9/F10), Drag & Drop de guiones,
arrastre desde cualquier área de la ventana, botón de cierre, barra retráctil HUD
de controles (fuente, opacidad, velocidad, mirada) y distintivo LED real de estado.
"""

import sys
import os
import ctypes
from ctypes import wintypes
import config_manager
from PyQt6.QtCore import Qt, QTimer, QEvent
from PyQt6.QtGui import QKeyEvent, QMouseEvent, QDropEvent, QDragEnterEvent
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTextEdit,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QLabel,
    QSlider,
    QPushButton,
    QFrame,
)

WDA_EXCLUDEFROMCAPTURE = 0x00000011

# Constantes Win32 para Global Hotkeys
WM_HOTKEY = 0x0312
HOTKEY_F8_ID = 101
HOTKEY_F9_ID = 102
HOTKEY_F10_ID = 103
VK_F8 = 0x77
VK_F9 = 0x78
VK_F10 = 0x79


def set_window_capture_invisibility(hwnd: int) -> bool:
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        SetWindowDisplayAffinity = user32.SetWindowDisplayAffinity
        SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        SetWindowDisplayAffinity.restype = wintypes.BOOL
        return bool(SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))
    except Exception:
        return False


class InvisibleTeleprompter(QMainWindow):
    def __init__(self, script_path=None, ipc_pipe=None):
        super().__init__()
        self.script_path = script_path
        self.ipc_pipe = ipc_pipe

        self.config = config_manager.load_config()
        self.enable_level1 = self.config.get("enable_level1_controls", True)
        self.is_eye_contact_active = bool(self.ipc_pipe is not None)
        self.eye_contact_intensity = self.config.get("eye_contact", {}).get("intensity", 50)
        self.eye_contact_enabled = self.config.get("eye_contact", {}).get("enabled", True)

        tele_cfg = self.config.get("teleprompter", {})
        self.is_paused = False
        self.scroll_speed = int(tele_cfg.get("scroll_speed", 3))
        self.base_interval_ms = 40
        self.current_font_size = int(tele_cfg.get("font_size", 24))
        self.current_opacity = int(tele_cfg.get("opacity", 215))  # Canal alfa 80-255

        self._drag_pos = None
        self._screen_listener_connected = False
        self._current_screen = None
        self._hotkeys_registered = False

        self._init_window_properties()
        self._init_ui_components()
        self._init_scroll_timer()
        self._load_initial_script()

    def _init_window_properties(self):
        self.setWindowTitle("DashEyes - Invisible Teleprompter")
        self.resize(680, 360)
        self.setAcceptDrops(True)

        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def _init_ui_components(self):
        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(6)

        # Barra Superior: Estado, Atajos, HUD y Cerrar
        self.header_bar = QHBoxLayout()

        self.led_badge = QLabel()
        self._update_led_badge()

        self.status_label = QLabel(f"Vel: {self.scroll_speed} | [F8] Pausa | [F9/F10] Vel | [F2] HUD | [Esc] Salir")
        self.status_label.setStyleSheet("""
            QLabel {
                color: #A0AEC0;
                font-family: 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 600;
                background-color: transparent;
            }
        """)

        self.hud_toggle_btn = QPushButton("⚙ HUD")
        self.hud_toggle_btn.setFixedSize(50, 20)
        self.hud_toggle_btn.setStyleSheet("""
            QPushButton {
                background-color: #334155;
                color: #F8FAFC;
                font-size: 10px;
                font-weight: bold;
                border-radius: 3px;
                border: none;
            }
            QPushButton:hover { background-color: #475569; }
        """)
        self.hud_toggle_btn.clicked.connect(self._toggle_hud)

        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(22, 20)
        self.close_btn.setToolTip("Cerrar Teleprompter (Esc)")
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: #334155;
                color: #F8FAFC;
                font-size: 11px;
                font-weight: bold;
                border-radius: 3px;
                border: none;
            }
            QPushButton:hover { background-color: #EF4444; color: #FFFFFF; }
        """)
        self.close_btn.clicked.connect(self.close)

        self.header_bar.addWidget(self.led_badge)
        self.header_bar.addWidget(self.status_label)
        self.header_bar.addStretch()
        self.header_bar.addWidget(self.hud_toggle_btn)
        self.header_bar.addWidget(self.close_btn)
        root_layout.addLayout(self.header_bar)

        # Editor de Texto Principal (sin robar foco de teclado y permitiendo arrastrar la ventana)
        self.text_editor = QTextEdit()
        self.text_editor.setReadOnly(True)
        self.text_editor.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.text_editor.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.text_editor.viewport().installEventFilter(self)
        self._update_editor_style()
        root_layout.addWidget(self.text_editor)

        # Barra Retráctil HUD (Opacidad, Fuente, Velocidad y Mirada)
        self.hud_frame = QFrame()
        self.hud_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(30, 41, 59, 230);
                border-radius: 6px;
                padding: 4px;
            }
            QLabel { color: #E2E8F0; font-size: 11px; font-weight: 600; }
        """)
        hud_layout = QHBoxLayout(self.hud_frame)
        hud_layout.setContentsMargins(8, 4, 8, 4)
        hud_layout.setSpacing(10)

        # Control Tamaño de Fuente
        hud_layout.addWidget(QLabel("Fuente:"))
        self.font_slider = QSlider(Qt.Orientation.Horizontal)
        self.font_slider.setRange(16, 44)
        self.font_slider.setValue(self.current_font_size)
        self.font_slider.valueChanged.connect(self._on_font_size_changed)
        hud_layout.addWidget(self.font_slider)

        # Control Opacidad de Fondo
        hud_layout.addWidget(QLabel("Opacidad:"))
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(80, 255)
        self.opacity_slider.setValue(self.current_opacity)
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        hud_layout.addWidget(self.opacity_slider)

        # Control Velocidad
        hud_layout.addWidget(QLabel("Vel:"))
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(1, 10)
        self.speed_slider.setValue(self.scroll_speed)
        self.speed_slider.valueChanged.connect(self._on_speed_slider_changed)
        hud_layout.addWidget(self.speed_slider)

        # Controles Eye Contact en HUD (Nivel 1: solo si enable_level1 y si corre con tubería de video)
        if self.enable_level1 and self.is_eye_contact_active:
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.VLine)
            sep.setStyleSheet("color: #475569;")
            hud_layout.addWidget(sep)

            hud_layout.addWidget(QLabel("Mirada:"))
            self.eye_slider = QSlider(Qt.Orientation.Horizontal)
            self.eye_slider.setRange(0, 100)
            self.eye_slider.setValue(self.eye_contact_intensity)
            self.eye_slider.valueChanged.connect(self._on_eye_intensity_changed)
            hud_layout.addWidget(self.eye_slider)

            self.eye_toggle_btn = QPushButton()
            self.eye_toggle_btn.setFixedSize(54, 20)
            self._update_eye_toggle_style()
            self.eye_toggle_btn.clicked.connect(self._on_eye_toggle_clicked)
            hud_layout.addWidget(self.eye_toggle_btn)

        self.hud_frame.hide()  # Inicia plegado
        root_layout.addWidget(self.hud_frame)

        # Preparación arquitectónica Nivel 2: contenedor de previsualización en vivo (colapsado)
        self.preview_container = QWidget()
        self.preview_container.setVisible(False)
        root_layout.addWidget(self.preview_container)

        container = QWidget()
        container.setLayout(root_layout)
        self.setCentralWidget(container)

    def _update_led_badge(self):
        """Actualiza el indicador superior reflejando fielmente si la cámara está activa o apagada."""
        if not hasattr(self, "led_badge"):
            return
        if not self.is_eye_contact_active:
            self.led_badge.setText("● Modo Lectura (Sin Cámara)")
            self.led_badge.setStyleSheet("""
                QLabel {
                    color: #38BDF8;
                    font-family: 'Segoe UI', sans-serif;
                    font-size: 11px;
                    font-weight: 700;
                    background-color: rgba(56, 189, 248, 0.15);
                    padding: 2px 6px;
                    border-radius: 4px;
                }
            """)
        elif self.eye_contact_enabled:
            self.led_badge.setText("● Eye Contact: Activo")
            self.led_badge.setStyleSheet("""
                QLabel {
                    color: #22C55E;
                    font-family: 'Segoe UI', sans-serif;
                    font-size: 11px;
                    font-weight: 700;
                    background-color: rgba(34, 197, 94, 0.15);
                    padding: 2px 6px;
                    border-radius: 4px;
                }
            """)
        else:
            self.led_badge.setText("● Eye Contact: Pausado")
            self.led_badge.setStyleSheet("""
                QLabel {
                    color: #F59E0B;
                    font-family: 'Segoe UI', sans-serif;
                    font-size: 11px;
                    font-weight: 700;
                    background-color: rgba(245, 158, 11, 0.15);
                    padding: 2px 6px;
                    border-radius: 4px;
                }
            """)

    def eventFilter(self, obj, event):
        """Permite arrastrar la ventana del teleprompter haciendo clic sobre el área de texto."""
        if obj == self.text_editor.viewport():
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            elif event.type() == QEvent.Type.MouseMove and (event.buttons() & Qt.MouseButton.LeftButton) and self._drag_pos is not None:
                self.move(event.globalPosition().toPoint() - self._drag_pos)
                return True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self._drag_pos = None
                return True
        return super().eventFilter(obj, event)

    def _update_editor_style(self):
        self.text_editor.setStyleSheet(f"""
            QTextEdit {{
                background-color: rgba(15, 23, 42, {self.current_opacity});
                color: #38BDF8;
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: {self.current_font_size}px;
                font-weight: 600;
                line-height: 1.4;
                border: 1px solid rgba(56, 189, 248, 80);
                border-radius: 10px;
                padding: 14px;
            }}
        """)

    def _load_initial_script(self):
        if self.script_path and os.path.exists(self.script_path):
            try:
                with open(self.script_path, "r", encoding="utf-8") as f:
                    self.text_editor.setPlainText(f.read())
                return
            except Exception:
                pass

        self.text_editor.setPlainText(
            "Bienvenido a DashEyes V2.0\n\n"
            "Atajos Globales Activos (puedes estar en PowerPoint o Zoom):\n"
            "• [F8] o [Espacio]: Pausar o reanudar el scroll.\n"
            "• [F9] / [F10]: Disminuir o aumentar velocidad globalmente.\n"
            "• [F2]: Mostrar u ocultar barra de controles HUD.\n"
            "• [Esc] o botón [✕]: Cerrar el teleprompter.\n\n"
            "Arrastra esta ventana desde cualquier parte o suelta un archivo .txt / .md "
            "para cargar tu presentación al instante."
        )

    def _toggle_hud(self):
        if self.hud_frame.isVisible():
            self.hud_frame.hide()
        else:
            self.hud_frame.show()

    def _on_font_size_changed(self, val):
        self.current_font_size = val
        self._update_editor_style()
        config_manager.update_teleprompter_config(font_size=val)

    def _on_opacity_changed(self, val):
        self.current_opacity = val
        self._update_editor_style()
        config_manager.update_teleprompter_config(opacity=val)

    def _on_speed_slider_changed(self, val):
        self.scroll_speed = val
        self._update_timer_speed()
        config_manager.update_teleprompter_config(scroll_speed=val)

    def _update_eye_toggle_style(self):
        if not hasattr(self, "eye_toggle_btn"):
            return
        if self.eye_contact_enabled:
            self.eye_toggle_btn.setText("👁 ON")
            self.eye_toggle_btn.setStyleSheet("""
                QPushButton {
                    background-color: #059669;
                    color: #FFFFFF;
                    font-size: 10px;
                    font-weight: 700;
                    border-radius: 3px;
                    border: none;
                }
                QPushButton:hover { background-color: #047857; }
            """)
        else:
            self.eye_toggle_btn.setText("👁 OFF")
            self.eye_toggle_btn.setStyleSheet("""
                QPushButton {
                    background-color: #475569;
                    color: #94A3B8;
                    font-size: 10px;
                    font-weight: 700;
                    border-radius: 3px;
                    border: none;
                }
                QPushButton:hover { background-color: #64748B; }
            """)

    def _on_eye_intensity_changed(self, val):
        self.eye_contact_intensity = val
        if self.ipc_pipe is not None:
            try:
                self.ipc_pipe.send({
                    "cmd": "SET_INTENSITY",
                    "intensity": val,
                    "enabled": self.eye_contact_enabled
                })
            except Exception:
                pass
        config_manager.update_eye_contact_config(intensity=val)

    def _on_eye_toggle_clicked(self):
        self.eye_contact_enabled = not self.eye_contact_enabled
        self._update_eye_toggle_style()
        self._update_led_badge()
        if self.ipc_pipe is not None:
            try:
                self.ipc_pipe.send({
                    "cmd": "TOGGLE_CORRECTION",
                    "enabled": self.eye_contact_enabled
                })
            except Exception:
                pass
        config_manager.update_eye_contact_config(enabled=self.eye_contact_enabled)

    def _init_scroll_timer(self):
        self.scroll_timer = QTimer(self)
        self.scroll_timer.timeout.connect(self._scroll_tick)
        self._update_timer_speed()
        self.scroll_timer.start()

    def _update_timer_speed(self):
        interval = max(8, int(self.base_interval_ms - (self.scroll_speed - 3) * 6))
        self.scroll_timer.setInterval(interval)
        if hasattr(self, "speed_slider") and self.speed_slider.value() != self.scroll_speed:
            self.speed_slider.blockSignals(True)
            self.speed_slider.setValue(self.scroll_speed)
            self.speed_slider.blockSignals(False)
        state_str = "PAUSADO" if self.is_paused else f"Vel: {self.scroll_speed}"
        self.status_label.setText(f"[{state_str}] | [F8] Pausa | [F9/F10] Vel | [F2] HUD | [Esc] Salir")

    def _scroll_tick(self):
        if not self.is_paused:
            scrollbar = self.text_editor.verticalScrollBar()
            scrollbar.setValue(scrollbar.value() + 1)

    # --------------------------------------------------------------------------
    # DRAG & DROP DE GUIONES (.TXT / .MD)
    # --------------------------------------------------------------------------
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if file_path.lower().endswith((".txt", ".md")) and os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        self.text_editor.setPlainText(f.read())
                        self.text_editor.verticalScrollBar().setValue(0)
                        event.acceptProposedAction()
                        return
                except Exception:
                    pass

    # --------------------------------------------------------------------------
    # REGISTRO Y GESTIÓN DE GLOBAL HOTKEYS Y CIERRE UNIFICADO
    # --------------------------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        hwnd = int(self.winId())
        set_window_capture_invisibility(hwnd)
        self._current_screen = self.screen()

        if not self._hotkeys_registered:
            user32 = ctypes.windll.user32
            user32.RegisterHotKey(hwnd, HOTKEY_F8_ID, 0, VK_F8)
            user32.RegisterHotKey(hwnd, HOTKEY_F9_ID, 0, VK_F9)
            user32.RegisterHotKey(hwnd, HOTKEY_F10_ID, 0, VK_F10)
            self._hotkeys_registered = True

        if not self._screen_listener_connected and self.windowHandle() is not None:
            self.windowHandle().screenChanged.connect(self._on_screen_changed)
            self._screen_listener_connected = True

    def closeEvent(self, event):
        """Cierre único y unificado: libera Hotkeys Win32 y notifica STOP por la tubería IPC."""
        if self._hotkeys_registered:
            try:
                hwnd = int(self.winId())
                user32 = ctypes.windll.user32
                user32.UnregisterHotKey(hwnd, HOTKEY_F8_ID)
                user32.UnregisterHotKey(hwnd, HOTKEY_F9_ID)
                user32.UnregisterHotKey(hwnd, HOTKEY_F10_ID)
            except Exception:
                pass
            self._hotkeys_registered = False

        if self.ipc_pipe is not None:
            try:
                self.ipc_pipe.send({"cmd": "STOP"})
            except Exception:
                pass
            try:
                self.ipc_pipe.close()
            except Exception:
                pass

        event.accept()

    def nativeEvent(self, event_type, message):
        """Captura pulsaciones globales de F8, F9 y F10 aun sin tener el foco."""
        if event_type in (b"windows_generic_MSG", "windows_generic_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                hotkey_id = msg.wParam
                if hotkey_id == HOTKEY_F8_ID:
                    self.is_paused = not self.is_paused
                    self._update_timer_speed()
                    return True, 0
                elif hotkey_id == HOTKEY_F9_ID:
                    if self.scroll_speed > 1:
                        self.scroll_speed -= 1
                        self._update_timer_speed()
                        config_manager.update_teleprompter_config(scroll_speed=self.scroll_speed)
                    return True, 0
                elif hotkey_id == HOTKEY_F10_ID:
                    if self.scroll_speed < 10:
                        self.scroll_speed += 1
                        self._update_timer_speed()
                        config_manager.update_teleprompter_config(scroll_speed=self.scroll_speed)
                    return True, 0
        return super().nativeEvent(event_type, message)

    def moveEvent(self, event):
        super().moveEvent(event)
        new_screen = self.screen()
        if new_screen != self._current_screen:
            self._current_screen = new_screen
            set_window_capture_invisibility(int(self.winId()))

    def _on_screen_changed(self, screen):
        self._current_screen = screen
        set_window_capture_invisibility(int(self.winId()))

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        if key == Qt.Key.Key_Space:
            self.is_paused = not self.is_paused
            self._update_timer_speed()
        elif key == Qt.Key.Key_F2:
            self._toggle_hud()
        elif key == Qt.Key.Key_Up:
            if self.scroll_speed < 10:
                self.scroll_speed += 1
                self._update_timer_speed()
                config_manager.update_teleprompter_config(scroll_speed=self.scroll_speed)
        elif key == Qt.Key.Key_Down:
            if self.scroll_speed > 1:
                self.scroll_speed -= 1
                self._update_timer_speed()
                config_manager.update_teleprompter_config(scroll_speed=self.scroll_speed)
        elif key == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None


def run_teleprompter_process(ipc_pipe=None, script_path=None):
    app = QApplication(sys.argv)
    prompter = InvisibleTeleprompter(script_path=script_path, ipc_pipe=ipc_pipe)
    prompter.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run_teleprompter_process()
