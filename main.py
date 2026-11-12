"""Orquestador sin dependencias gráficas ni de vídeo al importar.

Cada cola tiene una función explícita. El padre es el único emisor hacia vídeo;
el Event de parada no depende de que una cola esté vacía o tenga espacio.
"""

import multiprocessing as mp

if __name__ == "__main__":
    mp.freeze_support()

import argparse
import json
import queue
import signal
import sys
import threading
import time

import config_manager
from protocol import coalesce_commands, drain, offer, offer_latest, valid_command
from runtime import ProcessJob, get_logger, restore_redirected_stdio


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description="DashEyes")
    parser.add_argument("--mode", choices=["teleprompter", "eye_contact"], default="teleprompter")
    parser.add_argument("--script")
    parser.add_argument("--camera", type=int)
    parser.add_argument("--profile", choices=list(config_manager.PROFILES))
    parser.add_argument("--intensity", type=int)
    parser.add_argument("--eye-contact-enabled", type=int, choices=[0, 1])
    parser.add_argument("--dashboard", action="store_true", help="Recibir JSON por stdin")
    parser.add_argument("--diagnose", action="store_true", help="Comprobar dependencias y salir")
    parser.add_argument("--no-camera", action="store_true", help="Diagnóstico sin abrir cámara virtual")
    parser.add_argument("--native-first", action="store_true", help="Comparar importación de MediaPipe antes de OpenCV")
    args = parser.parse_args(argv)
    if args.no_camera and not args.diagnose:
        parser.error("--no-camera requiere --diagnose")
    if args.native_first and not args.diagnose:
        parser.error("--native-first requiere --diagnose")
    return args


def emit(message):
    if sys.stdout is not None:
        try:
            print(json.dumps(message, ensure_ascii=True), flush=True)
        except (OSError, ValueError):
            pass


def _run_video(commands, events, previews, stop, settings, shm_name=None):
    logger = get_logger("video")
    previews.cancel_join_thread()
    try:
        try:
            from video_worker import run_video_worker
        except Exception:
            logger.exception("No se pudo importar el motor")
            offer(events, {"type": "ERROR", "message": "No se pudo cargar el motor. Revisa las dependencias y el registro local."})
            raise
        run_video_worker(commands, events, previews, stop, settings, shm_name=shm_name)
    finally:
        # El padre drena esta cola también durante el cierre.
        events.close()
        events.join_thread()


def _run_ui(commands, events, previews, stop, script, settings, eye_mode, shm_name=None):
    commands.cancel_join_thread()
    try:
        from teleprompter_ui import run_teleprompter_process
        run_teleprompter_process(commands, events, previews, stop, script, settings, eye_mode, shm_name=shm_name)
    except Exception:
        get_logger("teleprompter").exception("Error de interfaz")
        offer(commands, {"type": "ERROR", "message": "No se pudo abrir el teleprompter. Revisa las dependencias de interfaz."})
        stop.set()
        raise


def _read_dashboard(incoming, stop):
    if sys.stdin is None:
        stop.set()
        return
    try:
        for line in sys.stdin:
            if stop.is_set():
                return
            if len(line) > 4096:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if valid_command(message):
                if message["cmd"] == "STOP":
                    stop.set()
                    return
                if not offer_latest(incoming, message):
                    offer(incoming, message)
    except (OSError, ValueError):
        pass
    finally:
        stop.set()  # También al desaparecer el Dashboard.


