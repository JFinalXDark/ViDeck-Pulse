# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys

START_CWD, START_ARGV = os.getcwd(), list(sys.argv)     # con qué carpeta y argumentos arrancó (para reiniciar igual)

from core import paths  # noqa: E402
if __name__ == "__main__":
    paths.enter_data_dir()           # la carpeta de datos (junto al programa, o %APPDATA%\ViDeck Pulse si está instalado) pasa a ser la carpeta de trabajo

if len(sys.argv) >= 4 and sys.argv[1] == "--mpv-check":     # proceso auxiliar: prueba una libmpv nueva sin cargarla en el programa (core/mpv_tools.py)
    from core.mpv_check import main as _mpv_check
    sys.exit(_mpv_check(sys.argv))

if len(sys.argv) >= 3 and sys.argv[1] == "--smoke-test":    # prueba de humo del ejecutable compilado (automatización de GitHub Actions): core/smoke.py
    from core.smoke import run as _smoke
    sys.exit(_smoke(sys.argv[2]))

if len(sys.argv) >= 3 and sys.argv[1] == "--firstrun-selftest":    # primer inicio de extremo a extremo con el ejecutable compilado (GitHub Actions): core/firstrun_selftest.py
    from core.firstrun_selftest import run as _firstrun_selftest
    sys.exit(_firstrun_selftest(sys.argv[2]))

# Windows: permite que python-mpv encuentre libmpv-2.dll / mpv-2.dll junto a main.py
os.environ["PATH"] = paths.components_dir() + os.pathsep + os.environ.get("PATH", "")
# libmpv/ffmpeg propios y «Legacy Mode»: aplica la libmpv preparada y deja el PATH apuntando a la carpeta correcta ANTES de importar mpv
from core import components  # noqa: E402
BOOT_EVENTS = components.bootstrap()
# Menos CPU: Tesseract (OpenMP) y OpenCV usan 1 hilo; los ROIs son diminutos y no compensa paralelizar
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

import cv2  # noqa: E402
cv2.setNumThreads(1)

from PySide6.QtGui import QSurfaceFormat  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core import devlog, winicon  # noqa: E402
from core.config import load_config, save_config  # noqa: E402
from core.db import TracksDB  # noqa: E402
from core.restart import relaunch  # noqa: E402
from gui.icon import app_icon  # noqa: E402
from gui.qtlog import install_qt_hook  # noqa: E402


def load_main_window():
    """Importa la ventana principal (ahí se importa mpv: sin libmpv ni siquiera se puede, por eso esto va DESPUÉS de la ventana de primer inicio).
    Si la libmpv nueva no carga, se vuelve a la anterior y se reinicia."""
    try:
        from gui.main_window import MainWindow
        return MainWindow
    except Exception as e:                                           # noqa: BLE001
        if components.rescue_import(e):
            ok, why = relaunch(START_CWD, START_ARGV)
            sys.exit(0 if ok else 1)
        raise


def first_run(cfg):
    """Primer inicio: si falta libmpv o Tesseract (o ffmpeg la primera vez), la ventana «Componentes necesarios» los instala ANTES de abrir el
    programa. Devuelve False si el usuario salió sin instalar lo imprescindible."""
    from core import firstrun
    if not firstrun.needs_window(cfg):
        return True
    from gui.firstrun_dialog import FirstRunDialog
    dlg = FirstRunDialog(cfg, lambda: save_config(cfg))
    dlg.exec()
    return dlg.outcome == "ready"


def main():
    devlog.install_hooks()           # excepciones no controladas (también de hilos) y avisos de logging -> registro
    install_qt_hook()                # avisos y errores internos de Qt -> registro
    fmt = QSurfaceFormat()
    fmt.setSwapInterval(1)           # sincronía vertical (60 Hz): fundidos suaves en el visor OpenGL
    QSurfaceFormat.setDefaultFormat(fmt)
    winicon.set_taskbar_identity()   # Windows: la barra de tareas muestra el icono de ViDeck Pulse, no el de python.exe
    app = QApplication(sys.argv)     # Qt6 ya hace el proceso DPI-aware (coordenadas en píxeles reales)
    app.setWindowIcon(app_icon())    # icono de todas las ventanas de Qt (principal, reproducción, diálogos)
    cfg = load_config()
    devlog.configure(bool(cfg.get("dev", {}).get("log_enabled")))     # archivo videck_pulse.log si está activado
    devlog.info("inicio", "ViDeck Pulse arrancando")
    if not first_run(cfg):                                            # (la libmpv recién instalada ya quedó activa para el import de abajo)
        devlog.info("inicio", "Primer inicio: el usuario salió sin instalar los componentes")
        devlog.close()
        sys.exit(0)
    MainWindow = load_main_window()
    sec = components.section(cfg)
    st = components.state()
    if st["legacy_failed"]:                                           # Legacy Mode estaba pedido pero no se pudo activar: se desmarca
        sec["legacy"] = False
        save_config(cfg)
    for src, tag in st["skip"].items():                               # una libmpv que no funcionó no se vuelve a ofrecer sola
        sec["mpv" if src == "libmpv" else src]["skip_version"] = tag
        save_config(cfg)
    components.flush_events()                                         # lo ocurrido al preparar libmpv/Legacy, al registro y la barra de estado
    db = TracksDB(cfg["db_path"], roots=lambda: [cfg.get("videos_dir", "")])      # un vínculo cuyo video se movió se repara buscándolo en la carpeta de videos
    win = MainWindow(cfg, db)
    win.show()
    components.confirm_start(getattr(win, "libmpv_failed", False))    # arrancó bien con la libmpv recién puesta: se confirma
    code = app.exec()
    restart = bool(getattr(win, "restart_requested", False) is True)     # lo pide la ventana principal (p. ej. al cambiar el idioma)
    devlog.info("inicio", "ViDeck Pulse cerrado")
    if restart:
        devlog.info("inicio", "Se reinicia ViDeck Pulse")
    devlog.close()
    if restart:                                                          # ya se cerró todo: ahora sí se abre la copia nueva
        ok, why = relaunch(START_CWD, START_ARGV)
        if not ok:
            print(f"No se pudo reiniciar ViDeck Pulse ({why}); ábrelo de nuevo a mano.", file=sys.stderr)
    sys.exit(code)


if __name__ == "__main__":
    main()
