"""
main.py - Orquestador Multiprocesos de DashEyes (V2.0 Dual Mode / Nivel 1)
freeze_support() en primera línea, arranque condicional por --mode (teleprompter/eye_contact),
conexión directa de tuberías UI -> Video y cierre explícito en finally.
"""

import sys
import multiprocessing as mp

# CRÍTICO: freeze_support() en primera línea para PyInstaller en Windows
if __name__ == "__main__":
    mp.freeze_support()

import time
import argparse
import config_manager
from teleprompter_ui import run_teleprompter_process
from video_worker import run_video_worker


def parse_arguments():
    parser = argparse.ArgumentParser(description="DashEyes Orchestrator")
    parser.add_argument("--script", default=None, help="Ruta hacia el archivo de guión (.txt o .md)")
    parser.add_argument(
        "--mode",
        default="teleprompter",
        choices=["teleprompter", "eye_contact"],
        help="Modo de ejecución: teleprompter (liviano/sin admin) o eye_contact (cámara virtual)"
    )
    parser.add_argument("--intensity", type=int, default=None, help="Intensidad de corrección de mirada (0-100)")
    parser.add_argument("--eye-contact-enabled", type=int, default=None, help="Estado inicial de activación (1 o 0)")
    args, _ = parser.parse_known_args()
    return args


def main():
    mp.freeze_support()
    args = parse_arguments()

    # Si se pasan parámetros de calibración desde el Dashboard, sincronizar en config_manager
    if args.intensity is not None or args.eye_contact_enabled is not None:
        enabled_bool = bool(args.eye_contact_enabled) if args.eye_contact_enabled is not None else None
        config_manager.update_eye_contact_config(intensity=args.intensity, enabled=enabled_bool)

    print("=" * 68)
    print("      DASHEYES V2.0 - TELEPROMPTER INVISIBLE & MOTOR BLINDADO       ")
    print(f"      MODO SELECCIONADO: {args.mode.upper()}")
    print("=" * 68)

    # 1. MODO TELEPROMPTER (Liviano, sin cámara, sin OBS, sin admin)
    if args.mode == "teleprompter":
        teleprompter_process = mp.Process(
            target=run_teleprompter_process,
            args=(None, args.script),
            name="DashEyes-Teleprompter"
        )
        print(f"[ORQUESTADOR] Iniciando Modo Teleprompter (Guión: {args.script or 'Por defecto'})...")
        teleprompter_process.start()

        print("\n[ESTADO: ACTIVO] Atajos Globales F8 (Pausa), F9/F10 (Velocidad), F2 (HUD).")

        try:
            while teleprompter_process.is_alive():
                time.sleep(0.2)
        except KeyboardInterrupt:
            print("\n[ORQUESTADOR] Interrupción por teclado.")
        finally:
            if teleprompter_process.is_alive():
                teleprompter_process.join(timeout=1.0)
                if teleprompter_process.is_alive():
                    teleprompter_process.terminate()
                    teleprompter_process.join(timeout=0.5)
            print("[ORQUESTADOR] Teleprompter cerrado correctamente.")
        return

    # 2. MODO EYE CONTACT (Completo con video y cámara virtual)
    # Tuberías directas: cmd_pipe_parent se entrega a teleprompter_process para control en vivo
    cmd_pipe_parent, cmd_pipe_child = mp.Pipe(duplex=False)
    ack_pipe_child, ack_pipe_parent = mp.Pipe(duplex=False)

    video_process = mp.Process(
        target=run_video_worker,
        args=(cmd_pipe_child, ack_pipe_child, None),
        name="DashEyes-VideoWorker"
    )

    teleprompter_process = mp.Process(
        target=run_teleprompter_process,
        args=(cmd_pipe_parent, args.script),
        name="DashEyes-Teleprompter"
    )

    print("[ORQUESTADOR] Lanzando Motor de Video y Cámara Virtual...")
    video_process.start()

    cmd_pipe_child.close()
    ack_pipe_child.close()

    print(f"[ORQUESTADOR] Lanzando Teleprompter Invisible (Guión: {args.script or 'Por defecto'})...")
    teleprompter_process.start()

    print("\n[ESTADO: ACTIVO] Atajos Globales F8 (Pausa), F9/F10 (Velocidad), F2 (HUD).")

    try:
        while True:
            if not teleprompter_process.is_alive():
                print("\n[ORQUESTADOR] Teleprompter cerrado.")
                break

            if not video_process.is_alive():
                print("\n[ORQUESTADOR] El proceso de video ha terminado.")
                break

            time.sleep(0.2)

    except KeyboardInterrupt:
        print("\n[ORQUESTADOR] Interrupción por teclado.")

    finally:
        print("\n[ORQUESTADOR] Ejecutando Handshake de parada...")

        # Implementación Opción (b): Enviar STOP de respaldo y cerrar explícitamente el descriptor
        try:
            cmd_pipe_parent.send({"cmd": "STOP"})
        except Exception:
            pass

        try:
            cmd_pipe_parent.close()
        except Exception:
            pass

        ack_confirmed = False
        start_wait = time.time()
        timeout_sec = 3.0

        while time.time() - start_wait < timeout_sec:
            if not video_process.is_alive():
                ack_confirmed = True
                break

            try:
                if ack_pipe_parent.poll(timeout=0.05):
                    msg = ack_pipe_parent.recv()
                    if isinstance(msg, dict) and msg.get("cmd") == "ACK_STOP":
                        ack_confirmed = True
                        break
            except Exception:
                pass

        if ack_confirmed:
            video_process.join(timeout=1.0)
        else:
            if video_process.is_alive():
                video_process.terminate()
                video_process.join(timeout=1.0)

        if teleprompter_process.is_alive():
            teleprompter_process.join(timeout=1.5)
            if teleprompter_process.is_alive():
                teleprompter_process.terminate()
                teleprompter_process.join(timeout=0.5)

        try:
            ack_pipe_parent.close()
        except Exception:
            pass

        print("[ORQUESTADOR] Procesos cerrados correctamente.")


if __name__ == "__main__":
    main()
