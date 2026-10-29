# -*- mode: python ; coding: utf-8 -*-
# PyInstaller (onedir) para ViDeck Pulse. Se ejecuta desde la raíz del repositorio:  pyinstaller --noconfirm --clean packaging/ViDeckPulse.spec
# PLANTILLA SIN PROBAR (el sandbox de desarrollo es Linux). Pendientes conocidos para el ejecutable congelado:
#   1) base del programa = carpeta del .exe (sys.frozen) en vez de la de main.py (core/paths.py, update 13);
#   2) el proceso auxiliar «--mpv-check» y el reinicio (core/restart.py) deben lanzar ViDeckPulse.exe, no python main.py;
#   3) libmpv-2.dll / ffmpeg / Tesseract NO se incluyen: los instala el gestor de componentes en la carpeta de datos.
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

a = Analysis(
    ['../main.py'],
    pathex=['..'],
    binaries=[],
    datas=[('../assets', 'assets')],
    hiddenimports=collect_submodules('core') + collect_submodules('gui'),
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'pandas'],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='ViDeckPulse',
    console=False,
    icon='../assets/icon.ico',
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name='ViDeckPulse')
