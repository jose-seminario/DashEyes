"""Rutas de DLL del propio paquete, antes de importar extensiones nativas.

No instala runtimes, no modifica PATH del sistema ni oculta errores de carga.
Los handles deben permanecer vivos mientras se utilicen esas bibliotecas.
"""

import os
import sys


def configure_native_paths():
    root = getattr(sys, "_MEIPASS", None)
    if os.name != "nt" or not root or not hasattr(os, "add_dll_directory"):
        return
    handles = getattr(sys, "_dasheyes_dll_directories", [])
    for relative in ("mediapipe.libs", "mediapipe", os.path.join("mediapipe", "python")):
        directory = os.path.join(root, relative)
        if os.path.isdir(directory):
            handles.append(os.add_dll_directory(directory))
    sys._dasheyes_dll_directories = handles


configure_native_paths()
