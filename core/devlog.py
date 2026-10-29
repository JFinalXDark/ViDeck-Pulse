# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Registro (log) de ViDeck Pulse: solo errores, con todo el detalle del momento del error.

Idea: si todo funciona, el registro queda VACÍO. Los eventos normales (cargas de video, motor iniciado, layout…) no se
muestran: se guardan en una memoria corta de «contexto». Cuando ocurre un ERROR, el registro:
  1. vuelca el contexto de los segundos anteriores (qué estaba pasando justo antes),
  2. escribe el error con todo su detalle (traceback incluido),
  3. y durante unos segundos más deja pasar también los eventos normales (qué pasó después).
Los avisos (WARN) significativos (de mpv, Qt, logging…) también se registran, pero sin abrir ese episodio. Con el registro
activado (pestaña «Desarrollador») lo registrado se muestra y se guarda en `videck_pulse.log`.

* Recoge las excepciones no controladas (también de hilos), los avisos de `logging`, de Qt y de libmpv.
* `route()` limpia los textos del panel: cada aviso «⚠ …» queda como un único símbolo ⚠ y su detalle va al registro.
* Incidencias abiertas (`incidents()`): lo que muestra la barra de estado. Un ERROR es una incidencia roja y un WARN una
  alerta amarilla. Siguen abiertas HASTA QUE EL PROBLEMA SE SOLUCIONA (ni revisar ni limpiar el registro las cierra):
  las que vienen del panel (`route()`/`condition()`) se cierran solas cuando el aviso desaparece del panel; las demás
  (excepciones, errores de librerías…) las cierra `resolve(fuente)` cuando esa parte del programa vuelve a funcionar
  (p. ej. el siguiente video carga bien) y, si no hay forma de comprobarlo, duran hasta reiniciar el programa.

