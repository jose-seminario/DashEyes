"""
teleprompter_ui.py - Módulo A: Teleprompter Invisible para Windows (PyQt6)
Diseñado para operar como proceso independiente o autónomo dentro de DashEyes.
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

# Constante de la API de Windows 10 (Build 19041+) y Windows 11
# Excluye la superficie de la ventana de las texturas de captura de DWM
WDA_EXCLUDEFROMCAPTURE = 0x00000011


def set_window_capture_invisibility(hwnd: int) -> bool:
    """
    Configura la afinidad de la ventana con la API nativa de Windows (User32).
    Aplica WDA_EXCLUDEFROMCAPTURE para ocultar la ventana en Zoom, Teams, Meet y OBS.
    """
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        SetWindowDisplayAffinity = user32.SetWindowDisplayAffinity
        SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        SetWindowDisplayAffinity.restype = wintypes.BOOL

        result = SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        if not result:
            err = ctypes.get_last_error()
            print(f"[Error Win32] No se pudo aplicar afinidad de ventana: Código {err}")
            return False
        return True
    except Exception as e:
        print(f"[Excepción Win32] Fallo al invocar SetWindowDisplayAffinity: {e}")
        return False


class InvisibleTeleprompter(QMainWindow):
    def __init__(self, ipc_queue=None):
        super().__init__()
        self.ipc_queue = ipc_queue  # Canal de comunicación con el proceso de video

        # Parámetros de desplazamiento
        self.is_paused = False
        self.scroll_speed = 3  # Nivel de velocidad (1 a 10)
        self.base_interval_ms = 40  # Intervalo base del timer
        self._drag_pos = None  # Para permitir arrastre sin barra de título

        self._init_window_properties()
        self._init_ui_components()
        self._init_scroll_timer()

    def _init_window_properties(self):
        """Configuración de flags de ventana para superposición y transparencia."""
        self.setWindowTitle("DashEyes - Invisible Teleprompter")
        self.resize(650, 320)

        # Siempre al frente, sin bordes de ventana y modo herramienta (sin icono en Alt+Tab)
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
        )
        # Permite canal alfa en el fondo
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def _init_ui_components(self):
        """Construcción de la interfaz de texto y barra de estado."""
        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(6)

        # Barra superior con atajos y estado
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

        # Área de lectura del guion
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

        # Guion de prueba por defecto
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
        """Inicializa el temporizador de auto-scroll."""
        self.scroll_timer = QTimer(self)
        self.scroll_timer.timeout.connect(self._scroll_tick)
        self._update_timer_speed()
        self.scroll_timer.start()

    def showEvent(self, event):
        """Aplica la exclusión de captura en cuanto la ventana obtiene su HWND en Windows."""
        super().showEvent(event)
        hwnd = int(self.winId())
        is_hidden = set_window_capture_invisibility(hwnd)
        if is_hidden:
            print(f"[OK] Ventana (HWND: {hwnd}) excluida de capturas de pantalla.")
        else:
            print(f"[Aviso] No se pudo asegurar la invisibilidad en el HWND: {hwnd}.")

    def _scroll_tick(self):
        """Desplaza el scroll vertical en una unidad por tick."""
        if not self.is_paused:
            scrollbar = self.text_editor.verticalScrollBar()
            scrollbar.setValue(scrollbar.value() + 1)

    def _update_timer_speed(self):
        """Calcula el intervalo del timer según la velocidad seleccionada (1 a 10)."""
        # Mapeo: nivel 1 = 80ms, nivel 10 = 8ms
        interval = max(8, int(self.base_interval_ms - (self.scroll_speed - 3) * 6))
        self.scroll_timer.setInterval(interval)
        self._update_status_ui()

    def _update_status_ui(self):
        state_str = "PAUSADO" if self.is_paused else f"Vel: {self.scroll_speed}"
        self.status_label.setText(
            f"[{state_str}] | [Espacio] Alternar | [↑/↓] Vel | [Arrastrar para mover] | [Esc] Salir"
        )

    # --------------------------------------------------------------------------
    # CONTROLES DE TECLADO
    # --------------------------------------------------------------------------
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

    # --------------------------------------------------------------------------
    # SOPORTE DE ARRASTRE DE VENTANA SIN BORDES
    # --------------------------------------------------------------------------
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
    """Punto de entrada exportable para ser ejecutado como proceso independiente."""
    app = QApplication(sys.argv)
    prompter = InvisibleTeleprompter(ipc_queue=ipc_queue)
    prompter.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run_teleprompter_process()
