"""
main.py - Orquestador Multiprocesos de DashEyes (V2.0 Engine)
Incluye arquitectura de comunicación con Pipes Unidireccionales,
Handshake ACK_STOP y protección de kernel para DirectShow.
"""

import sys
import time
import multiprocessing as mp
from teleprompter_ui import run_teleprompter_process
from video_worker import run_video_worker


def main():
    mp.freeze_support()

    print("=" * 68)
    print("      DASHEYES V2.0 - TELEPROMPTER INVISIBLE & MOTOR BLINDADO       ")
    print("=" * 68)
    print("[ORQUESTADOR] Inicializando arquitectura de Pipes desacoplados...")

    # Canales de comunicación unidireccionales (Kernel Pipes)
    cmd_pipe_parent, cmd_pipe_child = mp.Pipe(duplex=False)
    ack_pipe_child, ack_pipe_parent = mp.Pipe(duplex=False)

    # 1. Proceso 2: Motor de Video
    video_process = mp.Process(
        target=run_video_worker,
        args=(cmd_pipe_child, ack_pipe_child, None),
        name="DashEyes-VideoWorker"
    )

    # 2. Proceso 1: Teleprompter Invisible
    teleprompter_process = mp.Process(
        target=run_teleprompter_process,
        args=(None,),
        name="DashEyes-Teleprompter"
    )

    print("[ORQUESTADOR] Lanzando Motor de Video y Cámara Virtual...")
    video_process.start()

    print("[ORQUESTADOR] Lanzando Teleprompter Invisible...")
    teleprompter_process.start()

    print("\n[ESTADO: ACTIVO] Teleprompter y Cámara Virtual operando en paralelo.")
    print("  • Para salir limpiamente, presiona [Escape] en el teleprompter o [Ctrl+C].\n")

    try:
        while True:
            if not teleprompter_process.is_alive():
                print("\n[ORQUESTADOR] Teleprompter cerrado por el usuario.")
                break

            if not video_process.is_alive():
                print("\n[ORQUESTADOR] El proceso de video ha terminado.")
                break

            time.sleep(0.2)

    except KeyboardInterrupt:
        print("\n[ORQUESTADOR] Interrupción detectada por teclado.")

    finally:
        print("\n" + "-" * 68)
        print("[ORQUESTADOR] EJECUTANDO PROTOCOLO HANDSHAKE VIA PIPE...")
        print("-" * 68)

        # 1. Enviar STOP por el Pipe de comando
        try:
            cmd_pipe_parent.send({"cmd": "STOP"})
            print("[ORQUESTADOR] Comando 'STOP' transmitido al worker.")
        except Exception as e:
            print(f"[ORQUESTADOR] Fallo al emitir STOP: {e}")

        # 2. Esperar confirmación ACK_STOP en el Pipe de confirmación
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
                        print("[ORQUESTADOR] Confirmación 'ACK_STOP' validada por Pipe.")
                        break
            except Exception:
                pass

        if ack_confirmed:
            video_process.join(timeout=1.0)
            print("[ORQUESTADOR] Proceso de video cerrado ordenadamente.")
        else:
            print("[ORQUESTADOR ADVERTENCIA] Timeout sin ACK. Forzando terminate()...")
            if video_process.is_alive():
                video_process.terminate()
                video_process.join(timeout=1.0)

        # 3. Finalizar Teleprompter
        if teleprompter_process.is_alive():
            teleprompter_process.join(timeout=1.5)
            if teleprompter_process.is_alive():
                teleprompter_process.terminate()
                teleprompter_process.join(timeout=0.5)

        # 4. Cerrar descriptores de los Pipes en el proceso padre
        try:
            cmd_pipe_parent.close()
            cmd_pipe_child.close()
            ack_pipe_parent.close()
            ack_pipe_child.close()
        except Exception:
            pass

        print("[ORQUESTADOR] Descriptores liberados. Sistema cerrado sin fugas.")
        print("=" * 68)


if __name__ == "__main__":
    main()
