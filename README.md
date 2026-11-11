# DashEyes

Aplicación local para Windows con teleprompter y corrección **sutil** de mirada.
Orientada a CPU y equipos de 16 GB de RAM, sin GPU dedicada ni servicios de pago.

> Esta revisión está pendiente de ejecutar las pruebas y validar cámara, calidad
> visual y empaquetado en Windows. Los FPS son objetivos, no resultados medidos.

## Modos

### Teleprompter

- No abre la webcam ni requiere OBS o las bibliotecas de vídeo.
- Empieza pausado. Botón visible para reanudar, controles y cierre.
- Guiones `.txt` y `.md` como texto plano UTF-8, hasta 2 MB.
- Arrastre desde el texto, tamaño ajustable y ancho de lectura configurable.
- Fuente, fondo, velocidad y tamaño se guardan en segundo plano.
- Resaltado del párrafo superior visible; no es seguimiento por voz.

### Eye Contact

- Entrada de cámara física seleccionada por índice; salida **OBS Virtual Camera**.
- Calibración frontal de 25 muestras estables por sesión.
- Zona sin corrección, límites proporcionales al tamaño del ojo y suavizado temporal.
- Se conserva el original ante parpadeos, poses no fiables o desviaciones grandes.
- La deformación se limita al interior del contorno ocular.
- Previsualización original/corregido opcional, aproximadamente hasta 5 imágenes/s.
- Indicadores del estado efectivo, FPS y coste de procesamiento.
- Reconexión acotada de cámara. Durante una pérdida prolongada se envía negro,
  no una imagen antigua del rostro presentada como si fuera vídeo vivo.

**Límite:** el motor no reconstruye ojos mediante IA generativa ni garantiza
contacto visual para cualquier ángulo. Calibra de nuevo si cambias cámara,
posición o iluminación. Las gafas y las oclusiones pueden reducir la calidad.

## Uso desde el Dashboard

1. Abre DashEyes con tu mecanismo habitual de ejecución.
2. Selecciona un guion si lo necesitas.
3. El modo lectura está disponible aunque falle el diagnóstico de vídeo.
4. Para Eye Contact, selecciona el índice de tu cámara física y el perfil.
   No selecciones una cámara virtual como entrada.
5. Inicia la sesión, abre **Controles** y pulsa **Calibrar mirando al objetivo**.
6. Mantén la cabeza quieta y mira al objetivo hasta completar las muestras.
7. Compara original/corregido antes de utilizar la salida en una videollamada.
8. En Zoom, Meet o Teams, selecciona **OBS Virtual Camera** como cámara.

Desmarcar la corrección **no apaga la webcam**: mantiene el vídeo sin efecto.
Para apagarla, detén la sesión o cierra el teleprompter.

El botón de comprobar entorno **no instala paquetes**, no solicita permisos de
administrador y ejecuta el diagnóstico aislado con un tiempo límite.

## Atajos

| Atajo | Acción |
|---|---|
| F8 | Pausa/reanuda la lectura |
| F9 / F10 | Reduce/aumenta la velocidad |
| F2 | Muestra/oculta controles |
| Espacio | Pausa, con la ventana enfocada |
| Arriba / Abajo | Velocidad, con la ventana enfocada |
| Esc / ✕ | Cierra la sesión |

F8/F9/F10/F2 intentan registrarse globalmente en Windows. Si otra aplicación
los ocupa, se muestra un aviso y quedan disponibles al enfocar el teleprompter.

## Privacidad y captura de pantalla

Windows recibe una solicitud de exclusión del teleprompter mediante
`SetWindowDisplayAffinity`. La interfaz informa si la solicitud fue aceptada.
**No es una garantía universal de invisibilidad:** comprueba la aplicación y
el mecanismo de captura que vas a utilizar antes de compartir pantalla.

No se envían imágenes a servicios externos, no se graba vídeo y no se guardan
guiones. La previsualización viaja entre procesos locales en una cola acotada.
Los registros contienen estado y excepciones, no imágenes ni texto del guion;
los errores del sistema pueden incluir rutas locales.

## Datos portátiles

Todos los ajustes, cerrojos y registros se crean en **`.dasheyes/` junto al
programa**, no en carpetas externas. Esa carpeta debe permitir escritura;
no instales esta versión portátil dentro de una carpeta protegida de Windows.

- Lecturas sin creación ni escritura automática de configuración.
- Validación de estructura, tipos y rangos.
- Transacciones de lectura/modificación/escritura bajo bloqueo entre procesos.
- Temporales únicos y reemplazo atómico.
- Un `config.json` anterior junto al código se lee como respaldo sin modificarlo.
- Antes de reemplazar una configuración corrupta actual se conserva una copia.
- Los errores de persistencia se notifican; no se confunden con un guardado exitoso.
- Una sola sesión y un solo Dashboard por carpeta de aplicación.

## Perfiles de CPU

| Perfil | Salida máxima solicitada | Ancho de detección |
|---|---|---|
| Ligero | 640×480, objetivo 20 FPS | 320 px |
| Equilibrado | 1280×720, objetivo 30 FPS | 480 px |

Se conserva la proporción real de cámara, sin exigir una resolución no soportada.
Si el procesamiento resulta lento de forma sostenida, se reduce la detección a
320 px. Solo se procesa el fotograma nuevo más reciente. La corrección se aplica
sobre pequeñas regiones oculares; MediaPipe sigue detectando el rostro completo
en una imagen reducida.

## Dependencias y distribución

Base propuesta: **Python 3.11 x64, Windows 10 2004+ o Windows 11**.

- `requirements-teleprompter.txt`: solo interfaz de lectura.
- `requirements.txt`: dependencias directas fijadas para Eye Contact.
- `requirements-build.txt`: añade la versión de PyInstaller.
- OBS es un requisito externo para la cámara virtual y no se instala desde la app.

Se conserva temporalmente MediaPipe **0.10.21 / FaceMesh**. Versiones modernas que
no incluyen `solutions.face_mesh` se rechazan explícitamente. La migración a Tasks
queda para otra revisión. Las dependencias transitivas todavía no tienen un lock
completo: no se afirma reproducibilidad binaria del entorno.

PyInstaller tiene un único punto de entrada (`dashboard.py`) y despacha los modos
antes de importar la interfaz. El orquestador usa colas pequeñas y una señal de
parada independiente; en Windows, un Job Object limita los procesos huérfanos si
desaparece el padre. Un controlador nativo bloqueado puede requerir terminación
de respaldo: no se garantiza eliminar fallos internos de drivers.

Consulta [validación](docs/VALIDATION.md), [plan de trabajo](WORK_PLAN.md) y
[dependencias y licencias](docs/THIRD_PARTY.md). La integración continua está
preparada para pruebas sin cámara y compilación; no ha sido ejecutada durante
esta revisión local.
