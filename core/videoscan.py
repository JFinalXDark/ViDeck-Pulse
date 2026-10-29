# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Búsqueda de videos en la carpeta de videos (update 13): progresiva, en un hilo aparte y sin tocar la pantalla desde él.

Se busca SOLO al iniciar el programa y con el motor detenido (al detenerlo, al volver a la ventana, con el botón «Recargar», al cerrar
«Preparar videos»); con el motor en marcha la lista queda quieta. El hilo no llama a Qt: deja avisos en una cola y la ventana principal los
recoge con un temporizador (`ScanJob.drain`), así que la interfaz no se cuelga aunque la carpeta esté en una unidad lenta (red, Z:).

`walk` recorre la carpeta con `os.scandir` (sin `resolve()`, sin abrir los archivos) y devuelve {ruta absoluta: (tamaño, mtime_ns)}: esa
«firma» sirve para saber si algo cambió y para reconocer un video renombrado (mismo tamaño y fecha) sin leerlo.
"""
import os
import queue
import threading
import time

from .media import VIDEO_EXTS

BATCH = 40                    # videos por aviso de progreso
SETTLE_S = 0.6                # un video nuevo se da por completo si su tamaño no cambia durante este tiempo (puede estar copiándose)


def _is_video(name):
    return os.path.splitext(name)[1].lower() in VIDEO_EXTS


def walk(folder, on_batch=None, cancel=None, batch=BATCH, scandir=os.scandir):
    """Recorre `folder` (subcarpetas incluidas, sin seguir enlaces) y devuelve {ruta: (tamaño, mtime_ns)} de los videos. Una carpeta que no
    se puede leer se salta; `cancel` (threading.Event) corta la búsqueda. `on_batch({ruta: firma})` se llama cada `batch` videos."""
    out, pend, stack = {}, {}, [os.path.abspath(folder)]
    while stack:
        if cancel is not None and cancel.is_set():
            break
        d = stack.pop()
        try:
            it = scandir(d)
        except OSError:
            continue
        with it:
            try:
                entries = list(it)
            except OSError:
                entries = []
        for e in entries:
            if cancel is not None and cancel.is_set():
                break
            try:
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
                elif _is_video(e.name) and e.is_file():
                    st = e.stat()
                    p = os.path.abspath(e.path)
                    out[p] = pend[p] = (st.st_size, st.st_mtime_ns)
            except OSError:
                continue
            if on_batch is not None and len(pend) >= batch:
                on_batch(dict(pend))
                pend.clear()
    if on_batch is not None and pend:
        on_batch(dict(pend))
    return out


def settle(paths, wait=SETTLE_S, cancel=None, sig_of=None):
    """Separa los videos que ya están completos de los que todavía cambian (se están copiando): espera `wait` s y vuelve a mirar su tamaño y
    fecha. Devuelve (estables {ruta: firma}, inestables [ruta])."""
    def sig(p):
        try:
            st = os.stat(p)
            return (st.st_size, st.st_mtime_ns)
        except OSError:
            return None
    sig_of = sig_of or sig
    first = {p: sig_of(p) for p in paths}
    if not first:
        return {}, []
    if cancel is not None:
        cancel.wait(wait)
    else:
        time.sleep(wait)
    stable, unstable = {}, []
    for p, a in first.items():
        b = sig_of(p)
        if a is not None and a == b and a[0] > 0:
            stable[p] = a
        else:
            unstable.append(p)
    return stable, unstable


def diff(old, new):
    """Compara dos firmas {ruta: firma}. Devuelve (nuevos [ruta], desaparecidos [ruta], renombrados [(vieja, nueva)]): un desaparecido y un nuevo
    con la MISMA firma (tamaño > 0 y fecha) y que no se repite en ninguna de las dos listas son el mismo archivo renombrado o movido."""
    added = sorted(p for p in new if p not in old)
    removed = sorted(p for p in old if p not in new)
    by_old, by_new = {}, {}
    for p in removed:
        by_old.setdefault(old[p], []).append(p)
    for p in added:
        by_new.setdefault(new[p], []).append(p)
    renamed = []
    for sig, olds in by_old.items():
        news = by_new.get(sig, [])
        if sig and sig[0] > 0 and len(olds) == 1 and len(news) == 1:
            renamed.append((olds[0], news[0]))
    gone = {o for o, _ in renamed}
    came = {n for _, n in renamed}
    return [p for p in added if p not in came], [p for p in removed if p not in gone], sorted(renamed)


class ScanJob:
    """Una búsqueda en un hilo aparte. Avisos que deja en la cola (se leen con `drain()` desde el hilo de la interfaz):
        ("batch", {ruta: firma})                      un lote de videos encontrados
        ("done", firma_completa, inestables)          terminó (firma_completa: {ruta: firma}; inestables: videos nuevos que aún se copian)
        ("cancelled", firma_parcial)                  se canceló (la firma es solo lo hallado hasta entonces)
        ("error", "texto")                            fallo inesperado
    `known`: videos conocidos antes (rutas); con él, los NUEVOS se comprueban con `settle` para no listar uno a medio copiar. Sin él (primera
    búsqueda) no se espera nada."""

    def __init__(self, folder, known=None, batch=BATCH, wait=SETTLE_S, scandir=os.scandir):
        self.folder, self.known, self.batch, self.wait, self._scandir = folder, known, batch, wait, scandir
        self.events = queue.Queue()
        self.cancel_ev = threading.Event()
        self.finished = threading.Event()
        self.thread = threading.Thread(target=self._run, name="busqueda-videos", daemon=True)
        self.partial = {}

    def start(self):
        self.thread.start()
        return self

    def cancel(self):
        self.cancel_ev.set()

    def wait_done(self, timeout):
        """Espera hasta `timeout` s a que termine (o se cancele). True si ya terminó."""
        return self.finished.wait(timeout)

    @property
    def done(self):
        return self.finished.is_set()

    def drain(self):
        out = []
        while True:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                return out

    def _run(self):
        try:
            def on_batch(b):
                self.partial.update(b)
                self.events.put(("batch", b))
            sig = walk(self.folder, on_batch, self.cancel_ev, self.batch, self._scandir)
            if self.cancel_ev.is_set():
                self.events.put(("cancelled", dict(sig)))
                return
            unstable = []
            if self.known is not None:
                new = [p for p in sig if p not in self.known]
                if new:
                    ok, unstable = settle(new, self.wait, self.cancel_ev)
                    if self.cancel_ev.is_set():
                        self.events.put(("cancelled", dict(sig)))
                        return
                    for p in unstable:
                        sig.pop(p, None)
            self.events.put(("done", sig, unstable))
        except Exception as e:                                  # noqa: BLE001: la búsqueda nunca debe tumbar el programa
            self.events.put(("error", f"{type(e).__name__}: {e}"))
        finally:
            self.finished.set()
