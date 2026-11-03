# DashEyes - Teleprompter Invisible & Contacto Visual en Tiempo Real (V2.0)

DashEyes es una aplicación de escritorio de alto rendimiento para Windows diseñada para mejorar videollamadas profesionales, presentaciones y entrevistas en **Zoom, Microsoft Teams, Google Meet y OBS**:

1. **Teleprompter Invisible:** Una ventana flotante sobre tus diapositivas con texto de desplazamiento automático que **nunca aparece en las capturas de pantalla** de los participantes (`WDA_EXCLUDEFROMCAPTURE`).
2. **Corrección de Mirada Blindada (V2.0):** Pipeline con Threaded Camera Capture, control por `frame_id`, compensación de rotación 3D (*Head-Pose Gating*), filtro temporal anti-gelatina y sincronización nominal con `pyvirtualcam`.
3. **Centro de Control (Dashboard):** Panel visual en modo oscuro con diagnóstico de hardware, verificación de permisos de administrador y arranque de un solo clic.

---

## Punto de Entrada Oficial (Dashboard V2.0)

A partir de la versión 2.0, el punto de inicio recomendado es el **Dashboard visual**:

```bash
cd D:\DashEyes
python dashboard.py
```

### Funciones del Dashboard:
* **Verificación de Administrador:** Comprueba los privilegios del sistema para asegurar la correcta comunicación con los drivers.
* **Diagnóstico de Entorno (Checklist Rojo/Verde):** Valida la presencia de paquetes (`PyQt6`, `opencv-python-headless`, `mediapipe`, `pyvirtualcam`, `numpy`) y comprueba la disponibilidad del driver DirectShow de OBS Virtual Camera.
* **Reparar / Instalar Dependencias:** Botón de un solo clic para instalar automáticamente los requisitos con `pip` y abrir el instalador de OBS Studio si el driver no está presente.
* **Iniciar DashEyes:** Botón protegido que se habilita únicamente cuando el entorno está 100% listo, lanzando el orquestador en un subproceso independiente.

---

## Ejecución por Terminal (Modo Avanzado)

Si prefieres omitir el Dashboard y lanzar directamente los procesos:

```bash
python main.py
```

---

## Estructura de Archivos

* **`dashboard.py`**: Centro de control visual con diagnóstico del sistema y lanzador de un clic.
* **`main.py`**: Orquestador principal multiprocesos con protocolo Handshake (`STOP` / `ACK_STOP`) para prevenir bloqueos del driver en el kernel.
* **`teleprompter_ui.py`**: Interfaz de teleprompter invisible con API nativa Win32 `SetWindowDisplayAffinity(0x00000011)`.
* **`video_worker.py`**: Motor de visión por computador con Threaded Grabber, MediaPipe Iris, `solvePnP` 3D gating y `pyvirtualcam`.
* **`requirements.txt`**: Lista de dependencias del entorno.

---

## Requisitos del Sistema

1. **Sistema Operativo:** Windows 10 (versión 2004 Build 19041+) o Windows 11.
2. **Python:** 3.10 o 3.11.
3. **Cámara Virtual:** Tener instalado **OBS Studio** (o su driver independiente DirectShow).

---

## Controles del Teleprompter

* **Barra Espaciadora:** Pausar o reanudar el desplazamiento del texto.
* **Flecha Arriba (↑):** Aumentar la velocidad del scroll (Niveles 1 a 10).
* **Flecha Abajo (↓):** Disminuir la velocidad del scroll.
* **Clic Izquierdo + Arrastrar:** Posicionar la ventana libremente en la pantalla (idealmente cerca de tu webcam).
* **Escape (Esc):** Cerrar la aplicación de forma segura (detiene la cámara virtual y libera los recursos del sistema sin procesos zombis).
