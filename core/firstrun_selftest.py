# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prueba de extremo a extremo del primer inicio, para GitHub Actions (equipo Windows limpio): `ViDeckPulse.exe --firstrun-selftest <informe.txt>`.

Hace lo mismo que la ventana «Componentes necesarios» al pulsar «Instalar lo marcado», pero sin ventana: instala de verdad los componentes que
falten (libmpv, Tesseract, ffmpeg) con `core.firstrun.Installer` desde el EJECUTABLE COMPILADO y comprueba que después el programa podría
arrancar: `import mpv` funciona, mpv se inicia, Tesseract y ffmpeg responden. Los datos van a la carpeta de VIDECK_PULSE_DATA (si está puesta).
Escribe un informe de texto (el ejecutable no tiene consola) y devuelve 0 solo si todo salió bien. Textos en inglés: es una herramienta de CI.
"""
import sys
import time

WAIT_S = 18 * 60


def run(out_path):
    lines, bad = [], 0

    def log(text, ok=True):
        nonlocal bad
        lines.append(("OK   " if ok else "FAIL ") + text)
        bad += 0 if ok else 1

    try:
        from core import components as comp
        from core import ffmpeg_tools as ft
        from core import firstrun as fr
        from core import paths
        from core import tesseract_tools as tt
        log(f"frozen={paths.frozen()} components_dir={paths.components_dir()}")
        cfg = {}
        before = fr.rows(cfg)
        log("before: " + ", ".join(f"{r['key']}={'ok' if r['ok'] else 'missing'}" for r in before))
        log("first-run window needed before installing: " + str(fr.needs_window(cfg, before)))
        keys = [r["key"] for r in before if not r["ok"]]
        inst = fr.Installer(keys, cfg).start()
        t0, last = time.time(), ""
        while not inst.finished() and time.time() - t0 < WAIT_S:
            snap = inst.snapshot()
            now = " | ".join(f"{k}:{v['state']}:{v['phase']}" for k, v in snap.items())
            if now != last:
                lines.append(f"{time.time() - t0:6.0f}s {now}")
                last = now
            time.sleep(1)
        if not inst.finished():
            inst.cancel()
            log(f"installation did not finish in {WAIT_S} s", False)
        for k, v in inst.snapshot().items():
            ok = v["state"] == "ok"
            log(f"install {k}: {v['state']}" + ("" if ok else f" ({v['error']})") + (f" info={ {x: v['info'].get(x) for x in ('version', 'mpv', 'path')} }" if ok else ""), ok)
        after = fr.rows(cfg)
        log("after: " + ", ".join(f"{r['key']}={r['version'] or 'ok'}" if r["ok"] else f"{r['key']}=MISSING" for r in after), all(r["ok"] for r in after))
        log("first-run window still needed after installing: " + str(fr.needs_window(cfg, after)), not fr.needs_window(cfg, after))
        # the program must be able to start: import mpv and create a player (this is what gui.main_window does first)
        try:
            import mpv
            m = mpv.MPV(vo="null", ao="null", idle=True, input_default_bindings=False, input_vo_keyboard=False)
            try:
                ver = str(m.mpv_version)
            finally:
                m.terminate()
            log(f"import mpv + MPV(): {ver}", ver.lower().startswith("mpv"))
            path, origin = comp.find_libmpv()
            log(f"libmpv in use: {path} ({origin})", bool(path))
        except BaseException as e:                                   # noqa: BLE001
            log(f"import mpv failed: {type(e).__name__}: {e}", False)
        found = tt.installed()
        log(f"tesseract: {found}", bool(found))
        tools = ft.resolve("")
        log(f"ffmpeg: {tools.ffmpeg} {tools.version}", bool(tools.ffmpeg))
        try:
            from core import vision                                    # the OCR module must find the same tesseract
            ok, msg = vision.tesseract_status("")
            log(f"vision.tesseract_status: {msg}", ok)
        except BaseException as e:                                   # noqa: BLE001
            log(f"vision import failed: {type(e).__name__}: {e}", False)
    except BaseException as e:                                       # noqa: BLE001
        import traceback
        log("self-test crashed: " + traceback.format_exc()[-1500:], False)
    lines.append("RESULT: " + ("PASS" if not bad else f"FAIL ({bad})"))
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        print("\n".join(lines), file=sys.stderr)
    return 0 if not bad else 1