No depende de Qt: lo usan el motor, el reproductor y la interfaz.
"""
import logging
import os
import platform
import re
import sys
import threading
import time
import traceback
from collections import deque, namedtuple

LOG_FILE = "videck_pulse.log"
MAXLEN = 3000                     # líneas visibles en memoria
MAX_MSG = 6000                    # caracteres por línea (un traceback largo cabe)
MAX_FILE = 1_500_000              # tamaño del archivo antes de rotar a «.1»
DUP_WINDOW = 120.0                # el mismo mensaje no se repite antes de estos segundos (se cuenta cuántas veces se calló)
FLOOD_N, FLOOD_WIN = 40, 10.0     # una fuente no puede dar más de FLOOD_N líneas cada FLOOD_WIN segundos
CTX_RING = 150                    # eventos normales recordados (no visibles) para dar contexto a un error
CTX_SECONDS, CTX_MAX = 20.0, 40   # al ocurrir un error se vuelcan los eventos de los últimos CTX_SECONDS (hasta CTX_MAX)
EPISODE_SECONDS = 20.0            # tras un error, durante este tiempo los eventos normales también se registran

INFO, WARN, ERROR = "INFO", "WARN", "ERROR"
RANK = {INFO: 0, WARN: 1, ERROR: 2}
ALERT = "⚠"
BENIGN = ("iniciando la captura", "esperando la primera captura")     # fases normales del arranque, no errores (si duran, el motor las escala)

# Avisos de librerías (mpv, Qt, logging): solo cuentan los que indican un problema real y no están en la lista de inofensivos.
SIGNIFICANT = ("fail", "cannot", "can't", "could not", "unable", "unsupported", "not supported", "no such file", "error",
               "invalid", "missing", "crash", "denied", "opengl", "exception")
HARMLESS = ("no key binding found", "unable to set geometry", "propagatesizehints", "desynchronisation", "dropping frame",
            "qt.qpa.", "setgeometry", "sizehint")

Entry = namedtuple("Entry", "seq t level src msg")
Incident = namedtuple("Incident", "key level text match src rows t cond")
MAX_INCIDENTS = 60
WARN_ALERTS = ("fundido cruzado no disponible", "warn ")   # avisos del panel que no impiden el funcionamiento (alerta, no error)
WARN_CONTAINS = ("está minimizada",)                       # «la ventana de Traktor está minimizada»: es alerta, no error


def norm(msg):
    """Primera línea de un mensaje, sin «⚠», sin el «(se repitió N veces más)» y en minúsculas: sirve para saber si dos textos
    hablan de lo mismo."""
    first = str(msg).strip().splitlines()[0] if str(msg).strip() else ""
    first = first.split("   (se repitió")[0]
    return first.lstrip(ALERT + "️ ").strip().lower()


def covers(a, b):
    """¿Un texto contiene al otro? (los muy cortos solo si son iguales)."""
    if not a or not b:
        return False
    if len(a) >= 12 and len(b) >= 12:
        return a in b or b in a
    return a == b


def summarize(msg, limit=160):
    """Resumen de una línea de un mensaje de registro: su primera línea y, si trae un traceback, el error final
    (p. ej. «excepción en el bucle del motor (ZeroDivisionError: division by zero)»)."""
    lines = [ln.rstrip() for ln in str(msg).splitlines() if ln.strip()]
    head = lines[0].strip() if lines else ""
    head = re.sub(r"\s*\(se repitió \d+ veces más\)\s*$", "", head).lstrip(ALERT + "️ ").strip()
    if len(lines) > 1 and any(ln.startswith("Traceback") for ln in lines):
        tail = lines[-1].strip()
        if tail and tail not in head:
            head += f" ({tail})"
    if len(head) > limit:
        head = head[:limit - 1].rstrip() + "…"
    return head


def alert_level(text):
    """Nivel de un aviso «⚠» del panel: alerta (WARN: el programa sigue funcionando o el aviso se arregla solo, p. ej. sin
    fundido cruzado o con Traktor minimizada) o error (ERROR: algo no funciona, p. ej. sin OCR, sin captura o un video que
    no carga)."""
    t = str(text).lstrip().lower()
    return WARN if t.startswith(WARN_ALERTS) or any(k in t for k in WARN_CONTAINS) else ERROR


# Fila del panel «Estado del Reproductor» donde se ubica cada problema (el ⚠ se enciende solo en la fila afectada).
ROWS_BY_SRC = {"captura": ("capture",), "layout": ("capture",), "ocr": ("track", "check"), "reproductor": ("player",),
               "fundido": ("player",), "opengl": ("player",), "video": ("player",), "video-rendimiento": ("player",), "video-vigilancia": ("player",)}
OCR_ROWS = ("track", "check")


def text_rows(text):
    t = str(text).lower()
    if t.startswith("autovinculaci"):
        return ("auto",)
    if "sin ocr" in t or "tesseract" in t:
        return OCR_ROWS
    return ()


def rows_for(src, text=""):
    """Filas del panel afectadas por un mensaje del registro (según su fuente y su texto)."""
    rows = text_rows(text)
    if rows:
        return rows
    s = str(src).lower()
    if s.startswith("mpv"):
        return ("player",)
    if s in ROWS_BY_SRC:
        return ROWS_BY_SRC[s]
    if "captura" in str(text).lower():
        return ("capture",)
    return ()


def fmt_entry(e, tr=None):
    """«HH:MM:SS.mmm [NIVEL] fuente: mensaje» (las líneas siguientes de un traceback van sangradas). Con `tr` (el traductor de
    la interfaz) la fuente y el mensaje salen en el idioma elegido: solo el visor lo usa; el archivo y el portapapeles siguen
    en español."""
    ms = int((e.t % 1) * 1000)
    src = tr(str(e.src)) if tr else e.src
    head = f"{time.strftime('%H:%M:%S', time.localtime(e.t))}.{ms:03d} [{e.level:<5}] {src}: "
    lines = str(e.msg).splitlines() or [""]
    if tr:
        lines = [tr(ln) for ln in lines]
    return head + lines[0] + "".join("\n    " + ln for ln in lines[1:])


def classify(msg):
    """Nivel de un mensaje del motor según su texto (el motor los emite sin nivel)."""
    m = str(msg).lstrip().lower()
    if m.startswith(ALERT) or m.startswith(("motor:", "reproductor:")) or "no se pudo" in m or "error" in m:
        return ERROR
    return INFO


def harmless(msg):
    """¿Es ruido conocido e inofensivo de una librería? (tecla sin asignar al hacer clic en mpv, geometría de ventanas de Qt…)"""
    m = str(msg).lower()
    return any(h in m for h in HARMLESS)


def significant(msg):
    """¿Un aviso de una librería indica un problema real?"""
    m = str(msg).lower()
    return any(k in m for k in SIGNIFICANT) and not harmless(m)


def versions():
    """Versiones de las librerías que importan al diagnosticar."""
    out = []
    for name in ("mpv", "PySide6", "cv2", "numpy", "pytesseract", "PIL"):
        try:
            mod = __import__(name)
            out.append(f"{name} {getattr(mod, '__version__', '?')}")
        except Exception:
            out.append(f"{name} (no se pudo importar)")
    try:
        import importlib.util
        out.append("windows_capture " + ("instalado" if importlib.util.find_spec("windows_capture") else "no instalado"))
    except Exception:
        pass
    return out


def environment():
    """Líneas de entorno para la cabecera del registro."""
    try:
        osn = platform.platform()
    except Exception:
        osn = sys.platform
    return [f"Python {sys.version.split()[0]} · {osn}", "Librerías: " + " · ".join(versions())]


class DevLog:
    def __init__(self):
        self._lock = threading.RLock()
        self._buf = deque(maxlen=MAXLEN)        # lo registrado (visible)
        self._ctx = deque(maxlen=CTX_RING)      # eventos normales recientes (no visibles; contexto de un error)
        self._seq = 0
        self._err_seq = 0                      # seq del último aviso/error (para la marca de la pestaña)
        self._episode_until = 0.0              # hasta cuándo se registran también los eventos normales (tras un error)
        self._dup = {}                         # mensaje -> [último instante, repeticiones calladas]
        self._dup_info = {}                    # evento normal con dedupe -> último instante (no se repite en DUP_WINDOW)
        self._flood = {}                       # fuente -> [inicio de ventana, líneas, avisado]
        self._route_last = {}                  # fila del panel -> último aviso ya registrado (un aviso que sigue ahí no se repite)
        self._inc = {}                         # incidencias abiertas (alertas/errores que muestra la barra de estado)
        self._inc_ver = 0                      # sube con cada cambio de `_inc` (la interfaz solo redibuja si cambió)
        self.header_lines = []                 # datos del equipo/configuración (cabecera del archivo y de «Copiar»)
        self.enabled, self.path, self._f = False, LOG_FILE, None
        self._hooked = False
        self._hdr_done = False

    # ------------------------------------------------------------ escritura
    def _append(self, level, src, msg, now):
        self._seq += 1
        e = Entry(self._seq, now, level, src, msg)
        self._buf.append(e)
        if RANK[level] >= RANK[WARN]:
            self._err_seq = e.seq
        self._write(e)
        return e

    def add(self, level, src, msg, dedupe=None, quiet=False, _now=None, _inc=True):
        """Registra una línea. Devuelve la entrada, o None si era un duplicado reciente o la fuente se desbordó.
        INFO (evento normal): solo queda visible durante un episodio de error; si no, se guarda como contexto.
        ERROR: vuelca el contexto anterior, se registra con detalle y abre un episodio.
        `dedupe`: no repetir el mismo texto durante DUP_WINDOW (por defecto sí para avisos/errores).
        `quiet`: si es un duplicado, tampoco cuenta como «repetición» (lo usa el panel, que solo re-explica lo ya registrado).
        Un WARN/ERROR también abre una incidencia (barra de estado) aunque la línea sea un duplicado y no se vuelva a escribir;
        `_inc=False` lo evita (lo usa el panel, que gestiona sus propias condiciones)."""
        level = level if level in RANK else INFO
        if dedupe is None:
            dedupe = level != INFO
        msg = str(msg).strip()
        if not msg:
            return None
        if len(msg) > MAX_MSG:
            msg = msg[:MAX_MSG] + " …(recortado)"
        now = time.time() if _now is None else _now
        if _inc and RANK[level] >= RANK[WARN]:
            self._open_event(level, src, msg, now)
        with self._lock:
            if level == INFO and dedupe:                                # evento normal repetido (p. ej. un aviso de librería)
                last = self._dup_info.get(msg)
                if last is not None and now - last < DUP_WINDOW:
                    return None
                self._dup_info[msg] = now
                if len(self._dup_info) > 500:
                    self._dup_info.clear()
            if level == INFO and now >= self._episode_until:
                e = Entry(0, now, INFO, src, msg)
                self._ctx.append(e)                                 # contexto: no se muestra ni se escribe
                return None
            skipped = 0
            if dedupe:
                d = self._dup.get(msg)
                if d is not None and now - d[0] < DUP_WINDOW:
                    if not quiet:
                        d[1] += 1              # mismo texto que hace nada: no se repite
                    return None
                skipped = d[1] if d is not None else 0
                self._dup[msg] = [now, 0]
                if len(self._dup) > 500:       # la tabla no crece sin límite
                    for k in [k for k, v in self._dup.items() if now - v[0] > DUP_WINDOW][:250]:
                        self._dup.pop(k, None)
            fl = self._flood.setdefault(src, [now, 0, False])
            if now - fl[0] > FLOOD_WIN:
                fl[0], fl[1], fl[2] = now, 0, False
            fl[1] += 1
            if fl[1] > FLOOD_N:
                if fl[2]:
                    return None
                fl[2] = True
                level, msg = WARN, f"demasiados mensajes seguidos de «{src}»: se omiten los siguientes unos segundos"
            if skipped:
                msg += f"   (se repitió {skipped} veces más)"
            if level == ERROR:
                if now >= self._episode_until:                      # primer error del episodio: antes de él, el contexto
                    recent = [c for c in self._ctx if now - c.t <= CTX_SECONDS][-CTX_MAX:]
                    for c in recent:
                        self._append(INFO, c.src, c.msg, c.t)
                    self._ctx.clear()
                self._episode_until = now + EPISODE_SECONDS         # y después, unos segundos más de eventos normales
            return self._append(level, src, msg, now)

    def info(self, src, msg, dedupe=False):
        return self.add(INFO, src, msg, dedupe=dedupe)

    def warn(self, src, msg):
        return self.add(WARN, src, msg)

    def error(self, src, msg):
        return self.add(ERROR, src, msg)

    def external(self, src, msg, level):
        """Mensaje de una librería (mpv, Qt, logging): los errores siempre; los avisos solo si indican un problema real.
        El ruido inofensivo conocido se descarta (mpv.log lo conserva); el resto de avisos es solo contexto."""
        if level == ERROR:
            return self.add(ERROR, src, msg)
        if harmless(msg):
            return None
        if significant(msg):
            return self.add(WARN, src, msg)
        return self.add(INFO, src, msg, dedupe=True)

    def exception(self, src, msg="", exc=None, level=ERROR):
        """Error con su traceback (dentro de un `except`, o pasando la excepción). `level=WARN` para un fallo que no impide
        el funcionamiento (p. ej. un adorno que no se pudo aplicar o una función con alternativa)."""
        if exc is None:
            exc = sys.exc_info()[1]
        if exc is None:
            return self.add(level, src, msg or "error")
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()
        return self.add(level, src, (msg + "\n" if msg else "") + tb)

    # -------------------------------------------------------------- lectura
    def since(self, seq=0):
        with self._lock:
            return [e for e in self._buf if e.seq > seq]

    def entries(self, min_level=INFO):
        with self._lock:
            return [e for e in self._buf if RANK[e.level] >= RANK[min_level]]

    def recent_context(self, n=40):
        """Últimos eventos normales (no visibles): se añaden solo al copiar el registro a mano."""
        with self._lock:
            return list(self._ctx)[-n:]

    @property
    def last_seq(self):
        return self._seq

    @property
    def err_seq(self):
        return self._err_seq

    def set_header(self, lines):
        self.header_lines = [str(x) for x in lines]

    def header(self):
        return environment() + list(self.header_lines)

    def text(self, min_level=INFO, header=True, context=False):
        """Texto para pegar en un mensaje. `context=True` añade al final los últimos eventos normales (solo al copiar a mano)."""
        lines = [fmt_entry(e) for e in self.entries(min_level)]
        if not lines:
            lines = ["(sin errores registrados)"]
        if context:
            ctx = self.recent_context()
            if ctx:
                lines += ["", "--- Eventos recientes (contexto, no son errores) ---"] + [fmt_entry(e) for e in ctx]
        if header:
            lines = ["ViDeck Pulse · registro copiado " + time.strftime("%Y-%m-%d %H:%M:%S")] + self.header() + [""] + lines
        return "\n".join(lines)

    def clear(self):
        with self._lock:
            self._buf.clear()
            self._ctx.clear()
            self._dup.clear()
            self._dup_info.clear()
            self._route_last.clear()
            self._episode_until = 0.0
            now = time.time()                                           # los avisos siguen abiertos (el problema sigue sin solucionarse):
            for i in sorted(self._inc.values(), key=lambda i: i.t):     # el registro limpio los vuelve a anotar para no contradecir la barra
                self._append(i.level, i.src, i.text + "   (sigue sin solucionarse)", now)

    # -------------------------------------------------------------- archivo
    def configure(self, enabled, path=None):
        """Activa/desactiva el archivo. Al activarlo vuelca lo que ya había registrado en memoria."""
        with self._lock:
            if path:
                self.path = path
            if enabled and not self.enabled:
                self.enabled = True
                self._open()
                for e in list(self._buf):                            # lo ya registrado en memoria
                    self._write(e)
            elif not enabled and self.enabled:
                self.enabled = False
                self._close()

    def _open(self):
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) > MAX_FILE:
                os.replace(self.path, self.path + ".1")              # rota: conserva la sesión anterior
            self._f = open(self.path, "a", encoding="utf-8", buffering=1)
            self._hdr_done = False                                   # la cabecera de sesión se escribe con el primer error
        except Exception:
            self._f = None

    def _close(self):
        f, self._f = self._f, None
        if f:
            try:
                f.close()
            except Exception:
                pass

    def _write(self, e):
        if self.enabled and self._f:
            try:
                if not self._hdr_done:                               # sin errores el archivo no crece: ni cabecera de sesión
                    self._hdr_done = True
                    self._f.write(f"\n===== sesión {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
                    for ln in self.header():
                        self._f.write(ln + "\n")
                self._f.write(fmt_entry(e) + "\n")
            except Exception:
                pass

    def close(self):
        with self._lock:
            self._close()

    # ----------------------------------------------------------- recolección
    def install_hooks(self):
        """Excepciones no controladas (hilo principal y demás hilos), avisos de `logging` y de `warnings`."""
        if self._hooked:
            return
        self._hooked = True
        prev = sys.excepthook

        def excepthook(et, ev, tb):
            try:
                self.add(ERROR, "excepción", "".join(traceback.format_exception(et, ev, tb)).rstrip())
            finally:
                prev(et, ev, tb)
        sys.excepthook = excepthook

        prev_t = getattr(threading, "excepthook", None)
        if prev_t:
            def thook(args):
                try:
                    if args.exc_type is not SystemExit:
                        name = args.thread.name if args.thread else "?"
                        self.add(ERROR, f"hilo {name}", "".join(traceback.format_exception(
                            args.exc_type, args.exc_value, args.exc_traceback)).rstrip())
                finally:
                    prev_t(args)
            threading.excepthook = thook

        class _H(logging.Handler):
            def emit(h, rec):
                try:
                    lv = ERROR if rec.levelno >= logging.ERROR else WARN
                    msg = rec.getMessage()
                    if rec.exc_info:
                        msg += "\n" + "".join(traceback.format_exception(*rec.exc_info)).rstrip()
                    self.external(rec.name, msg, lv)
                    if logging.lastResort is not None:           # la consola sigue mostrando los avisos como siempre
                        logging.lastResort.handle(rec)
                except Exception:
                    pass
        h = _H(level=logging.WARNING)
        logging.getLogger().addHandler(h)
        logging.captureWarnings(True)

    # ------------------------------------------------------- textos del panel
    def _covered(self, alert, now=None, within=180.0):
        """¿Ya hay en el registro (de hace poco) un aviso/error que explica este texto del panel? Evita la segunda línea con
        las mismas palabras: el panel suele repetir, con algo más, lo que el módulo que falló ya registró con su detalle."""
        now = time.time() if now is None else now
        with self._lock:
            for e in reversed(self._buf):
                if now - e.t > within:
                    break
                if RANK[e.level] < RANK[WARN]:
                    continue
                first = str(e.msg).splitlines()[0].split("   (se repitió")[0].strip().lower() if e.msg else ""
                if len(first) >= 12 and (first in alert.lower() or alert.lower() in first):
                    return True
        return False

    def explain(self, level, src, msg):
        """Registra un aviso/error del motor salvo que algo reciente ya lo explique (p. ej. la excepción con su traceback)."""
        if RANK.get(level, 0) >= RANK[WARN] and self._covered(str(msg)):
            self._open_event(level, src, msg, time.time())      # ya está en el registro, pero para la barra de estado sigue siendo un aviso
            return None
        return self.add(level, src, msg)

    # ------------------------------------------------------- incidencias (barra de estado)
    def _trim(self):
        if len(self._inc) > MAX_INCIDENTS:                          # no crecen sin límite: se descartan las más viejas
            for k in sorted(self._inc, key=lambda k: self._inc[k].t)[:len(self._inc) - MAX_INCIDENTS]:
                del self._inc[k]

    def _open_event(self, level, src, msg, now):
        """Un aviso/error puntual (excepción, error de librería…): queda abierto hasta que se soluciona (`resolve`) o se
        reinicia el programa, salvo que ya lo explique una condición del panel (esa se cierra sola al solucionarse)."""
        match = norm(msg)
        if not match:
            return
        lv = ERROR if level == ERROR else WARN
        with self._lock:
            if any(i.cond and covers(match, i.match) for i in self._inc.values()):
                return
            key = ("ev", match)
            old = self._inc.get(key)
            if old is not None and RANK[old.level] > RANK[lv]:
                lv = old.level
            self._inc[key] = Incident(key, lv, summarize(msg), match, src, rows_for(src, msg), now, False)
            self._inc_ver += 1
            self._trim()

    def _cond_sync(self, src, items, rows=None, now=None):
        """Condiciones abiertas de una fuente (fila del panel u otra): `items` = [(nivel, texto)] de lo que hay AHORA.
        Lo nuevo se abre, lo que ya no está se cierra (el problema se solucionó) y absorbe los avisos puntuales que
        decían lo mismo."""
        now = time.time() if now is None else now
        with self._lock:
            keep, changed = set(), False
            for lv, body in items:
                match = norm(body)
                if not match:
                    continue
                key = ("cond", src, match)
                keep.add(key)
                for k in [k for k, i in self._inc.items() if not i.cond and covers(i.match, match)]:
                    del self._inc[k]                                 # el aviso puntual lo explica esta condición
                    changed = True
                old = self._inc.get(key)
                if old is None or old.level != lv:
                    r = tuple(rows) if rows is not None else tuple(dict.fromkeys((src,) + text_rows(body)))
                    self._inc[key] = Incident(key, lv, summarize(body), match, src, r, old.t if old else now, True)
                    changed = True
            for k in [k for k, i in self._inc.items() if i.cond and i.src == src and k not in keep]:
                del self._inc[k]
                changed = True
            if changed:
                self._inc_ver += 1
                self._trim()

    def condition(self, src, level, text, rows=None):
        """Condición que dura hasta solucionarse (se cierra con `clear_condition`): p. ej. Tesseract ausente."""
        self._cond_sync(src, [(ERROR if level == ERROR else WARN, text)], rows)

    def clear_condition(self, src):
        self._cond_sync(src, [])

    def incidents(self):
        """Incidencias abiertas, de la más grave a la más leve (y la más reciente primero); sin repetidas."""
        with self._lock:
            items = list(self._inc.values())
        items.sort(key=lambda i: (-RANK[i.level], -i.t))
        out = []
        for i in items:
            if not any(covers(i.match, o.match) for o in out):
                out.append(i)
        return out

    def row_mark(self, row):
        """Detalle de los avisos abiertos de una fila del panel («» si no tiene): ahí se enciende el ⚠."""
        with self._lock:
            seen = []
            for i in sorted(self._inc.values(), key=lambda i: -i.t):
                if row in i.rows and i.text not in seen:
                    seen.append(i.text)
        return " · ".join(seen)

    def resolve(self, src, level=ERROR):
        """Esa parte del programa volvió a funcionar (p. ej. el siguiente video cargó bien, la captura entrega imagen otra vez):
        se cierran sus errores puntuales abiertos. `level=None` cierra también sus alertas. No toca las condiciones del panel
        (esas se cierran solas cuando su aviso desaparece). Devuelve cuántos cerró."""
        with self._lock:
            gone = [k for k, i in self._inc.items()
                    if not i.cond and i.src == src and (level is None or i.level == level)]
            for k in gone:
                del self._inc[k]
            if gone:
                self._inc_ver += 1
        return len(gone)

    @property
    def inc_ver(self):
        return self._inc_ver

    def route(self, src, text, log=True):
        """Limpia un texto del panel: cada segmento «⚠ …» (separados por « · ») pasa al registro y en su lugar queda un
        único «⚠». Devuelve (texto limpio, detalle de los avisos o «»)."""
        if not text or ALERT not in text:
            if log:
                self._cond_sync(src, [])                  # el aviso desapareció: la condición se cierra sola
                if self._route_last:                      # y si vuelve, se registrará de nuevo
                    self._route_last.pop(src, None)
                    self._route_last.pop(src + "\0info", None)
            return text, ""
        keep, alerts, benign = [], [], []
        for seg in str(text).split(" · "):
            t = seg.strip()
            if not t:
                continue
            if t.startswith(ALERT):
                body = t.lstrip(ALERT + "️ ").strip()
                if body.startswith(BENIGN):
                    keep.append(body)
                    benign.append(body)
                elif body:
                    alerts.append(body)
            else:
                keep.append(t)
        if log:
            # Un aviso que sigue en el panel se registra UNA vez (no cada segundo ni cada 30 s); si cambia, se registra el nuevo;
            # si desaparece y vuelve, se registra de nuevo.
            now_alert, now_benign = " · ".join(alerts), " · ".join(benign)
            if now_alert != self._route_last.get(src):
                if now_alert:
                    for a in alerts:
                        if not self._covered(a):
                            self.add(alert_level(a), src, a, quiet=True, _inc=False)
                    self._route_last[src] = now_alert
                else:
                    self._route_last.pop(src, None)
            if now_benign != self._route_last.get(src + "\0info"):
                if now_benign:
                    for b in benign:
                        self.info(src, b, dedupe=True)
                    self._route_last[src + "\0info"] = now_benign
                else:
                    self._route_last.pop(src + "\0info", None)
            self._cond_sync(src, [(alert_level(a), a) for a in alerts])   # abiertas mientras el aviso siga en el panel
        clean = " · ".join(keep)
        if alerts:
            clean = (clean + " " if clean else "") + ALERT
        return clean, " · ".join(alerts)


devlog = DevLog()                    # instancia única: `from core.devlog import devlog`

# atajos de módulo
info, warn, error, exception, external = devlog.info, devlog.warn, devlog.error, devlog.exception, devlog.external
add, route, explain = devlog.add, devlog.route, devlog.explain
install_hooks, configure, close = devlog.install_hooks, devlog.configure, devlog.close
