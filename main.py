"""
main.py - Orquestador Multiprocesos de DashEyes (V2.0 Engine)
Incluye Handshake Bidireccional de Cierre Seguro (ACK_STOP) y protección
del driver DirectShow de Windows para evitar bloqueos en memoria.
"""

import sys
import time
import queue
import multiprocessing as mp
from teleprompter_ui import run_teleprompter_process
from video_worker import run_video_worker


def main():
    # Inicialización limpia en Windows (soporte multiprocessing spawn)
    mp.freeze_support()

    print("=" * 68)
    print("      DASHEYES V2.0 - TELEPROMPTER INVISIBLE & MOTOR BLINDADO       ")
    print("=" * 68)
    print("[ORQUESTADOR] Inicializando cola IPC y arquitectura multiprocesos...")

    # Cola de mensajería Inter-Procesos bidireccional
    ipc_queue = mp.Queue()

    # 1. Proceso 2: Motor de Video Blindado (Captura Threaded + MediaPipe + VirtualCam)
    video_process = mp.Process(
        target=run_video_worker,
        args=(ipc_queue,),
        name="DashEyes-VideoWorker"
    )

    # 2. Proceso 1: Teleprompter Invisible (PyQt6 + WDA_EXCLUDEFROMCAPTURE)
    teleprompter_process = mp.Process(
        target=run_teleprompter_process,
        args=(ipc_queue,),
        name="DashEyes-Teleprompter"
    )

    print("[ORQUESTADOR] Lanzando Motor de Video en segundo plano...")
    video_process.start()

    print("[ORQUESTADOR] Lanzando Teleprompter Invisible (UI)...")
    teleprompter_process.start()

    print("\n[ESTADO: ACTIVO] Ambos procesos operan en paralelo.")
    print("  • Teleprompter invisible a capturas de Zoom/Teams/OBS.")
    print("  • Motor de video con Threaded Grabber a 30 FPS estables.")
    print("  • Para salir de forma segura, presiona [Escape] en el teleprompter o [Ctrl+C] en la terminal.\n")

    # Bucle de supervisión del ciclo de vida
    try:
        while True:
            # Monitoreo prioritario: Si el usuario cierra el teleprompter (Esc o botón cerrar)
            if not teleprompter_process.is_alive():
                print("\n[ORQUESTADOR] Teleprompter cerrado por el usuario.")
                break

            # Si el proceso de video falla inesperadamente
            if not video_process.is_alive():
                print("\n[ORQUESTADOR] El proceso de video ha terminado inesperadamente.")
                break

            time.sleep(0.2)

    except KeyboardInterrupt:
        print("\n[ORQUESTADOR] Interrupción manual por teclado detectada.")

    finally:
        print("\n" + "-" * 68)
        print("[ORQUESTADOR] INICIANDO PROTOCOLO DE CIERRE SEGURO (HANDSHAKE)...")
        print("-" * 68)

        # 1. Notificar al Video Worker mediante comando STOP
        try:
            ipc_queue.put_nowait({"cmd": "STOP"})
            print("[ORQUESTADOR] Comando 'STOP' enviado al worker de video.")
        except Exception as e:
            print(f"[ORQUESTADOR] Error al emitir STOP: {e}")

        # 2. Esperar confirmación Handshake (ACK_STOP) durante una ventana de hasta 3 segundos
        ack_confirmed = False
        start_wait = time.time()
        timeout_sec = 3.0

        while time.time() - start_wait < timeout_sec:
            if not video_process.is_alive():
                ack_confirmed = True
                print("[ORQUESTADOR] El worker de video ya ha cerrado sus descriptores.")
                break

            try:
                msg = ipc_queue.get_nowait()
                if isinstance(msg, dict) and msg.get("cmd") == "ACK_STOP":
                    ack_confirmed = True
                    print("[ORQUESTADOR] Confirmación 'ACK_STOP' recibida: Hardware y driver liberados.")
                    break
            except queue.Empty:
                time.sleep(0.05)

        # 3. Proceder al join o forzar terminación solo si no hubo respuesta
        if ack_confirmed:
            video_process.join(timeout=1.0)
            print("[ORQUESTADOR] Proceso de video cerrado limpiamente.")
        else:
            print("[ORQUESTADOR ADVERTENCIA] Timeout de 3s agotado sin ACK. Forzando terminate()...")
            if video_process.is_alive():
                video_process.terminate()
                video_process.join(timeout=1.0)

        # 4. Asegurar terminación del teleprompter
        if teleprompter_process.is_alive():
            teleprompter_process.join(timeout=1.5)
            if teleprompter_process.is_alive():
                teleprompter_process.terminate()
                teleprompter_process.join(timeout=0.5)

        # 5. Cerrar de forma segura la cola IPC
        try:
            ipc_queue.close()
            ipc_queue.join_thread()
        except Exception:
            pass

        print("[ORQUESTADOR] Driver DirectShow y procesos cerrados sin riesgo de bloqueo.")
        print("=" * 68)


if __name__ == "__main__":
    main()
