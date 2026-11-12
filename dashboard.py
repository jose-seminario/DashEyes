"""Centro de control: procesos asíncronos, sin shell, pip ni elevación UAC."""

import multiprocessing as mp
import sys


def should_dispatch_to_orchestrator(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    return any(
        arg in ("--mode", "--diagnose", "-h", "--help") or arg.startswith("--mode=")
        for arg in args
    )


if __name__ == "__main__":
    mp.freeze_support()
    # Despachar antes de importar Qt para no cargar la UI en los otros modos.
    if should_dispatch_to_orchestrator(sys.argv[1:]):
        import main as orchestrator
        sys.exit(orchestrator.main())

import json
import os

from PyQt6.QtCore import QProcess, QTimer, Qt
from PyQt6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea,
    QSlider, QSpinBox, QSystemTrayIcon, QVBoxLayout, QWidget,
)

import config_manager
from runtime import get_logger
from settings_writer import SettingsWriter


def create_dasheyes_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush(QColor("#0F172A")))
    painter.setPen(QPen(QColor("#38BDF8"), 4))
    painter.drawEllipse(4, 4, 56, 56)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor("#38BDF8")))
    painter.drawEllipse(16, 20, 32, 24)
    painter.setBrush(QBrush(QColor("#0F172A")))
    painter.drawEllipse(26, 26, 12, 12)
    painter.end()
    return QIcon(pixmap)


class DashEyesDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.logger = get_logger("dashboard")
        self.selected_script_path = None
        self.can_use_eye_contact = False
        self._level1_enabled = True
        self._exiting = False
        self._force_discard_exit = False
        self._stopping = False
        self._pending_mode = None
        self._session_error = ""
        self._diagnostic_aborted = False
        self._diagnostic_abort_reason = ""
        self._session_buffer = bytearray()
        self._diagnostic_buffer = bytearray()
        self.writer = SettingsWriter(self)
        self.writer.saved.connect(self._settings_saved)
        self.writer.idle.connect(self._start_pending)
        self.app_icon = create_dasheyes_icon()
        self.setWindowIcon(self.app_icon)
        self.setWindowTitle("DashEyes — Contacto visual local")
        self.resize(620, 750)
        self.setMinimumSize(460, 400)
        self.setStyleSheet("""
            QWidget { background: #0F172A; color: #E2E8F0; font-family: 'Segoe UI'; font-size: 13px; }
            QPushButton, QComboBox, QSpinBox { background: #1E293B; padding: 9px; border: 1px solid #475569; border-radius: 5px; }
            QPushButton:hover { background: #334155; }
            QPushButton:disabled { color: #64748B; }
            QLabel { background: transparent; }
        """)
        self._init_ui()
        self._init_tray()
        self.session = QProcess(self)
        self.session.readyReadStandardOutput.connect(self._read_session)
        self.session.readyReadStandardError.connect(self._read_session_stderr)
        self.session.finished.connect(self._session_finished)
        self.session.errorOccurred.connect(self._session_failed)
        self.diagnostic = QProcess(self)
        self.diagnostic.readyReadStandardOutput.connect(self._read_diagnostics)
        self.diagnostic.readyReadStandardError.connect(lambda: self.diagnostic.readAllStandardError())
        self.diagnostic.finished.connect(self._diagnostic_finished)
        self.diagnostic.errorOccurred.connect(self._diagnostic_failed)
        self.diagnostic_timeout = QTimer(self)
        self.diagnostic_timeout.setSingleShot(True)
        self.diagnostic_timeout.timeout.connect(self._diagnostic_timed_out)
        self.shutdown_timer = QTimer(self)
        self.shutdown_timer.setSingleShot(True)
        self.shutdown_timer.timeout.connect(self._kill_session)
        self.exit_monitor = QTimer(self)
        self.exit_monitor.setInterval(100)
        self.exit_monitor.timeout.connect(self._finish_exit)
        self._load_controls()
        self.writer.start()
        QTimer.singleShot(0, self.run_diagnostics)

    def _init_ui(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 20)
        title = QLabel("DASHEYES")
        title.setStyleSheet("font-size: 28px; font-weight: bold; color: #38BDF8")
        layout.addWidget(title)
        description = QLabel("Teleprompter y corrección sutil de mirada. Procesamiento local, sin GPU dedicada.")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.diagnostics_label = QLabel("Comprobando dependencias…")
        self.diagnostics_label.setWordWrap(True)
        layout.addWidget(self.diagnostics_label)
        self.diagnostics_details = QLabel()
        self.diagnostics_details.setWordWrap(True)
        self.diagnostics_details.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.diagnostics_details)
        self.repair_btn = QPushButton("Volver a comprobar entorno (no instala nada)")
        self.repair_btn.clicked.connect(self.run_diagnostics)
        layout.addWidget(self.repair_btn)
        self.script_path_label = QLabel("Guion de bienvenida")
        self.script_path_label.setWordWrap(True)
        self.script_path_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.script_path_label)
        select = QPushButton("Seleccionar guion .txt / .md")
        select.clicked.connect(self.on_select_script_clicked)
        layout.addWidget(select)
        self.calibration_frame = QFrame()
        calibration = QVBoxLayout(self.calibration_frame)
        calibration.addWidget(QLabel("Preparación de Eye Contact (controles en vivo dentro del HUD):"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Cámara (índice):"))
        self.camera_index = QSpinBox()
        self.camera_index.setRange(0, 15)
        self.camera_index.setToolTip("Selecciona la cámara física; no selecciones OBS Virtual Camera como entrada.")
        row.addWidget(self.camera_index)
        self.profile = QComboBox()
        self.profile.addItem("Ligero — hasta 480p / 20 FPS", "light")
        self.profile.addItem("Equilibrado — hasta 720p / 30 FPS", "balanced")
        row.addWidget(self.profile, 1)
        calibration.addLayout(row)
        self.intensity_label = QLabel()
        calibration.addWidget(self.intensity_label)
        self.intensity_slider = QSlider(Qt.Orientation.Horizontal)
        self.intensity_slider.setRange(0, 100)
        calibration.addWidget(self.intensity_slider)
        self.toggle_checkbox = QCheckBox("Habilitar corrección (requiere calibrar en cada sesión)")
        calibration.addWidget(self.toggle_checkbox)
        layout.addWidget(self.calibration_frame)
        self.launch_teleprompter_btn = QPushButton("Iniciar Teleprompter — sin cámara")
        self.launch_teleprompter_btn.clicked.connect(lambda: self._launch("teleprompter"))
        layout.addWidget(self.launch_teleprompter_btn)
        self.launch_eye_contact_btn = QPushButton("Iniciar Eye Contact — cámara virtual OBS")
        self.launch_eye_contact_btn.setEnabled(False)
        self.launch_eye_contact_btn.clicked.connect(lambda: self._launch("eye_contact"))
        layout.addWidget(self.launch_eye_contact_btn)
        self.stop_btn = QPushButton("Detener sesión")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._request_stop)
        layout.addWidget(self.stop_btn)
        self.status_label = QLabel("Sin sesión activa")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.settings_label = QLabel("Ajustes portátiles en .dasheyes; no se guardan imágenes ni guiones.")
        self.settings_label.setWordWrap(True)
        layout.addWidget(self.settings_label)
        exit_btn = QPushButton("Salir de DashEyes")
        exit_btn.clicked.connect(self._force_exit)
        layout.addWidget(exit_btn)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.setCentralWidget(scroll)
        self.intensity_slider.valueChanged.connect(self._controls_changed)
        self.toggle_checkbox.toggled.connect(self._controls_changed)
        self.camera_index.valueChanged.connect(self._controls_changed)
        self.profile.currentIndexChanged.connect(self._controls_changed)

    def _init_tray(self):
        self.tray_icon = QSystemTrayIcon(self.app_icon, self)
        self.tray_icon.setToolTip("DashEyes")
        self.tray_menu = QMenu(self)
        self.tray_menu.addAction("Mostrar Dashboard", self.showNormal)
        self.tray_menu.addAction("Detener sesión", self._request_stop)
        self.tray_menu.addAction("Salir", self._force_exit)
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self._tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon.show()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.showNormal()
            self.activateWindow()

    def _load_controls(self):
        config = config_manager.load_config()
        self._level1_enabled = config_manager.level1_controls_enabled(config)
        controls = (self.intensity_slider, self.toggle_checkbox, self.camera_index, self.profile)
        for control in controls:
            control.blockSignals(True)
        self.intensity_slider.setValue(config["eye_contact"]["intensity"])
        self.toggle_checkbox.setChecked(config["eye_contact"]["enabled"])
        self.camera_index.setValue(config["video"]["camera_index"])
        self.profile.setCurrentIndex(self.profile.findData(config["video"]["profile"]))
        for control in controls:
            control.blockSignals(False)
        self.intensity_label.setVisible(self._level1_enabled)
        self.intensity_slider.setVisible(self._level1_enabled)
        self.intensity_slider.setEnabled(self._level1_enabled)
        self.intensity_label.setText(f"Intensidad máxima: {self.intensity_slider.value()}%")

    def _controls_changed(self, *_):
        self.intensity_label.setText(f"Intensidad máxima: {self.intensity_slider.value()}%")
        self.writer.submit({
            "eye_contact": {"intensity": self.intensity_slider.value(), "enabled": self.toggle_checkbox.isChecked()},
            "video": {"camera_index": self.camera_index.value(), "profile": self.profile.currentData()},
        })

    def _settings_saved(self, success, message):
        self.settings_label.setText(message)
        self.settings_label.setStyleSheet("color: #94A3B8" if success else "color: #FBBF24")
        if not success:
            if self._pending_mode is not None:
                self._pending_mode = None
                self.status_label.setText("No se inició la sesión porque quedan ajustes sin guardar.")
            self._refresh_controls()

    def _start_pending(self):
        if self._pending_mode is not None and not self._exiting:
            mode, self._pending_mode = self._pending_mode, None
            self._launch(mode)

    def _command(self, arguments):
        if getattr(sys, "frozen", False):
            return sys.executable, arguments
        return sys.executable, [os.path.join(config_manager.get_base_dir(), "main.py"), *arguments]

    def _running(self):
        return self.session.state() != QProcess.ProcessState.NotRunning

    def _refresh_controls(self):
        running = self._running()
        busy = running or self._pending_mode is not None
        diagnosing = self.diagnostic.state() != QProcess.ProcessState.NotRunning
        self.launch_teleprompter_btn.setEnabled(not busy and not self._exiting)
        self.launch_eye_contact_btn.setEnabled(self.can_use_eye_contact and not busy and not diagnosing and not self._exiting)
        self.calibration_frame.setEnabled(not busy and not self._exiting)
        self.intensity_slider.setEnabled(self._level1_enabled and not busy and not self._exiting)
        self.repair_btn.setEnabled(not busy and not diagnosing and not self._exiting)
        self.stop_btn.setEnabled(running and not self._stopping)

    def run_diagnostics(self):
        if self._exiting or self._running() or self.diagnostic.state() != QProcess.ProcessState.NotRunning:
            return
        self._diagnostic_buffer.clear()
        self._diagnostic_aborted = False
        self._diagnostic_abort_reason = ""
        self.can_use_eye_contact = False
        self.diagnostics_details.clear()
        self.diagnostics_label.setText("Comprobando API y cámara virtual en un proceso aislado…")
        program, arguments = self._command(["--diagnose"])
        self.diagnostic.start(program, arguments)
        self.diagnostic_timeout.start(15000)
        self._refresh_controls()

    def _read_diagnostics(self):
        if self._diagnostic_aborted:
            self.diagnostic.readAllStandardOutput()
            return
        self._diagnostic_buffer.extend(bytes(self.diagnostic.readAllStandardOutput()))
        if len(self._diagnostic_buffer) > 128_000:
            self._diagnostic_buffer.clear()
            self._diagnostic_timed_out("Diagnóstico detenido por exceso de salida.")

    def _diagnostic_finished(self, exit_code=0, exit_status=QProcess.ExitStatus.NormalExit):
        self.diagnostic_timeout.stop()
        if self._diagnostic_aborted:
            self.can_use_eye_contact = False
            self.diagnostics_label.setText(self._diagnostic_abort_reason or "Diagnóstico detenido por tiempo límite.")
            self._refresh_controls()
            return
        self._read_diagnostics()
        if self._diagnostic_aborted:
            self._refresh_controls()
            return
        result = None
        for line in self._diagnostic_buffer.splitlines():
            try:
                candidate = json.loads(line)
                if isinstance(candidate, dict) and candidate.get("type") == "DIAGNOSTICS":
                    result = candidate
            except (ValueError, UnicodeError):
                continue
        normal_exit = exit_status == QProcess.ExitStatus.NormalExit and exit_code == 0
        if result and normal_exit:
            self.can_use_eye_contact = bool(
                result.get("packages_ok")
                and result.get("face_mesh_ok", True)
                and result.get("obs_driver_ok")
            )
            self.diagnostics_label.setText("Entorno Eye Contact disponible" if self.can_use_eye_contact else "Eye Contact no disponible; el modo lectura sigue disponible")
            failures = [f"{name}: {detail}" for name, ok, detail in result.get("packages_details", []) if not ok]
            self.diagnostics_details.setText("\n".join([*failures, result.get("obs_details", "")]))
        elif result:
            self.can_use_eye_contact = False
            self.diagnostics_label.setText("Eye Contact no disponible; el modo lectura sigue disponible")
            failures = [f"{name}: {detail}" for name, ok, detail in result.get("packages_details", []) if not ok]
            self.diagnostics_details.setText("\n".join([*failures, result.get("obs_details", "")]))
        else:
            self.can_use_eye_contact = False
            self.diagnostics_label.setText("El diagnóstico no respondió. El modo lectura sigue disponible.")
            self.diagnostics_details.clear()
        self._refresh_controls()

    def _diagnostic_failed(self, *_):
        self.diagnostic_timeout.stop()
        if self._diagnostic_aborted:
            return
        self._diagnostic_aborted = True
        self._diagnostic_abort_reason = "No se pudo completar el diagnóstico."
        self.can_use_eye_contact = False
        self.diagnostics_label.setText(self._diagnostic_abort_reason)
        self.diagnostics_details.clear()
        self._refresh_controls()

    def _diagnostic_timed_out(self, reason="Diagnóstico detenido por tiempo límite."):
        if self._diagnostic_aborted:
            return
        self._diagnostic_aborted = True
        self._diagnostic_abort_reason = reason
        self._diagnostic_buffer.clear()
        self.diagnostic.kill()
        self.can_use_eye_contact = False
        self.diagnostics_label.setText(reason)
        self.diagnostics_details.clear()
        self._refresh_controls()

    def on_select_script_clicked(self):
        path, _ = QFileDialog.getOpenFileName(self, "Seleccionar guion", "", "Texto (*.txt *.md)")
        if path:
            self.selected_script_path = path
            self.script_path_label.setText(os.path.basename(path))

    def _launch(self, mode):
        if self._running() or self._exiting:
            return
        if mode == "eye_contact" and not self.can_use_eye_contact:
            return
        if not self.writer.is_idle():
            self._pending_mode = mode
            self.status_label.setText("Guardando ajustes antes de iniciar…")
            self._refresh_controls()
            return
        if self.writer.has_unsaved_changes() and not self.writer.flush_sync():
            self.status_label.setText("No se inició la sesión porque quedan ajustes sin guardar.")
            self._refresh_controls()
            return
        arguments = ["--mode", mode, "--dashboard"]
        if self.selected_script_path:
            arguments += ["--script", self.selected_script_path]
        if mode == "eye_contact":
            arguments += ["--camera", str(self.camera_index.value()), "--profile", self.profile.currentData(),
                          "--intensity", str(self.intensity_slider.value()),
                          "--eye-contact-enabled", str(int(self.toggle_checkbox.isChecked()))]
        self._session_error = ""
        self._session_buffer.clear()
        self._stopping = False
        program, arguments = self._command(arguments)
        self.status_label.setText("Iniciando sesión…")
        self.session.start(program, arguments)
        self._refresh_controls()

    def _read_session(self):
        self._session_buffer.extend(bytes(self.session.readAllStandardOutput()))
        while b"\n" in self._session_buffer:
            line, _, rest = self._session_buffer.partition(b"\n")
            self._session_buffer = bytearray(rest)
            try:
                message = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(message, dict):
                continue
            if message.get("type") == "ERROR":
                self._session_error = str(message.get("message", "Error de sesión"))
            elif message.get("type") == "WARNING" and message.get("message"):
                self.settings_label.setText(str(message["message"]))
                self.settings_label.setStyleSheet("color: #FBBF24")
            if message.get("message"):
                suffix = f" | {message['fps']} FPS | {message.get('processing_ms', 0)} ms" if "fps" in message else ""
                self.status_label.setText(str(message["message"]) + suffix)
        if len(self._session_buffer) > 128_000:
            self._session_buffer.clear()

    def _read_session_stderr(self):
        # Consumir siempre la tubería; los módulos escriben detalles en logs locales.
        self.session.readAllStandardError()

    def _session_failed(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._session_error = "No se pudo iniciar la sesión: " + self.session.errorString()
            self.status_label.setText(self._session_error)
            self._refresh_controls()
            if not self._exiting:
                self.showNormal()
                QMessageBox.warning(self, "Sesión detenida", self._session_error)

    def _session_finished(self, code, *_):
        self._read_session()
        self.shutdown_timer.stop()
        self._load_controls()
        self._stopping = False
        self._refresh_controls()
        if not self._exiting and not self.isVisible():
            self.showNormal()
            self.activateWindow()
        if self._session_error or code:
            message = self._session_error or "La sesión terminó con error. Revisa .dasheyes/logs."
            self.status_label.setText(message)
            if not self._exiting:
                self.showNormal()
                QMessageBox.warning(self, "Sesión detenida", message)
        else:
            self.status_label.setText("Sesión cerrada")

    def _request_stop(self):
        if self._running() and not self._stopping:
            self._stopping = True
            self.session.write(b'{"cmd":"STOP"}\n')
            self.session.closeWriteChannel()
            self.shutdown_timer.start(9000)
            self.status_label.setText("Cerrando cámara y guardando ajustes…")
            self._refresh_controls()

    def _kill_session(self):
        if self._running():
            self.logger.warning("El cierre cooperativo agotó su plazo")
            self._session_error = "Fue necesario terminar la sesión por tiempo límite."
            self.session.kill()  # El Job Object del orquestador libera también sus hijos.

    def _force_exit(self):
        if self._exiting:
            return
        self._exiting = True
        self._pending_mode = None
        self.writer.finish()
        self.diagnostic.kill()
        self._request_stop()
        self._refresh_controls()
        self.exit_monitor.start()
        self._finish_exit()

    def _finish_exit(self):
        if self._exiting and not self._running() and self.diagnostic.state() == QProcess.ProcessState.NotRunning and not self.writer.isRunning():
            self.exit_monitor.stop()
            if self.writer.has_unsaved_changes() and not self._force_discard_exit:
                if not self.writer.flush_sync() and self.isVisible():
                    box = QMessageBox(self)
                    box.setIcon(QMessageBox.Icon.Warning)
                    box.setWindowTitle("Ajustes sin guardar")
                    box.setText(f"No se pudieron guardar los ajustes antes de salir.\n{self.writer.last_error}")
                    retry_btn = box.addButton("Reintentar", QMessageBox.ButtonRole.AcceptRole)
                    discard_btn = box.addButton("Salir sin guardar", QMessageBox.ButtonRole.DestructiveRole)
                    cancel_btn = box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
                    box.exec()
                    clicked = box.clickedButton()
                    if clicked == retry_btn and self.writer.flush_sync():
                        pass
                    elif clicked == cancel_btn or (clicked == retry_btn and self.writer.has_unsaved_changes()):
                        self._exiting = False
                        old_writer = self.writer
                        self.writer = SettingsWriter(self)
                        self.writer.adopt_unsaved(old_writer)
                        self.writer.saved.connect(self._settings_saved)
                        self.writer.idle.connect(self._start_pending)
                        self.writer.start()
                        self._refresh_controls()
                        return
                    elif clicked == discard_btn:
                        self._force_discard_exit = True
                        self.writer.discard_unsaved()
                else:
                    self._force_discard_exit = True
                    self.writer.discard_unsaved()
            self.close()

    def closeEvent(self, event):
        if not self._exiting:
            if self._running() and QSystemTrayIcon.isSystemTrayAvailable():
                self.hide()
                event.ignore()
                return
            self._force_exit()
        if self._running() or self.writer.isRunning() or self.diagnostic.state() != QProcess.ProcessState.NotRunning:
            event.ignore()
            return
        self.tray_icon.hide()
        event.accept()
        QApplication.quit()


def main():
    mp.freeze_support()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    try:
        with config_manager.file_lock("dashboard.lock", timeout=0):
            window = DashEyesDashboard()
            window.show()
            return app.exec()
    except (config_manager.ConfigError, OSError) as exc:
        QMessageBox.warning(None, "DashEyes", f"No se pudo abrir el Dashboard: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
