# Plan y estado de la revisión

## Restricciones respetadas
- Lectura y edición únicamente dentro de `D:\DashEyes`.
- Sin instalaciones, ejecución local de la app, pruebas locales ni acceso a hardware.
- Terminal utilizada exclusivamente para comandos Git autorizados por el usuario.
- Sin commit, push ni publicación. Se solicitará permiso antes de esas operaciones.

## Implementado en código, pendiente de ejecutar

### Estabilidad
- [x] Importaciones diferidas para separar lectura y vídeo.
- [x] Colas acotadas, señal de parada y supervisor con plazos.
- [x] Diagnóstico aislado con tiempo límite; sin instalador ni UAC.
- [x] Captura de cámara propiedad de un solo hilo y reconexión acotada.
- [x] Cierre cooperativo y Job Object de respaldo en Windows.
- [x] Configuración validada y transacciones completas bajo cerrojo.
- [x] Persistencia agrupada fuera del hilo gráfico y errores visibles.
- [x] Datos portátiles locales; archivo anterior conservado.
- [x] Sincronización de ajustes del Dashboard al terminar una sesión.

### Visibilidad y naturalidad
- [x] Calibración frontal por sesión con muestras estables.
- [x] Zona sin corrección y desplazamientos proporcionales al ojo.
- [x] Máscara interior a párpados y suavizado temporal de desplazamientos.
- [x] Rechazo de pose fallida, parpadeos, oclusiones estimadas y grandes desvíos.
- [x] Previsualización original/corregido opcional y estado efectivo.
- [x] Perfiles de CPU, detección reducida y descarte de imágenes repetidas.
- [x] Controles visibles, lectura estrecha, resaltado de párrafo y tamaño persistente.
- [x] Avisos de captura y conflictos de atajos.

### Distribución y calidad
- [x] Un único punto de entrada para PyInstaller.
- [x] Versiones directas propuestas y configuración de CI actualizada.
- [x] Suite de pruebas escrita y lista manual de validación.
- [x] Documentación de límites, privacidad y licencias.
- [ ] Repetir la suite con los últimos cambios; la ejecución remota de `64d5167` superó el paso de pruebas, no el smoke test del exe.
- [ ] Validar los modos con webcam, OBS y videollamada reales.
- [ ] Revisar naturalidad con diferentes personas, gafas e iluminación.
- [ ] Medir CPU, memoria, FPS y latencia en equipos con 16 GB y GPU integrada.
- [ ] Construir y probar el ejecutable en un equipo sin Python.
- [ ] Resolver y fijar dependencias transitivas a partir de un entorno comprobado.
- [ ] Revisar avisos de redistribución y elección de licencia del proyecto.
- [ ] Solicitar permiso para commit/push; no publicar mientras falte validación.

## Decisiones deliberadas

### Workaround respaldado por el proyecto de MediaPipe

Se consultó la API pública de Actions: `64d5167` superó los pasos de pruebas
unitarias y preflight sin empaquetar y falló en el exe. La descarga del ZIP de
diagnósticos devolvió HTTP 401; no se ha leído ese artefacto ni se han solicitado
credenciales al usuario.

La investigación del issue oficial de MediaPipe #5838 encontró reportes del
mismo fallo nativo con PyInstaller y un workaround explícito: volver a 0.10.14.
Se cambió el pin de 0.10.21 a 0.10.14, manteniendo FaceMesh, Python 3.11 x64 y el
resto de dependencias. PyPI confirma un wheel cp311-win_amd64 no retirado.
Referencias:
- https://github.com/google-ai-edge/mediapipe/issues/5838#issuecomment-2831803363
- https://pypi.org/pypi/mediapipe/0.10.14/json

Se mantiene la comparación del ejecutable y se exige además crear/cerrar FaceMesh
con refinamiento de iris, sin cámara, para comprobar modelos. Se añadieron pruebas
del fallo de modelos y del pin de compatibilidad. No se declara resuelto el
problema hasta ejecutar la nueva versión en CI. No se ha hecho push de este ajuste.

### Incidencia de CI pendiente de diagnóstico

La primera ejecución comunicada por el usuario llegó al smoke test del ejecutable
y falló con el mensaje genérico `Packaged dispatch failed`. Ese mensaje no permite
distinguir un error de dependencias empaquetadas, arranque o lectura del ExitCode
por PowerShell. No se da por corregida la causa raíz.

Se preparó captura de stdout/stderr, conservación del handle del proceso,
comprobación explícita del ExitCode y del informe JSON y publicación de los logs
incluso si el paso falla. Ese ajuste se publicó en `dd84381` con autorización;
no se han ejecutado pruebas en el equipo universitario.

El siguiente informe remoto confirmó ExitCode 1 y un fallo al inicializar
`mediapipe.python._framework_bindings`; las otras cuatro dependencias cargaron.
No es un fallo de webcam: ocurrió antes de comprobar dispositivos.

Se preparó la recolección explícita de DLL de MediaPipe y bibliotecas delvewheel,
un runtime hook con rutas internas de carga y un diagnóstico que importa los
componentes de vídeo antes de Qt. CI ahora comprueba los imports sin empaquetar
antes de construir y vuelve a comprobarlos dentro del exe, con `--no-camera`.
También conserva el inventario de DLL/PYD. Se añadieron cuatro pruebas de
regresión (37 en total). Esta corrección sigue pendiente de validación remota;
no se ha identificado la DLL concreta que falló ni se declara resuelto sin CI.

El usuario comunicó que `2ce0270` sigue fallando al inicializar el mismo binding.
La recolección adicional y el cambio de orden respecto a Qt no solucionaron el
problema. No deben presentarse como una corrección confirmada.

Se preparó una comparación controlada, pendiente de push autorizado, con el mismo
código y dependencias: windowed/console y OpenCV-primero/MediaPipe-primero. Se
conservan los cuatro resultados aunque falle el primero, los tracebacks y el
estado de los descriptores estándar de Python/CRT. Esto permite separar problemas
del bootloader sin consola de conflictos de importación, sin afirmar de antemano
cuál es la causa. El exe de consola se construye únicamente si CI lo solicita y
se excluye del artefacto de distribución. La app de producción sigue debiendo
superar su prueba; las variantes no sustituyen esa validación.

- No se promete mirada perfecta, invisibilidad universal ni FPS sin mediciones.
- El efecto queda sin aplicar hasta calibrar. La referencia no se reutiliza entre
  sesiones o tras una pérdida de cámara, para evitar aplicar una calibración ajena.
- El Dashboard prepara los ajustes; los controles en vivo están en el teleprompter.
- Se mantiene FaceMesh compatible; migrar a MediaPipe Tasks no se mezcla con esta
  estabilización. No se añade un modelo pesado ni un servicio externo de IA.
- Se usa `.dasheyes` dentro de la carpeta autorizada, no AppData. Esta revisión es
  portátil: una carpeta protegida no se soluciona solicitando permisos de administrador.
