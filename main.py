"""
main.py - Orquestador Multiprocesos de DashEyes (V2.0 Final)
freeze_support() en primera línea, Pipes unidireccionales y soporte para --script.
"""

import sys
import multiprocessing as mp

# CRÍTICO: freeze_support() en primera línea para PyInstaller en Windows
if __name__ == "__main__":
    mp.freeze_support()

import time
import argparse
from teleprompter_ui import run_teleprompter_process
from video_worker import run_video_worker


def parse_arguments():
    parser = argparse.ArgumentParser(description="DashEyes Orchestrator")
    parser.add_argument("--script", default=None, help="Ruta hacia el archivo de guión (.txt o .md)")
    args, _ = parser.parse_known_args()
    return args


def main():
    mp.freeze_support()
    args = parse_arguments()

    print("=" * 68)
    print("      DASHEYES V2.0 - TELEPROMPTER INVISIBLE & MOTOR BLINDADO       ")
    print("=" * 68)

    cmd_pipe_parent, cmd_pipe_child = mp.Pipe(duplex=False)
    ack_pipe_child, ack_pipe_parent = mp.Pipe(duplex=False)

    video_process = mp.Process(
        target=run_video_worker,
        args=(cmd_pipe_child, ack_pipe_child, None),
        name="DashEyes-VideoWorker"
    )

    teleprompter_process = mp.Process(
        target=run_teleprompter_process,
        args=(None, args.script),
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

        try:
            cmd_pipe_parent.send({"cmd": "STOP"})
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
            cmd_pipe_parent.close()
            ack_pipe_parent.close()
        except Exception:
            pass

        print("[ORQUESTADOR] Procesos cerrados correctamente.")


if __name__ == "__main__":
    main()
