"""
dashboard.py - Centro de Control y Diagnóstico de DashEyes (V2.0)
Punto de entrada visual para verificación estricta de administrador, diagnóstico de hardware
y dependencias, autoreparación asíncrona (sin UI freeze) e inicio orquestado.
"""

import sys
import os
import ctypes
import subprocess
import webbrowser
import importlib
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QMessageBox,
    QFrame,
)


class DiagnosticWorker(QThread):
    """Hilo de diagnóstico en segundo plano para no congelar la UI al testear hardware."""
    diagnostics_complete = pyqtSignal(dict)

    def run(self):
        results = {
            "packages_ok": True,
            "packages_details": [],
            "obs_driver_ok": False,
            "obs_details": "",
            "is_admin": False
        }

        # 1. Verificación de Administrador
        try:
            results["is_admin"] = bool(ctypes.windll.shell32.IsUserAnAdmin() != 0)
        except Exception:
            results["is_admin"] = False

        # 2. Verificación de Paquetes Python
        required_modules = ["PyQt6", "cv2", "mediapipe", "pyvirtualcam", "numpy"]
        for mod in required_modules:
            try:
                importlib.import_module(mod)
                results["packages_details"].append((mod, True, "Instalado"))
            except ImportError:
                results["packages_details"].append((mod, False, "Faltante"))
                results["packages_ok"] = False

        # 3. Verificación del Driver OBS Virtual Camera (DirectShow)
        try:
            import pyvirtualcam
            with pyvirtualcam.Camera(width=640, height=480, fps=30) as cam:
                results["obs_driver_ok"] = True
                results["obs_details"] = f"Driver Detectado ({cam.device})"
        except Exception as e:
            results["obs_driver_ok"] = False
            results["obs_details"] = "Driver no detectado o en uso"

        self.diagnostics_complete.emit(results)


class InstallWorker(QThread):
    """Ejecuta la instalación de pip en segundo plano con notificación por señales."""
    install_finished = pyqtSignal(bool, str)

    def __init__(self, requirements_path: str):
        super().__init__()
        self.requirements_path = requirements_path

    def run(self):
        try:
            cmd = [sys.executable, "-m", "pip", "install", "-r", self.requirements_path]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            self.install_finished.emit(True, "Dependencias instaladas correctamente.")
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr if e.stderr else e.stdout
            self.install_finished.emit(False, f"Fallo en pip install:\n{err_msg}")
        except Exception as ex:
            self.install_finished.emit(False, f"Excepción al ejecutar el instalador:\n{str(ex)}")


class DashEyesDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.dash_process = None
        self.install_worker = None
        self._init_window()
        self._init_ui()
        self.run_diagnostics()

    def _init_window(self):
        self.setWindowTitle("DashEyes - Centro de Control V2.0")
        self.setFixedSize(540, 520)
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0F172A;
            }
            QWidget {
                color: #E2E8F0;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
        """)

    def _init_ui(self):
        central_widget = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)

        # Encabezado
        title = QLabel("DASHEYES")
        title.setStyleSheet("font-size: 26px; font-weight: 800; color: #38BDF8; letter-spacing: 2px;")
        subtitle = QLabel("Centro de Control y Diagnóstico de Entorno")
        subtitle.setStyleSheet("font-size: 13px; color: #94A3B8;")

        layout.addWidget(title)
        layout.addWidget(subtitle)

        # Separador
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #334155;")
        layout.addWidget(sep)

        # Checklist de Estado
        self.status_box = QVBoxLayout()
        self.status_box.setSpacing(10)

        self.admin_badge = self._create_checklist_item("Privilegios de Administrador", "Verificando...")
        self.packages_badge = self._create_checklist_item("Paquetes de Python (Requisitos)", "Verificando...")
        self.obs_badge = self._create_checklist_item("Driver OBS Virtual Camera", "Verificando...")

        self.status_box.addLayout(self.admin_badge[0])
        self.status_box.addLayout(self.packages_badge[0])
        self.status_box.addLayout(self.obs_badge[0])

        layout.addLayout(self.status_box)

        # Barra de progreso
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                background-color: #1E293B;
            }
            QProgressBar::chunk {
                background-color: #38BDF8;
            }
        """)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        layout.addStretch()

        # Botón de Reparación / Autoconfiguración
        self.repair_btn = QPushButton("Reparar / Instalar Dependencias")
        self.repair_btn.setFixedHeight(40)
        self.repair_btn.setStyleSheet("""
            QPushButton {
                background-color: #334155;
                color: #F8FAFC;
                font-weight: 600;
                border-radius: 6px;
                border: 1px solid #475569;
            }
            QPushButton:hover {
                background-color: #475569;
            }
            QPushButton:disabled {
                color: #64748B;
                background-color: #1E293B;
            }
        """)
        self.repair_btn.clicked.connect(self.on_repair_clicked)
        layout.addWidget(self.repair_btn)

        # Botones Principales (Iniciar / Salir)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        self.launch_btn = QPushButton("Iniciar DashEyes")
        self.launch_btn.setFixedHeight(46)
        self.launch_btn.setEnabled(False)
        self.launch_btn.setStyleSheet("""
            QPushButton {
                background-color: #0284C7;
                color: #FFFFFF;
                font-size: 15px;
                font-weight: 700;
                border-radius: 6px;
                border: none;
            }
            QPushButton:hover {
                background-color: #0369A1;
            }
            QPushButton:disabled {
                background-color: #1E293B;
                color: #64748B;
            }
        """)
        self.launch_btn.clicked.connect(self.on_launch_clicked)

        self.exit_btn = QPushButton("Salir")
        self.exit_btn.setFixedHeight(46)
        self.exit_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #94A3B8;
                font-size: 14px;
                font-weight: 600;
                border-radius: 6px;
                border: 1px solid #334155;
            }
            QPushButton:hover {
                background-color: #1E293B;
                color: #F8FAFC;
            }
        """)
        self.exit_btn.clicked.connect(self.close)

        btn_layout.addWidget(self.exit_btn, 1)
        btn_layout.addWidget(self.launch_btn, 2)
        layout.addLayout(btn_layout)

        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

    def _create_checklist_item(self, label_text: str, default_status: str):
        row = QHBoxLayout()
        title_label = QLabel(label_text)
        title_label.setStyleSheet("font-size: 14px; font-weight: 500;")

        status_badge = QLabel(default_status)
        status_badge.setStyleSheet("""
            font-size: 12px;
            font-weight: 600;
            color: #94A3B8;
            background-color: #1E293B;
            padding: 4px 8px;
            border-radius: 4px;
        """)

        row.addWidget(title_label)
        row.addStretch()
        row.addWidget(status_badge)
        return (row, status_badge)

    def run_diagnostics(self):
        self.progress_bar.show()
        self.worker = DiagnosticWorker()
        self.worker.diagnostics_complete.connect(self.on_diagnostics_complete)
        self.worker.start()

    def on_diagnostics_complete(self, res: dict):
        self.progress_bar.hide()

        # 1. Verificación Estricta de Administrador
        if not res["is_admin"]:
            self.admin_badge[1].setText("DENEGADO (No es Admin)")
            self.admin_badge[1].setStyleSheet(
                "color: #EF4444; background-color: rgba(239, 68, 68, 0.15); padding: 4px 8px; border-radius: 4px; font-weight: bold;"
            )
            self.launch_btn.setEnabled(False)
            self.repair_btn.setEnabled(False)

            QMessageBox.critical(
                self,
                "Acceso Denegado - Requiere Administrador",
                "DashEyes requiere privilegios de Administrador para gestionar drivers DirectShow de video y dependencias del sistema.\n\n"
                "Por favor, cierra el programa, haz clic derecho sobre la terminal o acceso directo y selecciona 'Ejecutar como administrador'."
            )
            self.close()
            return

        self.admin_badge[1].setText("OK (Administrador)")
        self.admin_badge[1].setStyleSheet(
            "color: #22C55E; background-color: rgba(34, 197, 94, 0.15); padding: 4px 8px; border-radius: 4px; font-weight: bold;"
        )

        all_ok = True

        # 2. Paquetes Python
        if res["packages_ok"]:
            self.packages_badge[1].setText("100% Instalado")
            self.packages_badge[1].setStyleSheet(
                "color: #22C55E; background-color: rgba(34, 197, 94, 0.15); padding: 4px 8px; border-radius: 4px; font-weight: bold;"
            )
        else:
            self.packages_badge[1].setText("Faltan Paquetes")
            self.packages_badge[1].setStyleSheet(
                "color: #EF4444; background-color: rgba(239, 68, 68, 0.15); padding: 4px 8px; border-radius: 4px; font-weight: bold;"
            )
            all_ok = False

        # 3. Driver OBS Virtual Camera
        if res["obs_driver_ok"]:
            self.obs_badge[1].setText("OK (DirectShow Activo)")
            self.obs_badge[1].setStyleSheet(
                "color: #22C55E; background-color: rgba(34, 197, 94, 0.15); padding: 4px 8px; border-radius: 4px; font-weight: bold;"
            )
        else:
            self.obs_badge[1].setText("No Detectado")
            self.obs_badge[1].setStyleSheet(
                "color: #EF4444; background-color: rgba(239, 68, 68, 0.15); padding: 4px 8px; border-radius: 4px; font-weight: bold;"
            )
            all_ok = False

        self.launch_btn.setEnabled(all_ok)

    def on_repair_clicked(self):
        """Inicia la instalación asíncrona mediante QThread sin bloquear la interfaz."""
        req_file = os.path.join(os.path.dirname(__file__), "requirements.txt")
        self.progress_bar.show()
        self.repair_btn.setEnabled(False)
        self.repair_btn.setText("Instalando paquetes en segundo plano...")

        self.install_worker = InstallWorker(req_file)
        self.install_worker.install_finished.connect(self.on_install_completed)
        self.install_worker.start()

    def on_install_completed(self, success: bool, message: str):
        """Callback invocado al concluir el hilo de instalación."""
        self.progress_bar.hide()
        self.repair_btn.setText("Reparar / Instalar Dependencias")
        self.repair_btn.setEnabled(True)

        if success:
            QMessageBox.information(self, "Instalación Completada", message)
        else:
            QMessageBox.warning(self, "Aviso de Instalación", message)

        reply = QMessageBox.question(
            self,
            "Driver de Cámara Virtual",
            "¿Deseas abrir la página oficial de OBS Studio para instalar el driver DirectShow de OBS Virtual Camera?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            webbrowser.open("https://obsproject.com/download")

        self.run_diagnostics()

    def on_launch_clicked(self):
        main_py = os.path.join(os.path.dirname(__file__), "main.py")
        try:
            self.dash_process = subprocess.Popen([sys.executable, main_py])
            self.launch_btn.setEnabled(False)
            self.launch_btn.setText("DashEyes en Ejecución")
            self.launch_btn.setStyleSheet("background-color: #10B981; color: white; font-weight: bold;")
            QMessageBox.information(
                self,
                "DashEyes Iniciado",
                "DashEyes se ha lanzado con éxito.\nEl teleprompter flotará en pantalla y la cámara virtual está activa.\n\nPuedes cerrar este Dashboard sin interrumpir la llamada."
            )
        except Exception as e:
            QMessageBox.critical(self, "Error al Iniciar", f"No se pudo iniciar main.py:\n{e}")

    def closeEvent(self, event):
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = DashEyesDashboard()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
