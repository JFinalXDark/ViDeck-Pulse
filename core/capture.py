# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Captura de la ventana de Traktor (Windows), limitada a las regiones que se usan: cabecera de cada deck y mixer.

- Funciona aunque haya otras ventanas encima (no si está minimizada).
- Solo se copian los píxeles de los ROIs registrados (~100 KB por fotograma en vez de ~6 MB de la ventana entera).
- PrintWindow (GDI, por defecto): no abre ninguna sesión de captura, así que Traktor se puede transmitir (Discord, OBS) a la vez.
- Windows Graphics Capture (`windows-capture`, opcional) con límite de fps: solo si se elige en la pestaña «Captura».
"""
import os
import sys
import threading
import time

import numpy as np

from . import devlog as dl

IS_WIN = sys.platform == "win32"
MIN_W, MIN_H = 300, 200
SNAPSHOT_EVERY = 10.0       # s: copia completa ocasional (solo para calibrar y para ROIs nuevos)
ROI_TTL = 10.0              # s: un ROI que nadie pide se deja de recortar
DUTY = 4.0                  # la captura de ventana ocupa como mucho 1/4 del tiempo: entre dos capturas se espera ≥ DUTY × lo que tardó
BOOST_MAX_COST = 0.03        # s: la lectura rápida de las revisiones precisas solo se usa si una captura tarda menos que esto (con una máquina lenta no se fuerza)
MAX_PERIOD = 1.5            # s: aunque cada captura tarde mucho, nunca se espacian más que esto
HEAVY_S = 0.09              # s por captura a partir de los cuales se anota en el registro que la lectura se espacia sola
PARTIAL_TOL = 12            # lectura parcial: diferencia típica (0-255) con la copia completa a partir de la cual se descarta


def pull_period(max_fps, backend_fps, cost):
    """Segundos entre dos capturas de un método que lee la ventana por su cuenta (PrintWindow): lo pedido en «Lectura de la
    ventana» (con el tope del método), pero nunca menos de DUTY veces lo que tarda una captura. PrintWindow obliga a la GPU y
    al compositor de Windows a entregar la ventana ENTERA en cada lectura: con Traktor a pantalla completa (1920×1080) puede
    tardar 100 ms o más y, a 5 lecturas por segundo, el visor daría tirones. Así la captura nunca acapara el sistema."""
    base = 1.0 / max(1.0, min(float(max_fps), float(backend_fps)))
    return min(MAX_PERIOD, max(base, DUTY * max(0.0, float(cost or 0.0))))


BACKEND_LABELS = {"printwindow": "PrintWindow", "wgc": "WGC"}


def backend_label(name):
    """Nombre propio del método de captura tal como se muestra («PrintWindow», «WGC»): no es una palabra suelta que capitalizar."""
    return BACKEND_LABELS.get(name, name)


class WindowUnavailable(Exception):
    """La ventana no se puede leer ahora: no existe, está minimizada, captura en negro..."""


WARMUP_MSG = "esperando la primera captura de esta región…"      # arranque normal: el hilo de captura aún no entregó el primer fotograma


# ==================================================================== Windows
if IS_WIN:
    import ctypes
    from ctypes import wintypes

    user32, gdi32, kernel32 = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.kernel32
    for _fn, _args, _res in (
        (user32.IsWindow, [wintypes.HWND], wintypes.BOOL),
        (user32.IsWindowVisible, [wintypes.HWND], wintypes.BOOL),
        (user32.IsIconic, [wintypes.HWND], wintypes.BOOL),
        (user32.GetWindowTextLengthW, [wintypes.HWND], ctypes.c_int),
        (user32.GetWindowTextW, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        (user32.GetWindowRect, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        (user32.GetWindowDC, [wintypes.HWND], wintypes.HDC),
        (user32.ReleaseDC, [wintypes.HWND, wintypes.HDC], ctypes.c_int),
        (user32.PrintWindow, [wintypes.HWND, wintypes.HDC, wintypes.UINT], wintypes.BOOL),
        (user32.GetWindowThreadProcessId, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        (gdi32.CreateCompatibleDC, [wintypes.HDC], wintypes.HDC),
        (gdi32.CreateCompatibleBitmap, [wintypes.HDC, ctypes.c_int, ctypes.c_int], wintypes.HBITMAP),
        (gdi32.CreateDIBSection, [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                  wintypes.HANDLE, wintypes.DWORD], wintypes.HBITMAP),
        (gdi32.GdiFlush, [], wintypes.BOOL),
        (gdi32.SelectObject, [wintypes.HDC, wintypes.HGDIOBJ], wintypes.HGDIOBJ),
        (gdi32.GetDIBits, [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                           ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT], ctypes.c_int),
        (gdi32.DeleteObject, [wintypes.HGDIOBJ], wintypes.BOOL),
        (gdi32.DeleteDC, [wintypes.HDC], wintypes.BOOL),
        (kernel32.OpenProcess, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        (kernel32.QueryFullProcessImageNameW,
         [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        (kernel32.CloseHandle, [wintypes.HANDLE], wintypes.BOOL),
    ):
        _fn.argtypes, _fn.restype = _args, _res
    _ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [_ENUMPROC, wintypes.LPARAM]

    class _BMIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    def _win_exe(hwnd):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(0x1000, False, pid.value)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf, n = ctypes.create_unicode_buffer(520), wintypes.DWORD(520)
            return os.path.basename(buf.value) if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else ""
        finally:
            kernel32.CloseHandle(h)

    def _enum_windows():
        out = []

        @_ENUMPROC
        def cb(hwnd, _):
            if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                rc = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rc))
                out.append({"id": hwnd, "title": buf.value, "owner": _win_exe(hwnd),
                            "w": rc.right - rc.left, "h": rc.bottom - rc.top, "minimized": bool(user32.IsIconic(hwnd))})
            return True

        user32.EnumWindows(cb, 0)
        return out

    def window_state(info):
        """Comprobación barata (sin enumerar ventanas): "ok" | "minimized" | "gone"."""
        hwnd = info["id"]
        if not user32.IsWindow(hwnd):
            return "gone"
        return "minimized" if user32.IsIconic(hwnd) else "ok"

    _BLACK = ("PrintWindow devolvió negro (la ventana de Traktor no se está pintando): comprueba que no esté minimizada; "
              "si sigue igual, prueba el método WGC en la pestaña «Captura»")

    class PrintWindowBackend:
        """GDI PrintWindow(PW_RENDERFULLCONTENT): lee ventanas tapadas, pero pinta la ventana ENTERA en cada captura
        (más CPU, 5 fps como mucho). Es el método por defecto porque no abre una sesión de captura de la ventana.

        Para tardar lo menos posible:
        - la imagen se pinta directamente en una memoria reutilizada (CreateDIBSection): no se reserva, rellena ni copia un bloque
          de ~8 MB nuevo en cada captura, y no hace falta GetDIBits;
        - `capture(rows)` pinta solo las primeras `rows` filas (hasta donde llegan los ROIs: lo de abajo es el explorador de
          música, que no se lee); el resto lo recorta el propio bitmap.
        Si algo de eso falla o no entrega imagen, se vuelve a la lectura clásica de siempre (bitmap nuevo + GetDIBits)."""
        name, pull, max_fps = "printwindow", True, 5
        partial = True                      # entiende `capture(rows)`; el grabador lo apaga si la lectura parcial no cuadra

        def __init__(self, hwnd):
            self.hwnd, self.error = hwnd, None
            self.size = (0, 0)              # tamaño real de la ventana (la imagen puede traer solo las primeras filas)
            self._fast, self._res = True, {}      # camino rápido (DIB reutilizado) · memoria reutilizada por tamaño
            self.fast_note = ""             # por qué se dejó el camino rápido (diagnóstico)

        # ---- memoria reutilizada (camino rápido)
        def _alloc(self, w, h):
            dc = gdi32.CreateCompatibleDC(None)
            if not dc:
                raise OSError("CreateCompatibleDC falló")
            bi = _BMIH(ctypes.sizeof(_BMIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)        # 32 bits, de arriba hacia abajo
            bits = ctypes.c_void_p()
            bmp = gdi32.CreateDIBSection(dc, ctypes.byref(bi), 0, ctypes.byref(bits), None, 0)
            if not bmp or not bits.value:
                gdi32.DeleteDC(dc)
                raise OSError("CreateDIBSection falló")
            old = gdi32.SelectObject(dc, bmp)
            buf = (ctypes.c_ubyte * (w * h * 4)).from_address(bits.value)           # la memoria es del bitmap: no se libera aparte
            return {"dc": dc, "bmp": bmp, "old": old, "buf": buf, "view": np.frombuffer(buf, np.uint8).reshape(h, w, 4)}

        @staticmethod
        def _release(r):
            r["view"] = r["buf"] = None
            gdi32.SelectObject(r["dc"], r["old"])
            gdi32.DeleteObject(r["bmp"])
            gdi32.DeleteDC(r["dc"])

        def _resources(self, w, h):
            r = self._res.get((w, h))
            if r is None:
                for k in [k for k in self._res if k[0] != w]:                   # la ventana cambió de ancho: lo anterior ya no sirve
                    self._release(self._res.pop(k))
                while len(self._res) >= 2:                                      # como mucho dos (la parcial de siempre y la completa de cada 10 s)
                    self._release(self._res.pop(next(iter(self._res))))
                r = self._res[(w, h)] = self._alloc(w, h)
            return r

        def _grab_fast(self, w, h):
            r = self._resources(w, h)
            ok = user32.PrintWindow(self.hwnd, r["dc"], 2)                    # PW_RENDERFULLCONTENT
            gdi32.GdiFlush()                                                  # el dibujo termina antes de leer la memoria del bitmap
            return r["view"], bool(ok)

        # ---- lectura clásica (la de siempre): bitmap nuevo + GetDIBits
        def _grab_classic(self, w, h):
            hwnd = self.hwnd
            hdc = user32.GetWindowDC(hwnd)
            mdc = gdi32.CreateCompatibleDC(hdc)
            bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
            old = gdi32.SelectObject(mdc, bmp)
            buf = (ctypes.c_ubyte * (w * h * 4))()
            try:
                ok = user32.PrintWindow(hwnd, mdc, 2)                # PW_RENDERFULLCONTENT
                bi = _BMIH(ctypes.sizeof(_BMIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
                gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
            finally:
                gdi32.SelectObject(mdc, old)
                gdi32.DeleteObject(bmp)
                gdi32.DeleteDC(mdc)
                user32.ReleaseDC(hwnd, hdc)
            return np.frombuffer(buf, np.uint8).reshape(h, w, 4), bool(ok)

        @staticmethod
        def _dark(img, ok):
            return not ok or img[::8, ::8, :3].mean() < 1.0           # (basta una muestra: la imagen entera sería ~6 M de valores por captura)

        def capture(self, rows=None):
            """Imagen BGRA de la ventana: entera, o solo las primeras `rows` filas. La imagen del camino rápido es memoria
            reutilizada: hay que copiar lo que se quiera conservar antes de la siguiente captura (el grabador copia los ROIs)."""
            rc = wintypes.RECT()
            user32.GetWindowRect(self.hwnd, ctypes.byref(rc))
            w, h = rc.right - rc.left, rc.bottom - rc.top
            if w <= 0 or h <= 0:
                raise WindowUnavailable("ventana sin tamaño")
            self.size = (w, h)
            hh = h if not rows else max(1, min(h, int(rows)))
            if self._fast:
                try:
                    img, ok = self._grab_fast(w, hh)
                except Exception as e:                                   # no se pudo crear o usar la memoria reutilizada
                    self._drop_fast(f"{type(e).__name__}: {e}")
                else:
                    if not self._dark(img, ok):
                        return img
                    img2, ok2 = self._grab_classic(w, hh)                # ¿sin imagen? se prueba la lectura clásica…
                    if self._dark(img2, ok2):
                        raise WindowUnavailable(_BLACK)                   # …y si tampoco, de verdad no hay imagen
                    self._drop_fast("el camino rápido no entregó imagen y el clásico sí")
                    return img2
            img, ok = self._grab_classic(w, hh)
            if self._dark(img, ok):
                raise WindowUnavailable(_BLACK)
            return img

        def _drop_fast(self, why):
            """Deja el camino rápido (se queda con la lectura clásica) y libera su memoria."""
            self._fast, self.fast_note = False, why
            self._free()
            dl.info("captura", f"PrintWindow: se usa la lectura clásica ({why}).")

        def _free(self):
            for k in list(self._res):
                try:
                    self._release(self._res.pop(k))
                except Exception:
                    pass

        def close(self):
            self._free()

    class WgcBackend:
        """Windows Graphics Capture: la ventana llega desde la GPU aunque esté tapada. Solo emite fotogramas cuando
        la imagen cambia; el grabador limita los fps y recorta únicamente los ROIs."""
        name, pull = "wgc", False

        def __init__(self, title, grabber):
            from windows_capture import WindowsCapture            # ImportError -> se usa PrintWindow
            self.error, self._stop = None, False
            cap = WindowsCapture(window_name=title)

            @cap.event
            def on_frame_arrived(frame, control):
                if self._stop:
                    control.stop()
                    return
                now = time.time()
                if grabber.want_frame(now):                       # si no toca, ni siquiera se lee el búfer
                    grabber.ingest(frame.frame_buffer, now)

            @cap.event
            def on_closed():
                self.error = "la ventana se cerró"

            def _run():
                try:
                    cap.start()
                except Exception as e:
                    dl.exception("captura", "WGC: no se pudo iniciar la captura")
                    self.error = str(e)

            threading.Thread(target=_run, daemon=True).start()

        def close(self):
            self._stop = True

else:
    def _enum_windows():
        raise WindowUnavailable("la captura de ventana solo está soportada en Windows por ahora")

    def window_state(info):
        return "gone"


# ===================================================================== común
def list_windows():
    """Ventanas candidatas (grandes o minimizadas), de mayor a menor área."""
    ws = [w for w in _enum_windows() if w["minimized"] or (w["w"] >= MIN_W and w["h"] >= MIN_H)]
    return sorted(ws, key=lambda w: w["w"] * w["h"], reverse=True)


def window_matches(match, w):
    """`match` = texto suelto (proceso o título) o "proceso::título" (lo que guarda el selector de la GUI)."""
    m = (match or "").lower()
    if "::" in m:
        o, t = m.split("::", 1)
        return o in w["owner"].lower() and t in w["title"].lower()
    return m in f"{w['owner']} {w['title']}".lower()


def find_window(match):
    """La ventana más grande y visible que coincide con `match` (los procesos con ese nombre van antes que los
    títulos: una pestaña de Chrome con «Traktor» no gana a Traktor.exe); si solo hay minimizadas, devuelve una."""
    cands = [w for w in list_windows() if window_matches(match, w)]
    if "::" not in (match or ""):
        cands = [w for w in cands if (match or "").lower() in w["owner"].lower()] or cands
    visible = [w for w in cands if not w["minimized"]]
    return visible[0] if visible else (cands[0] if cands else None)


def _crop(frame, key):
    l, t, w, h = key
    l, t = max(0, l), max(0, t)
    return np.ascontiguousarray(frame[t:t + h, l:l + w, :3])         # BGR; solo se copian los píxeles del ROI


class WindowGrabber:
    """Misma interfaz que ScreenGrabber (register / grab / full / status / close), pero sobre UNA ventana
    y recortando solo los ROIs registrados."""

    def __init__(self, cap_cfg, finder=None, opener=None, state_fn=None):
        self.cfg = cap_cfg
        self._find = finder or find_window
        self._open = opener or self._open_backend
        self._state = state_fn or window_state
        self._lock = threading.Lock()
        self._rois, self._crops = {}, {}
        self._crop_t = {}                                                # hora (time.time()) del fotograma de cada recorte
        self._snapshot, self._snap_t, self._want_full = None, 0.0, False
        self._full_evt = threading.Event()
        self._err, self._stop, self._last_accept = None, False, 0.0      # _err: solo errores reales (sin "iniciando")
        self.notes = ""                                                  # por qué se descartó un método de captura
        self.stage = "iniciando"                                         # en qué paso está el hilo de captura (diagnóstico)
        self.backend_name, self.title, self.size = "—", "", None
        self.fps, self._fps_n, self._fps_t = 0.0, 0, time.time()
        self.cost, self._cost_n, self._heavy = None, 0, False             # lo que tarda una captura (PrintWindow), media móvil
        self._part_ok, self._part_bad = False, 0                          # lectura parcial: ¿ya se comprobó que coincide con la ventana entera?
        self.period, self.base_period = 0.0, 0.0                          # espera real entre capturas / la que se pidió
        self.boost = False                                                # el motor está midiendo con precisión (cambio de dígitos del reloj): se lee más seguido
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    # --------------------------------------------- entrada de fotogramas
    def want_frame(self, now):
        """Límite de fps: devuelve False si todavía no toca (así el backend ni lee el fotograma)."""
        period = 1.0 / max(1.0, float(self.cfg.get("max_fps", 15)))
        if self._want_full or self._snapshot is None or now - self._last_accept >= period:
            self._last_accept = now
            return True
        return False

    def ingest(self, frame, now, full=None, size=None):
        """Recibe un fotograma. `size` = tamaño real de la ventana si la imagen trae solo sus primeras filas (lectura parcial);
        `full` = si esta lectura debe servir de copia completa (si no se da, se decide aquí con los tiempos)."""
        h, w = frame.shape[:2]
        sw, sh = size or (w, h)
        partial = h < sh                                       # imagen parcial: nunca sirve de copia completa de la ventana
        with self._lock:
            self._rois = {k: t for k, t in self._rois.items() if now - t < ROI_TTL}
            keys = list(self._rois)
            need_snap = (self._want_full or self._snapshot is None or now - self._snap_t > SNAPSHOT_EVERY) if full is None else bool(full)
            old = self._crops
        need_snap = need_snap and not partial
        crops, stamps, ft = {}, {}, self.frame_stamp(now)
        for k in keys:
            if partial and max(0, k[1]) + k[3] > h:            # un ROI que quedó fuera de las filas leídas (se pidió mientras tanto):
                if k in old:                                   # se conserva el recorte anterior y se lee completo en la próxima vuelta
                    crops[k], stamps[k] = old[k], self._crop_t.get(k, ft)
                continue
            crops[k], stamps[k] = _crop(frame, k), ft
        snap = np.ascontiguousarray(frame[:, :, :3]) if need_snap else None
        with self._lock:
            self._crops, self._crop_t, self.size = crops, stamps, (sw, sh)
            if snap is not None:
                self._snapshot, self._snap_t, self._want_full = snap, now, False
                self._full_evt.set()
            self._fps_n += 1
            if now - self._fps_t >= 1.0:
                self.fps, self._fps_n, self._fps_t = self._fps_n / (now - self._fps_t), 0, now

    def frame_stamp(self, now):
        """Hora del contenido de un fotograma que terminó de capturarse en `now`: PrintWindow hace pintar la ventana durante la
        llamada, así que la imagen es de la mitad de lo que tardó (WGC entrega el fotograma al instante: cost = None → `now`)."""
        return now - 0.5 * (self.cost or 0.0)

    def _plan(self, now):
        """(filas, completa) de la próxima lectura: cuántas filas desde arriba hacen falta (None = la ventana entera) y si
        esta lectura debe servir de copia completa (calibrar, ROIs nuevos, y una vez cada SNAPSHOT_EVERY s). El resto del
        tiempo solo se lee hasta donde llegan los ROIs: lo de abajo en Traktor es el explorador, que no se mira."""
        with self._lock:
            full = self._want_full or self._snapshot is None or now - self._snap_t > SNAPSHOT_EVERY
            keys = [k for k, t in self._rois.items() if now - t < ROI_TTL]
        if full or not keys:
            return None, full
        return max(max(0, k[1]) + k[3] for k in keys), False

    def _check_partial(self, backend, frame, size):
        """La primera vez que se lee solo una parte de la ventana, se compara con la copia completa: si lo de arriba no coincide
        (el método de captura no recortó como se esperaba), se vuelve a leer la ventana entera. Basta la diferencia mediana
        (lo que cambia entre dos lecturas —vúmetros, ondas— es una parte pequeña de la imagen)."""
        with self._lock:
            snap = self._snapshot
        h = frame.shape[0]
        if snap is None or snap.shape[1] != frame.shape[1] or snap.shape[0] < h or h >= size[1]:
            return
        a = frame[::6, ::6, :3].astype(np.int16)
        b = snap[:h:6, ::6, :3].astype(np.int16)
        diff = float(np.median(np.abs(a - b).mean(axis=2)))
        if diff <= PARTIAL_TOL:
            self._part_ok = True
            return
        self._part_bad += 1
        if self._part_bad >= 2:
            backend.partial, self._part_ok = False, True
            dl.info("captura", f"La lectura parcial de la ventana no coincide con la copia completa (diferencia {diff:.0f}): "
                               f"se lee la ventana entera.")

    # ------------------------------------------------------- hilo de control
    def _loop(self):
        backend = info = None
        last_find, msg = 0.0, "iniciando…"
        while not self._stop:
            t0 = time.time()
            period = 0.2
            try:
                if info is not None:
                    st = self._state(info)
                    if st == "gone":
                        info = None
                        if backend:
                            backend.close()
                        backend = None
                    elif st == "minimized":
                        raise WindowUnavailable("la ventana de Traktor está minimizada")
                if info is None:
                    if t0 - last_find >= 1.0:                    # buscar la ventana cuesta: como mucho 1 vez por s
                        last_find = t0
                        self.stage = "buscando la ventana de Traktor"
                        info = self._find(self.cfg["window_title"])
                        msg = f"no se encontró ninguna ventana «{self.cfg['window_title']}»"
                    if info is None:
                        raise WindowUnavailable(msg)
                if info["minimized"] and self._state(info) == "minimized":
                    raise WindowUnavailable("la ventana de Traktor está minimizada")
                if backend is None:
                    backend = self._open(info)
                    self.backend_name, self.title = backend.name, f"{info['owner']} — {info['title']}"
                    self.cost, self._cost_n, self._heavy, self.period, self.base_period = None, 0, False, 0.0, 0.0
                    self._part_ok, self._part_bad = False, 0
                self.stage = f"capturando ({backend.name})"
                if backend.pull:
                    rows = full = None
                    if getattr(backend, "partial", False):           # PrintWindow: solo se lee hasta donde llegan los ROIs
                        rows, full = self._plan(time.time())
                    c0 = time.time()
                    frame = backend.capture(rows) if getattr(backend, "partial", False) else backend.capture()
                    c1 = time.time()
                    period = self._pull_wait(backend, c1 - c0)
                    size = getattr(backend, "size", None) if getattr(backend, "partial", False) else None
                    self.ingest(frame, c1, full=full, size=size)
                    if size and frame.shape[0] < size[1] and not self._part_ok and getattr(backend, "partial", False):
                        self._check_partial(backend, frame, size)
                elif backend.error:
                    raise RuntimeError(backend.error)
                with self._lock:
                    had_err = bool(self._err) and self._snapshot is not None
                    if self._snapshot is not None:
                        self._err = None
                if had_err:
                    dl.devlog.resolve("captura")              # la captura entrega imagen otra vez: el error anterior quedó solucionado
            except WindowUnavailable as e:
                with self._lock:
                    self._err = str(e)
            except Exception as e:                               # fallo inesperado: se reabre todo
                dl.exception("captura", "fallo inesperado: se reabre la captura")
                with self._lock:
                    self._err = f"error de captura: {e}"
                if backend:
                    backend.close()
                backend = info = None
            time.sleep(max(0.0, period - (time.time() - t0)))
        if backend:
            backend.close()

    def _pull_wait(self, backend, took):
        """Anota lo que tardó una captura y devuelve la espera hasta la siguiente (ver `pull_period`)."""
        self.cost = took if self.cost is None else 0.7 * self.cost + 0.3 * took
        self._cost_n += 1
        mf = float(self.cfg.get("max_fps", 15))
        bf = float(backend.max_fps)
        boost = float(self.cfg.get("boost_fps", 15) or 0)
        if self.boost and boost > bf and (self.cost is None or self.cost <= BOOST_MAX_COST):
            bf, mf = boost, max(mf, boost)               # durante una revisión precisa PrintWindow lee más seguido: el instante del cambio de dígito se sabe mejor
        self.base_period = pull_period(mf, bf, 0.0)
        self.period = pull_period(mf, bf, self.cost)
        if self._cost_n >= 5 and self.cost > HEAVY_S and not self._heavy:
            self._heavy = True
            dl.info("captura", f"{backend_label(backend.name)} tarda ~{self.cost * 1000:.0f} ms por captura (Traktor a pantalla completa pesa más): "
                               f"la lectura se espacia sola a {1 / self.period:.1f} fps para no frenar el visor. Si el visor va a "
                               f"tirones, baja «Lectura de la ventana» o prueba WGC en la pestaña «Captura».")
        elif self._heavy and self.cost < HEAVY_S * 0.6:
            self._heavy = False
        return self.period

    def _open_backend(self, info):
        pref = self.cfg.get("backend", "printwindow")
        if not IS_WIN:
            raise WindowUnavailable("la captura de ventana solo está soportada en Windows por ahora")
        last = None
        for name in (["wgc", "printwindow"] if pref == "auto" else [pref]):
            try:
                if name == "wgc":
                    self.stage = "abriendo WGC (hasta 7 s)"
                    b = self._start_wgc(info)
                    t = time.time()
                    while self._snapshot is None and not b.error and time.time() - t < 3.0:
                        time.sleep(0.05)
                    if self._snapshot is None:
                        b.close()
                        raise WindowUnavailable(f"WGC no entregó fotogramas ({b.error or 'timeout'})")
                    self.notes = ""
                    return b
                self.stage = "abriendo PrintWindow"
                return PrintWindowBackend(info["id"])
            except Exception as e:
                last = e
                dl.warn("captura", f"{name} no disponible: {type(e).__name__}: {e}")
                self.notes = f"{name} no disponible: {type(e).__name__}: {e}"[:90]
        raise WindowUnavailable(f"ningún método de captura funcionó ({last})")

    def _start_wgc(self, info, timeout=4.0):
        """Crea el backend WGC en un hilo aparte: si el constructor se cuelga, se abandona y se pasa a PrintWindow."""
        box = {}

        def make():
            try:
                box["b"] = WgcBackend(info["title"], self)
            except Exception as e:
                box["e"] = e

        th = threading.Thread(target=make, daemon=True)
        th.start()
        th.join(timeout)
        if "e" in box:
            raise box["e"]
        if "b" not in box:
            raise WindowUnavailable(f"WGC no respondió en {timeout:.0f} s")
        return box["b"]

    # ---------------------------------------------------------------- API
    def register(self, rois):
        """Declara los ROIs que se van a leer. Los nuevos se recortan al instante de la última copia completa."""
        keys, now = [tuple(int(v) for v in r) for r in rois], time.time()
        with self._lock:
            new = [k for k in keys if k not in self._crops]
            for k in keys:
                self._rois[k] = now
            if new and self._snapshot is not None:
                crops, stamps = dict(self._crops), dict(self._crop_t)
                for k in new:
                    crops[k], stamps[k] = _crop(self._snapshot, k), self.frame_stamp(self._snap_t)   # (de la copia completa: puede ser vieja)
                self._crops, self._crop_t = crops, stamps

    def prime(self, rois, timeout=2.0):
        self.register(rois)
        self.wait_ready(timeout)

    def wait_ready(self, timeout=10.0):
        t = time.time()
        while time.time() - t < timeout:
            with self._lock:
                if self._snapshot is not None or self._err:
                    return
            time.sleep(0.05)

    def grab_t(self, roi, wait=0.0):
        """El recorte de `roi` y la hora (time.time()) del fotograma del que sale: el motor mide con ella cuándo cambia un
        dígito del reloj de Traktor, sin depender de cuándo le toca leer."""
        key, deadline = tuple(int(v) for v in roi), time.time() + wait
        while True:
            with self._lock:
                if self._err:
                    raise WindowUnavailable(self._err)
                self._rois[key] = time.time()
                c, t = self._crops.get(key), self._crop_t.get(key)
            if c is not None:
                if c.size == 0:
                    raise WindowUnavailable("el ROI queda fuera de la ventana: recalibra")
                return c, t
            if time.time() >= deadline:
                raise WindowUnavailable(WARMUP_MSG)
            time.sleep(0.02)

    def grab(self, roi, wait=0.0):
        return self.grab_t(roi, wait)[0]

    def full(self, timeout=1.0):
        """Ventana completa (solo para calibrar): el próximo fotograma, o la última copia si la UI está quieta."""
        self._full_evt.clear()
        self._want_full = True
        self._full_evt.wait(timeout)
        t = time.time()
        while time.time() - t < 8.0:                  # al abrir, la captura puede tardar en entregar el 1.er fotograma
            with self._lock:
                err = self._err or ""
                if self._snapshot is not None or err.startswith("no se encontró") or "minimizada" in err:
                    break                             # (otros errores son pasajeros mientras el método de captura arranca)
            time.sleep(0.05)
        with self._lock:
            if self._snapshot is None:
                raise WindowUnavailable(self._err or f"la captura de la ventana no entregó imagen (paso: {self.stage}"
                                        + (f"; {self.notes}" if self.notes else "") + ")")
            if self._err:
                raise WindowUnavailable(self._err)
            return self._snapshot.copy()

    def status(self):
        with self._lock:
            if self._err:
                return f"⚠ {self._err}"
            if self._snapshot is None:
                return f"⚠ iniciando la captura de la ventana… ({self.stage}" + (f"; {self.notes}" if self.notes else "") + ")"
            cost = ""
            if self.cost is not None and self.period:                      # método que lee la ventana por su cuenta (PrintWindow)
                cost = f" · {self.cost * 1000:.0f} ms por captura"
                if self.period > self.base_period + 0.005:
                    cost += f" (espaciada a {1 / self.period:.1f} fps para no frenar el visor)"
            return (f"{self.title} · {self.size[0]}×{self.size[1]} · {backend_label(self.backend_name)} · {self.fps:.0f} fps" + cost
                    + (f" ({self.notes})" if self.notes else ""))

    def close(self):
        self._stop = True


def make_grabber(cfg):
    if cfg["capture"]["mode"] == "window":
        return WindowGrabber(cfg["capture"])
    from .vision import ScreenGrabber
    return ScreenGrabber(cfg["monitor"])
