# Plan y estado de la revisión

## Restricciones respetadas
- Lectura y edición únicamente dentro de `D:\DashEyes`.
- Sin terminal, instalación, ejecución de la app, pruebas ni acceso a hardware.
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
- [ ] Ejecutar la suite con autorización; corregir resultados, no darla por aprobada.
- [ ] Validar los modos con webcam, OBS y videollamada reales.
- [ ] Revisar naturalidad con diferentes personas, gafas e iluminación.
- [ ] Medir CPU, memoria, FPS y latencia en equipos con 16 GB y GPU integrada.
- [ ] Construir y probar el ejecutable en un equipo sin Python.
- [ ] Resolver y fijar dependencias transitivas a partir de un entorno comprobado.
- [ ] Revisar avisos de redistribución y elección de licencia del proyecto.
- [ ] Solicitar permiso para commit/push; no publicar mientras falte validación.

## Decisiones deliberadas

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

- No se promete mirada perfecta, invisibilidad universal ni FPS sin mediciones.
- El efecto queda sin aplicar hasta calibrar. La referencia no se reutiliza entre
  sesiones o tras una pérdida de cámara, para evitar aplicar una calibración ajena.
- El Dashboard prepara los ajustes; los controles en vivo están en el teleprompter.
- Se mantiene FaceMesh compatible; migrar a MediaPipe Tasks no se mezcla con esta
  estabilización. No se añade un modelo pesado ni un servicio externo de IA.
- Se usa `.dasheyes` dentro de la carpeta autorizada, no AppData. Esta revisión es
  portátil: una carpeta protegida no se soluciona solicitando permisos de administrador.
