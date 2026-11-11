# Dependencias y licencias — revisión antes de distribuir

El funcionamiento propuesto es local y no requiere suscripciones ni APIs de pago.
Gratis no significa que la redistribución carezca de condiciones.

| Componente | Condiciones a revisar |
|---|---|
| PyQt6 | GPLv3 o licencia comercial. La distribución gratuita debe cumplir la GPL aplicable. |
| Qt y sus plugins | Licencias específicas por módulo; comprobar los binarios incluidos por PyQt6. |
| OpenCV moderno | Apache 2.0 y avisos de componentes incluidos en sus wheels. |
| MediaPipe | Apache 2.0; revisar también los modelos y dependencias incorporados. |
| NumPy | BSD de tres cláusulas y avisos de bibliotecas numéricas incluidas. |
| pyvirtualcam | MIT y avisos de los componentes de cámara virtual. |
| OBS Studio | GPL y condiciones de su propia distribución; no se incluye su instalador. |
| PyInstaller | GPL con excepción de su bootloader; revisar la excepción y componentes incluidos. |

Esta tabla no sustituye los textos oficiales de licencia ni constituye una revisión
legal completa. Antes de publicar binarios: inventariar dependencias transitivas,
incluir sus avisos y licencias y comprobar compatibilidad con la licencia elegida
para DashEyes. No se ha asignado una licencia al proyecto en nombre de su autor.
