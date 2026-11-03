"""
teleprompter_ui.py - Módulo A: Teleprompter Invisible para Windows (PyQt6)
Diseñado para operar como proceso independiente o autónomo dentro de DashEyes.
BUG-02 Corregido: Detección geográfica de cambio de pantalla en moveEvent sin saturación Win32.
"""

import sys
import ctypes
from ctypes import wintypes
from PyQt6.QtCore import Qt, QTimer, QPoint
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTextEdit,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QLabel,
)

WDA_EXCLUDEFROMCAPTURE = 0x00000011


def set_window_capture_invisibility(hwnd: int) -> bool:
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        SetWindowDisplayAffinity = user32.SetWindowDisplayAffinity
        SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        SetWindowDisplayAffinity.restype = wintypes.BOOL

        result = SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        if not result:
            return False
        return True
    except Exception:
        return False


class InvisibleTeleprompter(QMainWindow):
    def __init__(self, ipc_queue=None):
        super().__init__()
        self.ipc_queue = ipc_queue

        self.is_paused = False
        self.scroll_speed = 3
        self.base_interval_ms = 40
        self._drag_pos = None
        self._screen_listener_connected = False
        self._current_screen = None  # Cache de pantalla para BUG-02

        self._init_window_properties()
        self._init_ui_components()
        self._init_scroll_timer()

    def _init_window_properties(self):
        self.setWindowTitle("DashEyes - Invisible Teleprompter")
        self.resize(650, 320)

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

        self.header_bar = QHBoxLayout()
        self.status_label = QLabel(f"Velocidad: {self.scroll_speed} | [Espacio] Pausa | [↑/↓] Vel | [Esc] Salir")
        self.status_label.setStyleSheet("""
            QLabel {
                color: #A0AEC0;
                font-family: 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 600;
                background-color: transparent;
            }
        """)
        self.header_bar.addWidget(self.status_label)
        root_layout.addLayout(self.header_bar)

        self.text_editor = QTextEdit()
        self.text_editor.setReadOnly(True)
        self.text_editor.setStyleSheet("""
            QTextEdit {
                background-color: rgba(15, 23, 42, 215);
                color: #38BDF8;
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 24px;
                font-weight: 600;
                line-height: 1.4;
                border: 1px solid rgba(56, 189, 248, 80);
                border-radius: 10px;
                padding: 14px;
            }
        """)

        self.text_editor.setPlainText(
            "Bienvenido a DashEyes - Teleprompter Invisible.\n\n"
            "Este texto flota en tu pantalla para permitirte leer mientras mantienes "
            "la vista cerca de la lente de tu cámara web.\n\n"
            "Gracias a la API WDA_EXCLUDEFROMCAPTURE de Windows, este cuadro es "
            "completamente invisible para los participantes en Zoom, Teams, Meet o OBS.\n\n"
            "Controles disponibles:\n"
            "• Barra Espaciadora: Pausar o reanudar el scroll.\n"
            "• Flecha Arriba (↑): Incrementar la velocidad.\n"
            "• Flecha Abajo (↓): Reducir la velocidad.\n"
            "• Arrastre con clic izquierdo: Mover la ventana a cualquier posición.\n"
            "• Tecla Escape (Esc): Cerrar la aplicación.\n\n"
            "Continúa agregando aquí tus notas o presentación..."
        )

        root_layout.addWidget(self.text_editor)

        container = QWidget()
        container.setLayout(root_layout)
        self.setCentralWidget(container)

    def _init_scroll_timer(self):
        self.scroll_timer = QTimer(self)
        self.scroll_timer.timeout.connect(self._scroll_tick)
        self._update_timer_speed()
        self.scroll_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        hwnd = int(self.winId())
        set_window_capture_invisibility(hwnd)
        self._current_screen = self.screen()

        if not self._screen_listener_connected and self.windowHandle() is not None:
            self.windowHandle().screenChanged.connect(self._on_screen_changed)
            self._screen_listener_connected = True

    def moveEvent(self, event):
        """BUG-02 Corregido: Solo invoca Win32 si la ventana realmente cruzó a otro monitor."""
        super().moveEvent(event)
        new_screen = self.screen()
        if new_screen != self._current_screen:
            self._current_screen = new_screen
            hwnd = int(self.winId())
            set_window_capture_invisibility(hwnd)

    def _on_screen_changed(self, screen):
        """Callback oficial de cambio de pantalla en Qt."""
        self._current_screen = screen
        hwnd = int(self.winId())
        set_window_capture_invisibility(hwnd)

    def _scroll_tick(self):
        if not self.is_paused:
            scrollbar = self.text_editor.verticalScrollBar()
            scrollbar.setValue(scrollbar.value() + 1)

    def _update_timer_speed(self):
        interval = max(8, int(self.base_interval_ms - (self.scroll_speed - 3) * 6))
        self.scroll_timer.setInterval(interval)
        self._update_status_ui()

    def _update_status_ui(self):
        state_str = "PAUSADO" if self.is_paused else f"Vel: {self.scroll_speed}"
        self.status_label.setText(
            f"[{state_str}] | [Espacio] Alternar | [↑/↓] Vel | [Arrastrar para mover] | [Esc] Salir"
        )

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        if key == Qt.Key.Key_Space:
            self.is_paused = not self.is_paused
            self._update_status_ui()
        elif key == Qt.Key.Key_Up:
            if self.scroll_speed < 10:
                self.scroll_speed += 1
                self._update_timer_speed()
        elif key == Qt.Key.Key_Down:
            if self.scroll_speed > 1:
                self.scroll_speed -= 1
                self._update_timer_speed()
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


def run_teleprompter_process(ipc_queue=None):
    app = QApplication(sys.argv)
    prompter = InvisibleTeleprompter(ipc_queue=ipc_queue)
    prompter.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run_teleprompter_process()
