"""
main.py - Orquestador Multiprocesos de DashEyes
Coordina la ejecución paralela del Teleprompter Invisible (Proceso 1)
y el Motor de Contacto Visual en Tiempo Real (Proceso 2).
"""

import sys
import time
import multiprocessing as mp
from teleprompter_ui import run_teleprompter_process
from video_worker import run_video_worker


def main():
    # Indispensable en Windows para inicialización limpia de procesos spawn
    mp.freeze_support()

    print("=" * 65)
    print("      DASHEYES - TELEPROMPTER INVISIBLE & CONTACTO VISUAL      ")
    print("=" * 65)
    print("[ORQUESTADOR] Inicializando arquitectura de procesos...")

    # Cola de mensajería Inter-Procesos (IPC)
    ipc_queue = mp.Queue()

    # 1. Proceso 2: Motor de Video (Captura -> MediaPipe Iris -> pyvirtualcam)
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

    print("[ORQUESTADOR] Iniciando Motor de Video y Cámara Virtual...")
    video_process.start()

    print("[ORQUESTADOR] Iniciando Teleprompter Invisible (UI)...")
    teleprompter_process.start()

    print("\n[LISTO] Ambos procesos están en ejecución paralela.")
    print("  • Teleprompter activo en tu pantalla (invisible en capturas de pantalla).")
    print("  • Cámara virtual transmitiendo con corrección de mirada.")
    print("  • Para salir, presiona [Escape] en el teleprompter o [Ctrl+C] en esta terminal.\n")

    # Bucle de supervisión del ciclo de vida
    try:
        while True:
            # Monitoreo activo: si el usuario cierra el teleprompter, cerramos el sistema
            if not teleprompter_process.is_alive():
                print("\n[ORQUESTADOR] Teleprompter cerrado por el usuario.")
                break

            # Si el proceso de video falla inesperadamente, detenemos el sistema
            if not video_process.is_alive():
                print("\n[ORQUESTADOR] El proceso de video ha terminado inesperadamente.")
                break

            time.sleep(0.3)

    except KeyboardInterrupt:
        print("\n[ORQUESTADOR] Interrupción detectada por teclado (Ctrl+C).")

    finally:
        print("[ORQUESTADOR] Ejecutando cierre seguro (Graceful Shutdown)...")

        # 1. Enviar señal de terminación al worker de video mediante la cola IPC
        try:
            ipc_queue.put_nowait({"cmd": "STOP"})
        except Exception:
            pass

        # 2. Esperar cierre ordenado del proceso de video (liberación de webcam y driver)
        if video_process.is_alive():
            video_process.join(timeout=3.0)
            if video_process.is_alive():
                print("[ORQUESTADOR] Forzando terminación del proceso de video...")
                video_process.terminate()
                video_process.join(timeout=1.0)

        # 3. Asegurar cierre del teleprompter
        if teleprompter_process.is_alive():
            teleprompter_process.join(timeout=2.0)
            if teleprompter_process.is_alive():
                print("[ORQUESTADOR] Forzando terminación del teleprompter...")
                teleprompter_process.terminate()
                teleprompter_process.join(timeout=1.0)

        # 4. Cerrar la cola IPC
        try:
            ipc_queue.close()
            ipc_queue.join_thread()
        except Exception:
            pass

        print("[ORQUESTADOR] DashEyes ha finalizado limpiamente. Procesos cerrados.")


if __name__ == "__main__":
    main()
