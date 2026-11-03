# DashEyes - Teleprompter Invisible & Contacto Visual en Tiempo Real

DashEyes es una aplicación de escritorio de alto rendimiento para Windows que combina dos funcionalidades esenciales para videollamadas profesionales, presentaciones y entrevistas en **Zoom, Microsoft Teams, Google Meet y OBS**:

1. **Teleprompter Invisible:** Una ventana flotante sobre tus diapositivas con texto de desplazamiento automático que **nunca aparece en las capturas de pantalla** de los participantes.
2. **Corrección de Mirada (Eye Contact Engine):** Un pipeline de visión por computador ligero que corre en CPU, aísla el iris mediante MediaPipe FaceMesh y traslada suavemente la pupila hacia la lente de la cámara en una cámara virtual.

---

## Arquitectura del Proyecto

DashEyes utiliza una arquitectura de **dos procesos independientes desacoplados** mediante `multiprocessing` de Python, coordinados por una cola IPC:

```text
               +---------------------------------------+
               |        main.py (Orquestador)          |
               +-------------------+-------------------+
                                   |
             +---------------------+---------------------+
             |                                           |
             v                                           v
+-------------------------+                 +-------------------------+
|      PROCESO 1          |                 |      PROCESO 2          |
|   teleprompter_ui.py    |                 |     video_worker.py     |
| (PyQt6 + Win32 Affinity)|                 | (MediaPipe + VirtualCam)|
+-------------------------+                 +-------------------------+
             |                                           |
             v                                           v
  Invisible en pantalla                       Emisión de cámara virtual
   compartida de Zoom                        con contacto visual directo
```

---

## Estructura de Archivos

* **`main.py`**: Orquestador principal con soporte para `freeze_support()` y apagado seguro (*graceful shutdown*) para prevenir procesos zombis.
* **`teleprompter_ui.py`**: Interfaz de usuario flotante y translúcida construida en PyQt6, con soporte para la API Win32 `SetWindowDisplayAffinity(0x00000011)`.
* **`video_worker.py`**: Motor de captura y procesamiento en tiempo real con cálculo de Eye Aspect Ratio (EAR) para ignorar parpadeos y filtro temporal EMA anti-temblores.
* **`requirements.txt`**: Dependencias necesarias del proyecto.

---

## Requisitos Previos

1. **Sistema Operativo:** Windows 10 (versión 2004 / Build 19041 o superior) o Windows 11 (para la API de exclusión de captura).
2. **Python:** Versión 3.10 o 3.11 recomendada.
3. **Driver de Cámara Virtual:** Tener instalado **OBS Studio** (que incluye el driver DirectShow de cámara virtual) o el instalador independiente de OBS Virtual Camera.

---

## Instalación y Puesta en Marcha

### 1. Clonar o navegar a la carpeta del proyecto:
```bash
cd D:\DashEyes
```

### 2. Instalar dependencias:
```bash
pip install -r requirements.txt
```

### 3. Ejecutar la aplicación completa:
```bash
python main.py
```

---

## Controles del Teleprompter

* **Barra Espaciadora:** Pausar o reanudar el desplazamiento del guion.
* **Flecha Arriba (↑):** Aumentar la velocidad del scroll (Niveles 1 a 10).
* **Flecha Abajo (↓):** Disminuir la velocidad del scroll.
* **Clic Izquierdo + Arrastrar:** Mover la ventana flotante libremente por el monitor (colócala idealmente justo debajo de tu webcam).
* **Escape (Esc):** Cerrar la aplicación de forma segura (detiene automáticamente la cámara virtual y libera los recursos del sistema).

---

## Configuración en tu Aplicación de Videollamada

1. Abre **Zoom / Teams / Meet**.
2. En la selección de cámara web, selecciona **"OBS Virtual Camera"** (el feed generado por DashEyes con contacto visual).
3. Al compartir tu pantalla completa, selecciona tu monitor normal: el teleprompter flotará en tu vista para que puedas leer cómodamente, pero **los participantes solo verán tu escritorio limpio sin el texto**.
