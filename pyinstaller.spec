# -*- mode: python ; coding: utf-8 -*-
# Un único punto de entrada. No ejecutar módulos de soporte como scripts.
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

datas = collect_data_files('mediapipe') + collect_data_files('pyvirtualcam') + [
    ('README.md', '.'),
    ('requirements.txt', '.'),
    ('docs/VALIDATION.md', 'docs'),
    ('docs/THIRD_PARTY.md', 'docs'),
]

a = Analysis(
    ['dashboard.py'],
    pathex=['.'],
    binaries=collect_dynamic_libs('pyvirtualcam'),
    datas=datas,
    hiddenimports=[
        'main', 'diagnostics', 'teleprompter_ui', 'video_worker', 'eye_contact',
        'config_manager', 'protocol', 'runtime', 'settings_writer',
        'mediapipe.python.solutions.face_mesh',
    ],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)

pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='DashEyes',
    debug=False, strip=False, upx=False, console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='DashEyes')
