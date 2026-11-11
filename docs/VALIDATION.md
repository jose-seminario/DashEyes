# Validación pendiente

No se han ejecutado la aplicación, comandos, instalaciones ni pruebas durante
esta revisión. Este documento describe comprobaciones futuras; no sus resultados.
Las pruebas en `tests/` están escritas, no aprobadas por ejecución.

## Automatización preparada

La suite usa `unittest` y cubre:

- Lectura sin efectos secundarios, estructura y rangos de configuración.
- Guardados parciales, exclusión mutua entre hilos y entre procesos.
- Fallo de bloqueo sin escritura y preservación de archivos corruptos.
- Compatibilidad de lectura del archivo anterior sin modificarlo.
- Colas llenas sin bloqueo y parada independiente de ellas.
- Separación de importaciones y un único punto de entrada empaquetado.
- Proyección facial sintética frontal y rechazo de poses fallidas o invertidas.
- Intensidad cero, pérdida de rostro, calibración y zona muerta.
- Conservación de píxeles fuera del ojo.
- Propiedad de captura y liberación en el mismo hilo con una cámara simulada.
- Interfaz offscreen: controles, modo lectura y conservación del guion ante errores.
- Orden de carga nativa anterior a Qt y conservación del fallo si una DLL no carga.
- CLI de diagnóstico sin cámara y recolección explícita de DLL en el empaquetado.

Los módulos que necesitan Qt/OpenCV se omiten si faltan sus dependencias; una
ejecución con omisiones no valida esas áreas. El workflow completo instala las
dependencias declaradas antes de ejecutar la suite. No requiere webcam.

CI también importa las dependencias sin empaquetar antes del build y dentro del
exe después. Ambas comprobaciones usan `--no-camera`. Si falla, compartir la
salida JSON y el artefacto `DashEyes-build-diagnostics`, que incluye el inventario
de DLL/PYD y las advertencias de PyInstaller. Un error de `_framework_bindings`
durante importación no demuestra ausencia de webcam ni de OBS.

## Comprobaciones manuales en Windows

### 1. Lectura
- Abrir únicamente con las dependencias del teleprompter.
- Cargar TXT y MD UTF-8, incluida una ruta con espacios y tildes.
- Intentar un archivo inexistente, ilegible o mayor de 2 MB.
- Arrastrar la ventana desde el texto; redimensionar y mostrar/ocultar controles.
- Probar Espacio y flechas después de interactuar con sliders y botones.
- Probar atajos globales desde otra aplicación y con un atajo ya ocupado.
- Cambiar ajustes y cerrar inmediatamente; reabrir y comprobar persistencia.
- Probar pantallas al 100%, 150% y 200%, y cambio de monitor.
- Verificar exclusión real en cada método de compartir pantalla utilizado.

### 2. Cámara y salida
- Probar índices físicos válidos e inválidos y cámara ocupada por otra aplicación.
- Probar cámaras que entreguen 640×480, 1280×720 y resoluciones distintas.
- Probar con y sin OBS Virtual Camera disponible.
- Comprobar la salida desde una aplicación de videollamada.
- Desconectar y reconectar brevemente la webcam. Una desconexión prolongada debe
  cerrar con error y una breve reconexión debe requerir recalibración.
- Verificar que deshabilitar corrección mantiene la cámara, y detener sesión la libera.

### 3. Naturalidad
- Antes de calibrar, la imagen debe permanecer sin corrección.
- Mirar al objetivo durante 25 muestras estables.
- Tras calibrar, mirar al objetivo: no debe aparecer movimiento artificial.
- Leer cerca de la webcam con intensidad 20–40% y comparar original/corregido.
- Probar parpadeos, sonrisa, mirada lateral, cabeza inclinada, gafas y oclusiones.
- Comprobar que no se deforman párpados o piel externa ni se duplican pupilas.
- Cambiar de posición y repetir calibración.

No aprobar una intensidad solo porque el efecto sea más visible. Si empeora la
apariencia, reducirla o desactivar el efecto. La prueba sintética no demuestra
naturalidad con rostros reales.

### 4. Cierre y distribución
- Abrir dos Dashboards de la misma carpeta: no deben iniciar dos instancias.
- Cerrar teleprompter, detener desde Dashboard, minimizar a bandeja y salir.
- Cerrar mientras inicia cámara, mientras se guarda y mientras hay previsualización.
- Simular un motor bloqueado y confirmar la terminación de respaldo de sus hijos.
- Probar el paquete en un equipo sin Python instalado.
- Confirmar que los botones del ejecutable abren el modo correcto y no otro Dashboard.
- Probar carpeta sin permisos: debe aparecer error, no solicitar elevación.

### 5. Rendimiento

Usar al menos un procesador Intel y otro AMD con gráficos integrados y 16 GB.
Registrar modelo de CPU, cámara, resolución, perfil, FPS, coste de procesamiento,
CPU y RAM con previsualización apagada y encendida. Observar al menos cinco minutos
para detectar crecimiento de memoria o retraso acumulado. Estos datos decidirán
los objetivos definitivos; todavía no hay resultados medidos.
