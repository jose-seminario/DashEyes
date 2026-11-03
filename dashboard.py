"""
dashboard.py - Centro de Control y Diagnóstico de DashEyes (V2.0 Dual Mode)
Arquitectura de arranque dual: Modo Teleprompter (sin admin/sin OBS) y Modo Eye Contact (Beta).
Incluye UAC bajo demanda (ShellExecuteW 'runas') y QSystemTrayIcon.
"""

import sys
import multiprocessing as mp

if __name__ == "__main__":
    mp.freeze_support()

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
    QFileDialog,
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
        self.selected_script_path = None

        self._init_window()
        self._init_tray_icon()
        self._init_ui()
        self.run_diagnostics()

    def _init_window(self):
        self.setWindowTitle("DashEyes - Centro de Control V2.0")
        self.setFixedSize(580, 620)
        self.setStyleSheet("""
            QMainWindow { background-color: #0F172A; }
            QWidget { color: #E2E8F0; font-family: 'Segoe UI', Arial, sans-serif; }
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
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(12)

        title = QLabel("DASHEYES")
        title.setStyleSheet("font-size: 26px; font-weight: 800; color: #38BDF8; letter-spacing: 2px;")
        subtitle = QLabel("Centro de Control y Selección de Modo V2.0")
        subtitle.setStyleSheet("font-size: 13px; color: #94A3B8;")

        layout.addWidget(title)
        layout.addWidget(subtitle)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #334155;")
        layout.addWidget(sep)

        # Panel de Diagnóstico
        self.status_box = QVBoxLayout()
        self.status_box.setSpacing(6)

        self.admin_badge = self._create_checklist_item("Nivel de Permisos", "Verificando...")
        self.packages_badge = self._create_checklist_item("Paquetes de Python", "Verificando...")
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
            QProgressBar { border: none; background-color: #1E293B; }
            QProgressBar::chunk { background-color: #38BDF8; }
        """)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        # Selector de Guiones
        script_box = QVBoxLayout()
        script_label = QLabel("Guión de Presentación:")
        script_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #E2E8F0;")

        script_btn_layout = QHBoxLayout()
        self.script_path_label = QLabel("Ningún archivo cargado (Guión por defecto)")
        self.script_path_label.setStyleSheet("""
            background-color: #1E293B;
            color: #94A3B8;
            padding: 6px 10px;
            border-radius: 4px;
            font-size: 11px;
        """)

        self.select_script_btn = QPushButton("Cargar .txt / .md")
        self.select_script_btn.setFixedHeight(30)
        self.select_script_btn.setStyleSheet("""
            QPushButton {
                background-color: #334155;
                color: #38BDF8;
                font-weight: 600;
                font-size: 11px;
                border-radius: 4px;
                padding: 0 10px;
                border: 1px solid #475569;
            }
            QPushButton:hover { background-color: #475569; }
        """)
        self.select_script_btn.clicked.connect(self.on_select_script_clicked)

        script_btn_layout.addWidget(self.script_path_label, 1)
        script_btn_layout.addWidget(self.select_script_btn)

        script_box.addWidget(script_label)
        script_box.addLayout(script_btn_layout)
        layout.addLayout(script_box)

        layout.addStretch()

        # Botón de Reparación con UAC Bajo Demanda
        self.repair_btn = QPushButton("Configurar / Reparar Entorno (UAC)")
        self.repair_btn.setFixedHeight(36)
        self.repair_btn.setStyleSheet("""
            QPushButton {
                background-color: #334155;
                color: #F8FAFC;
                font-weight: 600;
                border-radius: 6px;
                border: 1px solid #475569;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #475569; }
            QPushButton:disabled { color: #64748B; background-color: #1E293B; }
        """)
        self.repair_btn.clicked.connect(self.on_repair_clicked)
        layout.addWidget(self.repair_btn)

        # BOTONES DUALES DE LANZAMIENTO
        # Modo 1: Teleprompter (Estable / Sin Admin / Sin OBS)
        self.launch_teleprompter_btn = QPushButton("Iniciar Modo Teleprompter (Sin Admin / Rápido)")
        self.launch_teleprompter_btn.setFixedHeight(44)
        self.launch_teleprompter_btn.setStyleSheet("""
            QPushButton {
                background-color: #059669;
                color: #FFFFFF;
                font-size: 14px;
                font-weight: 700;
                border-radius: 6px;
                border: none;
            }
            QPushButton:hover { background-color: #047857; }
        """)
        self.launch_teleprompter_btn.clicked.connect(self.on_launch_teleprompter_clicked)
        layout.addWidget(self.launch_teleprompter_btn)

        # Modo 2: Eye Contact (Completo / Beta)
        self.launch_eye_contact_btn = QPushButton("Iniciar Modo Eye Contact (Beta / Requiere OBS)")
        self.launch_eye_contact_btn.setFixedHeight(44)
        self.launch_eye_contact_btn.setEnabled(False)
        self.launch_eye_contact_btn.setStyleSheet("""
            QPushButton {
                background-color: #0284C7;
                color: #FFFFFF;
                font-size: 14px;
                font-weight: 700;
                border-radius: 6px;
                border: none;
            }
            QPushButton:hover { background-color: #0369A1; }
            QPushButton:disabled { background-color: #1E293B; color: #64748B; }
        """)
        self.launch_eye_contact_btn.clicked.connect(self.on_launch_eye_contact_clicked)
        layout.addWidget(self.launch_eye_contact_btn)

        # Botón Salir
        self.exit_btn = QPushButton("Salir")
        self.exit_btn.setFixedHeight(36)
        self.exit_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #94A3B8;
                font-size: 13px;
                font-weight: 600;
                border-radius: 6px;
                border: 1px solid #334155;
            }
            QPushButton:hover { background-color: #1E293B; color: #F8FAFC; }
        """)
        self.exit_btn.clicked.connect(self.close)
        layout.addWidget(self.exit_btn)

        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

    def _create_checklist_item(self, label_text: str, default_status: str):
        row = QHBoxLayout()
        title_label = QLabel(label_text)
        title_label.setStyleSheet("font-size: 13px; font-weight: 500;")

        status_badge = QLabel(default_status)
        status_badge.setStyleSheet("""
            font-size: 11px;
            font-weight: 600;
            color: #94A3B8;
            background-color: #1E293B;
            padding: 3px 8px;
            border-radius: 4px;
        """)

        row.addWidget(title_label)
        row.addStretch()
        row.addWidget(status_badge)
        return (row, status_badge)

    def on_select_script_clicked(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar Guión de Presentación",
            "",
            "Archivos de Texto (*.txt *.md);;Todos los archivos (*.*)"
        )
        if file_path:
            self.selected_script_path = file_path
            file_name = os.path.basename(file_path)
            self.script_path_label.setText(f"Cargado: {file_name}")
            self.script_path_label.setStyleSheet("""
                background-color: rgba(56, 189, 248, 0.15);
                color: #38BDF8;
                padding: 6px 10px;
                border-radius: 4px;
                font-size: 11px;
                font-weight: 600;
            """)

    def run_diagnostics(self):
        self.progress_bar.show()
        self.worker = DiagnosticWorker()
        self.worker.diagnostics_complete.connect(self.on_diagnostics_complete)
        self.worker.start()

    def on_diagnostics_complete(self, res: dict):
        """Diagnóstico no bloqueante: la app abre sin importar si es admin o si falta OBS."""
        self.progress_bar.hide()

        # 1. Nivel de Administrador (Informativo, NUNCA bloquea el teleprompter)
        if res["is_admin"]:
            self.admin_badge[1].setText("Administrador (Acceso Total)")
            self.admin_badge[1].setStyleSheet(
                "color: #22C55E; background-color: rgba(34, 197, 94, 0.15); padding: 3px 8px; border-radius: 4px; font-weight: bold;"
            )
        else:
            self.admin_badge[1].setText("Usuario Estándar (Modo Teleprompter Disponible)")
            self.admin_badge[1].setStyleSheet(
                "color: #38BDF8; background-color: rgba(56, 189, 248, 0.15); padding: 3px 8px; border-radius: 4px; font-weight: bold;"
            )

        # 2. Paquetes Python
        if res["packages_ok"]:
            self.packages_badge[1].setText("100% Instalado")
            self.packages_badge[1].setStyleSheet(
                "color: #22C55E; background-color: rgba(34, 197, 94, 0.15); padding: 3px 8px; border-radius: 4px; font-weight: bold;"
            )
        else:
            self.packages_badge[1].setText("Faltan Paquetes")
            self.packages_badge[1].setStyleSheet(
                "color: #EF4444; background-color: rgba(239, 68, 68, 0.15); padding: 3px 8px; border-radius: 4px; font-weight: bold;"
            )

        # 3. Driver OBS Virtual Camera
        if res["obs_driver_ok"]:
            self.obs_badge[1].setText("OK (DirectShow Activo)")
            self.obs_badge[1].setStyleSheet(
                "color: #22C55E; background-color: rgba(34, 197, 94, 0.15); padding: 3px 8px; border-radius: 4px; font-weight: bold;"
            )
            # Solo se habilita el modo Eye Contact si el driver está presente
            self.launch_eye_contact_btn.setEnabled(res["packages_ok"])
        else:
            self.obs_badge[1].setText("No Detectado (Eye Contact Inactivo)")
            self.obs_badge[1].setStyleSheet(
                "color: #F59E0B; background-color: rgba(245, 158, 11, 0.15); padding: 3px 8px; border-radius: 4px; font-weight: bold;"
            )
            self.launch_eye_contact_btn.setEnabled(False)

        # El modo Teleprompter SIEMPRE está habilitado
        self.launch_teleprompter_btn.setEnabled(True)

    def on_repair_clicked(self):
        """Elevación de UAC bajo demanda usando ShellExecuteW 'runas' solo si no es admin."""
        is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
        req_file = os.path.join(os.path.dirname(__file__), "requirements.txt")

        if not is_admin:
            # Solicitar elevación UAC en Windows
            script_path = os.path.abspath(__file__)
            params = f'"{script_path}" --elevated-repair'
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
            if ret > 32:
                QMessageBox.information(
                    self,
                    "Elevación Solicitada",
                    "Se ha solicitado elevación de Administrador para instalar dependencias.\n"
                    "Al finalizar la ventana de instalación, pulsa Aceptar para refrescar el estado."
                )
            self.run_diagnostics()
            return

        # Si ya es admin, ejecutar hilo de instalación estándar
        self.progress_bar.show()
        self.repair_btn.setEnabled(False)
        self.repair_btn.setText("Instalando paquetes...")

        self.install_worker = InstallWorker(req_file)
        self.install_worker.install_finished.connect(self.on_install_completed)
        self.install_worker.start()

    def on_install_completed(self, success: bool, message: str):
        self.progress_bar.hide()
        self.repair_btn.setText("Configurar / Reparar Entorno (UAC)")
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

    def on_launch_teleprompter_clicked(self):
        """Lanza exclusivamente el Modo Teleprompter (Liviano / Sin OBS / Sin Admin)."""
        main_py = os.path.join(os.path.dirname(__file__), "main.py")
        cmd = [sys.executable, main_py, "--mode", "teleprompter"]
        if self.selected_script_path:
            cmd.extend(["--script", self.selected_script_path])

        try:
            self.dash_process = subprocess.Popen(cmd)
            QMessageBox.information(
                self,
                "Modo Teleprompter Activo",
                "Teleprompter invisible iniciado con éxito (Modo Liviano).\n"
                "• Atajos: [F8] Pausa, [F9/F10] Velocidad, [F2] HUD.\n"
                "• No consume cámara ni requiere permisos de administrador."
            )
        except Exception as e:
            QMessageBox.critical(self, "Error al Iniciar", f"No se pudo iniciar el teleprompter:\n{e}")

    def on_launch_eye_contact_clicked(self):
        """Lanza el Modo Eye Contact Completo (Teleprompter + Visión Artificial + Cámara Virtual)."""
        main_py = os.path.join(os.path.dirname(__file__), "main.py")
        cmd = [sys.executable, main_py, "--mode", "eye_contact"]
        if self.selected_script_path:
            cmd.extend(["--script", self.selected_script_path])

        try:
            self.dash_process = subprocess.Popen(cmd)
            QMessageBox.information(
                self,
                "Modo Eye Contact Activo",
                "DashEyes lanzado con Corrección de Mirada y Cámara Virtual.\n"
                "• Selecciona 'OBS Virtual Camera' en tu app de videollamada."
            )
        except Exception as e:
            QMessageBox.critical(self, "Error al Iniciar", f"No se pudo iniciar el modo Eye Contact:\n{e}")

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
            pid = self.dash_process.pid
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
            except Exception:
                self.dash_process.terminate()
        self.tray_icon.hide()
        QApplication.quit()


def main():
    # Soporte para elevación por línea de comandos
    if len(sys.argv) > 1 and sys.argv[1] == "--elevated-repair":
        req_file = os.path.join(os.path.dirname(__file__), "requirements.txt")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_file])
        sys.exit(0)

    app = QApplication(sys.argv)
    window = DashEyesDashboard()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