def supervise(args):
    logger = get_logger("session")
    context = mp.get_context("spawn")
    stop = context.Event()
    signal_stop = threading.Event()
    incoming = queue.Queue(maxsize=128)
    ui_commands = context.Queue(maxsize=128)
    video_commands = context.Queue(maxsize=128)
    video_events = context.Queue(maxsize=64)
    ui_events = context.Queue(maxsize=64)
    previews = context.Queue(maxsize=1)
    channels = (ui_commands, video_commands, video_events, ui_events, previews)
    processes = []
    job = None
    preview_shm = None
    shm_name = None
    exit_code = 0
    emitted_error = False
    settings = config_manager.load_config()
    # Los argumentos son ajustes de esta sesión, no sobrescriben preferencias.
    if args.camera is not None:
        settings["video"]["camera_index"] = args.camera
    if args.profile is not None:
        settings["video"]["profile"] = args.profile
    if args.intensity is not None:
        settings["eye_contact"]["intensity"] = args.intensity
    if args.eye_contact_enabled is not None:
        settings["eye_contact"]["enabled"] = bool(args.eye_contact_enabled)
    settings = config_manager.normalize_config(settings)
    eye_mode = args.mode == "eye_contact"
    ready = not eye_mode
    ui_ready = False
    started = time.monotonic()
    last_video_heartbeat = started
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: signal_stop.set())
        job = ProcessJob()
        if args.dashboard:
            threading.Thread(target=_read_dashboard, args=(incoming, stop), daemon=True).start()
        if eye_mode and config_manager.level2_preview_enabled(settings):
            try:
                from shared_buffer import SharedPreviewBuffer
                preview_shm = SharedPreviewBuffer.create()
                shm_name = preview_shm.name
            except Exception:
                logger.warning("No se pudo reservar memoria compartida de Nivel 2; usando modo estándar.", exc_info=True)
                preview_shm = None
                shm_name = None
        if eye_mode:
            video = context.Process(
                target=_run_video, args=(video_commands, video_events, previews, stop, settings, shm_name),
                name="DashEyes-Video",
            )
            video.start()
            processes.append(video)
            job.add(video)
        ui = context.Process(
            target=_run_ui,
            args=(ui_commands, ui_events, previews, stop, args.script, settings, eye_mode, shm_name),
            name="DashEyes-Teleprompter",
        )
        ui.start()
        processes.append(ui)
        job.add(ui)
        emit({"type": "STARTING", "mode": args.mode})
        while not stop.wait(0.03):
            if signal_stop.is_set():
                stop.set()
                break
            for channel in (incoming, ui_commands):
                for message in coalesce_commands(list(drain(channel))):
                    if isinstance(message, dict) and message.get("type") == "UI_READY":
                        ui_ready = True
                        if not eye_mode:
                            emit({"type": "READY", "message": "Teleprompter listo; lectura pausada."})
                        continue
                    if isinstance(message, dict) and message.get("type") == "WARNING":
                        emit(message)
                        continue
                    if isinstance(message, dict) and message.get("type") == "ERROR":
                        emit(message)
                        emitted_error = True
                        exit_code = 1
                        stop.set()
                        continue
                    if not valid_command(message):
                        continue
                    if message["cmd"] == "STOP":
                        stop.set()
                    elif eye_mode:
                        sent = offer_latest(video_commands, message) if message["cmd"] in {"SET_INTENSITY", "TOGGLE_CORRECTION", "PREVIEW"} else offer(video_commands, message)
                        if not sent:
                            warning = {"type": "WARNING", "reconcile": True, "message": "Control no enviado; vuelve a intentarlo."}
                            emit(warning)
                            offer_latest(ui_events, warning)
            for message in drain(video_events):
                last_video_heartbeat = time.monotonic()
                emit(message)
                offer_latest(ui_events, message)
                if message.get("type") == "READY":
                    ready = True
                elif message.get("type") == "ERROR":
                    emitted_error = True
                    exit_code = 1
                    stop.set()
            if not ui.is_alive():
                exit_code = exit_code or int(ui.exitcode not in (0, None))
                stop.set()
            if eye_mode and not video.is_alive():
                if not stop.is_set():
                    emit({"type": "ERROR", "message": "El motor de vídeo terminó inesperadamente."})
                    emitted_error = True
                    exit_code = 1
                stop.set()
            now = time.monotonic()
            if (not ready or not ui_ready) and now - started > 30:
                emit({"type": "ERROR", "message": "La cámara o el motor no respondió en 30 segundos."})
                emitted_error = True
                exit_code = 1
                stop.set()
            elif eye_mode and ready and now - last_video_heartbeat > 12:
                emit({"type": "ERROR", "message": "El motor de vídeo dejó de responder."})
                emitted_error = True
                exit_code = 1
                stop.set()
    except Exception as exc:
        logger.exception("Error de sesión")
        emit({"type": "ERROR", "message": str(exc)})
        emitted_error = True
        exit_code = 1
    finally:
        stop.set()
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and any(p.is_alive() for p in processes):
            for message in drain(video_events):
                if message.get("type") == "ERROR":
                    emitted_error = True
                    exit_code = 1
                emit(message)
            time.sleep(0.03)
        for process in processes:
            process.join(timeout=max(0, deadline - time.monotonic()))
        for process in processes:
            if process.is_alive():
                logger.warning("Terminación de respaldo: %s", process.name)
                exit_code = 1
                process.terminate()
                process.join(timeout=1)
        if job is not None:
            job.close()
        for message in drain(ui_commands):
            if isinstance(message, dict) and message.get("type") in ("ERROR", "WARNING"):
                emit(message)
                if message.get("type") == "ERROR":
                    emitted_error = True
                    exit_code = 1
        for message in drain(video_events):
            if message.get("type") == "ERROR":
                emitted_error = True
                exit_code = 1
            emit(message)
        for process in processes:
            if process.exitcode not in (0, None):
                if not emitted_error:
                    emit({"type": "ERROR", "message": f"{process.name} terminó con error. Revisa el registro local."})
                    emitted_error = True
                exit_code = 1
        for channel in channels:
            channel.cancel_join_thread()
            channel.close()
        for process in processes:
            if not process.is_alive():
                process.close()
        if preview_shm is not None:
            preview_shm.close()
        emit({"type": "STOPPED", "exit_code": exit_code})
    return exit_code


def main(argv=None):
    restore_redirected_stdio()
    args = parse_arguments(argv)
    if args.diagnose:
        from diagnostics import diagnose
        result = diagnose(check_camera=not args.no_camera, native_first=args.native_first)
        emit(result)
        return 0 if result["packages_ok"] else 1
    try:
        with config_manager.file_lock("session.lock", timeout=0):
            return supervise(args)
    except (config_manager.ConfigError, OSError) as exc:
        emit({"type": "ERROR", "message": f"No se pudo abrir la sesión: {exc}"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
