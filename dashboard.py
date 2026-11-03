"""
dashboard.py - Centro de Control y Diagnóstico de DashEyes (V2.0)
Punto de entrada visual con QSystemTrayIcon, InstallWorker asíncrono con
cancelación segura, y verificación estricta de Administrador.
"""

import sys
import os
import ctypes
import subprocess
import webbrowser
import importlib
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QIcon
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
    QSystemTrayIcon,
    QMenu,
)


class DiagnosticWorker(QThread):
    diagnostics_complete = pyqtSignal(dict)

    def run(self):
        results = {
            "packages_ok": True,
            "packages_details": [],
            "obs_driver_ok": False,
            "obs_details": "",
            "is_admin": False
        }

        try:
            results["is_admin"] = bool(ctypes.windll.shell32.IsUserAnAdmin() != 0)
        except Exception:
            results["is_admin"] = False

        required_modules = ["PyQt6", "cv2", "mediapipe", "pyvirtualcam", "numpy"]
        for mod in required_modules:
            try:
                importlib.import_module(mod)
                results["packages_details"].append((mod, True, "Instalado"))
            except ImportError:
                results["packages_details"].append((mod, False, "Faltante"))
                results["packages_ok"] = False

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
    install_finished = pyqtSignal(bool, str)

    def __init__(self, requirements_path: str):
        super().__init__()
        self.requirements_path = requirements_path
        self.process = None

    def run(self):
        try:
            cmd = [sys.executable, "-m", "pip", "install", "-r", self.requirements_path]
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            while self.process.poll() is None:
                if self.isInterruptionRequested():
                    self.process.terminate()
                    self.process.wait(timeout=2.0)
                    self.install_finished.emit(False, "Instalación cancelada por el usuario.")
                    return
                self.msleep(100)

            stdout, stderr = self.process.communicate()
            if self.process.returncode == 0:
                self.install_finished.emit(True, "Dependencias instaladas correctamente.")
            else:
                self.install_finished.emit(False, f"Fallo en pip:\n{stderr or stdout}")

        except Exception as ex:
            self.install_finished.emit(False, f"Error al ejecutar instalador:\n{str(ex)}")


class DashEyesDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.dash_process = None
        self.install_worker = None
        self._init_window()
        self._init_tray_icon()
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

    def _init_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.windowIcon())

        tray_menu = QMenu()
        show_action = tray_menu.addAction("Mostrar Dashboard")
        show_action.triggered.connect(self.showNormal)
        quit_action = tray_menu.addAction("Salir de DashEyes")
        quit_action.triggered.connect(self._force_exit)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.showNormal()
            self.activateWindow()

    def _init_ui(self):
        central_widget = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)

        title = QLabel("DASHEYES")
        title.setStyleSheet("font-size: 26px; font-weight: 800; color: #38BDF8; letter-spacing: 2px;")
        subtitle = QLabel("Centro de Control y Diagnóstico de Entorno")
        subtitle.setStyleSheet("font-size: 13px; color: #94A3B8;")

        layout.addWidget(title)
        layout.addWidget(subtitle)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #334155;")
        layout.addWidget(sep)

        self.status_box = QVBoxLayout()
        self.status_box.setSpacing(10)

        self.admin_badge = self._create_checklist_item("Privilegios de Administrador", "Verificando...")
        self.packages_badge = self._create_checklist_item("Paquetes de Python (Requisitos)", "Verificando...")
        self.obs_badge = self._create_checklist_item("Driver OBS Virtual Camera", "Verificando...")

        self.status_box.addLayout(self.admin_badge[0])
        self.status_box.addLayout(self.packages_badge[0])
        self.status_box.addLayout(self.obs_badge[0])

        layout.addLayout(self.status_box)

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
        req_file = os.path.join(os.path.dirname(__file__), "requirements.txt")
        self.progress_bar.show()
        self.repair_btn.setEnabled(False)
        self.repair_btn.setText("Instalando paquetes en segundo plano...")

        self.install_worker = InstallWorker(req_file)
        self.install_worker.install_finished.connect(self.on_install_completed)
        self.install_worker.start()

    def on_install_completed(self, success: bool, message: str):
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
        if self.install_worker is not None and self.install_worker.isRunning():
            self.install_worker.requestInterruption()
            self.install_worker.wait(3000)

        if self.dash_process is not None and self.dash_process.poll() is None:
            self.hide()
            self.tray_icon.showMessage(
                "DashEyes en Ejecución",
                "DashEyes sigue activo en segundo plano. Haz doble clic en este icono para reabrir.",
                QSystemTrayIcon.MessageIcon.Information,
                3000
            )
            event.ignore()
            return

        event.accept()

    def _force_exit(self):
        if self.dash_process is not None and self.dash_process.poll() is None:
            self.dash_process.terminate()
            try:
                self.dash_process.wait(timeout=2.0)
            except Exception:
                pass
        self.tray_icon.hide()
        QApplication.quit()


def main():
    app = QApplication(sys.argv)
    window = DashEyesDashboard()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
