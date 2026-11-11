# -*- mode: python ; coding: utf-8 -*-
# Un único punto de entrada. No ejecutar módulos de soporte como scripts.
from PyInstaller.utils.hooks import (
    collect_data_files, collect_dynamic_libs, collect_delvewheel_libs_directory,
)

datas = collect_data_files('mediapipe') + collect_data_files('pyvirtualcam') + [
    ('README.md', '.'),
    ('requirements.txt', '.'),
    ('docs/VALIDATION.md', 'docs'),
    ('docs/THIRD_PARTY.md', 'docs'),
]

# collect_data_files no sustituye la recolección de DLL. Conservar su disposición
# original y las bibliotecas hermanas añadidas por delvewheel en wheels Windows.
binaries = collect_dynamic_libs('mediapipe') + collect_dynamic_libs('pyvirtualcam')
datas, binaries = collect_delvewheel_libs_directory('mediapipe', datas=datas, binaries=binaries)

a = Analysis(
    ['dashboard.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        'main', 'diagnostics', 'teleprompter_ui', 'video_worker', 'eye_contact',
        'config_manager', 'protocol', 'runtime', 'settings_writer',
        'mediapipe.python.solutions.face_mesh',
        'mediapipe.python._framework_bindings',
    ],
    hookspath=[], hooksconfig={}, runtime_hooks=['pyi_rth_native_paths.py'],
    excludes=[], noarchive=False,
)

pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='DashEyes',
    debug=False, strip=False, upx=False, console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='DashEyes')
