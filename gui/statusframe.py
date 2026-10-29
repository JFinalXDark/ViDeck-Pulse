# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Barra de estado de ViDeck Pulse: una franja PLANA al pie de la ventana, del mismo color que la ventana (sin marco ni bordes);
solo unas líneas finas verticales separan el texto de los dos contadores.

Siempre empieza con una esfera dibujada (gui/sphere.py) y «Estado:» (ver core/status.py): verde = todo en orden, amarilla = alerta
(«Alerta:», se mantiene hasta que se soluciona), roja = error (resumen + «Revisa el registro de errores (Log)»). La esfera cambia de
color según el aviso que se ve. En el extremo derecho van dos zonas separadas con la MISMA esfera amarilla (alertas) y roja (errores) y un número:
cuántos errores y cuántas alertas hay abiertos ahora (0 | 0 cuando todo está en orden); se actualizan solos.

Dos cosas distintas llegan aquí:
  · `set_states([State, …])` / `set_state(State)`: el estado permanente (lo calcula la ventana con las incidencias abiertas del
    registro). Con varios avisos se TURNAN cada `ROTATE_S` segundos (5 s) para que se alcancen a leer: primero los errores, cada uno
    con su color y «(i/N)». Un error nuevo salta a la barra al instante; con el puntero encima la barra se detiene y el tooltip
    muestra todos los avisos.
  · `showMessage(texto, ms)` / `clearMessage()`: mismo uso que la `QStatusBar` de Qt, para los mensajes pasajeros («Se aplica al
    reiniciar el programa.»). Salen en verde tras «Estado:»; si hay una alerta o un error abiertos mandan estos, y el mensaje
    pasajero se ve cuando se solucionan, si aún no caducó.
`set_always(bool)`: con la casilla «Activar Barra de Estado» (pestaña Desarrollador) la barra se ve siempre; sin ella solo
aparece mientras hay algo que decir (un aviso abierto o un mensaje pasajero) y se oculta después."""
import html

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizeGrip, QSizePolicy

from core import status as st_mod
from core.status import ALERT, COLORS, ERROR, OK, ROTATE_S, State
from core.i18n import tr as T
from gui.sphere import Sphere

STYLE = """
#statusFrame, #statusPane {{ background: transparent; border: none; }}
#statusField, #statusCount {{ background: transparent; border: none; padding: 0px; }}
#statusSep {{ background: {line}; border: none; }}
"""
LINE_MIX = 0.30                  # los separadores son el color del texto mezclado un 30 % con el del fondo: se ven en tema claro y oscuro
SEP_H = 16                       # alto de los separadores (más cortos que la barra)


class StatusFrame(QFrame):
    HEIGHT = 26

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusFrame")
        self._line = None
        self._restyle()
        self.setFixedHeight(self.HEIGHT)
        self._info, self._always = "", False
        self._states = st_mod.compose_all()              # estados permanentes: «Estado: Todo está funcionando correctamente» (verde)
        self._idx, self._hover, self._rot_on, self._busy = 0, False, False, False
        self._plain = ""
        lay = self._lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 0, 0)              # la esfera queda alineada con el margen del contenido; los contadores llegan hasta la esquina
        lay.setSpacing(0)
        # zona del estado: esfera de color + texto
        self.main = self._pane()
        row = self.main.layout()
        row.setContentsMargins(0, 0, 12, 0)
        row.setSpacing(8)
        self.sphere = Sphere(COLORS[OK])
        row.addWidget(self.sphere, 0, Qt.AlignVCenter)
        self.field = QLabel("")
        self.field.setObjectName("statusField")
        try:
            self.field.setTextFormat(Qt.RichText)        # «Estado:» en negrita
        except Exception:
            pass
        self.field.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)     # un mensaje largo no ensancha la ventana
        row.addWidget(self.field, 1)
        self.field.installEventFilter(self)              # al cambiar de ancho el texto se recorta de nuevo (incluida la primera vez que se muestra)
        self.main.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay.addWidget(self.main, 1)
        # extremo derecho: alertas (esfera amarilla) y errores (esfera roja) abiertos ahora, separados por líneas finas
        self.sep_main = self._sep()
        self.pane_alert, self.sph_alert, self.lbl_alert = self._counter(COLORS[ALERT])
        self.sep_counts = self._sep()
        self.pane_err, self.sph_err, self.lbl_err = self._counter(COLORS[ERROR])
        lay.addWidget(self.sep_main, 0, Qt.AlignVCenter)
        lay.addWidget(self.pane_alert, 0)
        lay.addWidget(self.sep_counts, 0, Qt.AlignVCenter)
        lay.addWidget(self.pane_err, 0)
        self.grip = QSizeGrip(self)                      # esquina para cambiar el tamaño de la ventana
        lay.addWidget(self.grip, 0, Qt.AlignBottom | Qt.AlignRight)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.clearMessage)
        self._rot = QTimer(self)                         # turno entre varios avisos (alertas y errores)
        self._rot.setInterval(int(ROTATE_S * 1000))
        self._rot.timeout.connect(self._rotate)
        self._refresh()

    def _pane(self):
        """Una zona de la barra (sin marco ni fondo propio: se ve el de la ventana) con su propia fila horizontal."""
        f = QFrame(self)
        f.setObjectName("statusPane")
        QHBoxLayout(f)
        return f

    def _sep(self):
        """Línea vertical fina (1 px) que separa dos zonas de la barra."""
        f = QFrame(self)
        f.setObjectName("statusSep")
        f.setFixedSize(1, SEP_H)
        return f

    def _counter(self, color):
        """Zona con la esfera del color dado y un número: (zona, esfera, etiqueta)."""
        pane = self._pane()
        row = pane.layout()
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(6)
        sph = Sphere(color)
        lbl = QLabel("0")
        lbl.setObjectName("statusCount")
        try:
            lbl.setMinimumWidth(int(lbl.fontMetrics().horizontalAdvance("00")) + 2)      # no baila al pasar de 9 a 10
            lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)       # el número pegado a su esfera
        except Exception:
            pass
        row.addWidget(sph, 0, Qt.AlignVCenter)
        row.addWidget(lbl, 0, Qt.AlignVCenter)
        pane.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        return pane, sph, lbl

    # ------------------------------------------------------------------ aspecto
    def line_color(self):
        """Color de los separadores, sacado de la paleta actual (así se ven también con el tema oscuro de Windows)."""
        try:
            pal = self.palette()
            bg, fg = pal.color(QPalette.Window), pal.color(QPalette.WindowText)
            mix = lambda a, b: max(0, min(255, int(round(a + (b - a) * LINE_MIX))))
            return QColor(mix(bg.red(), fg.red()), mix(bg.green(), fg.green()), mix(bg.blue(), fg.blue())).name()
        except Exception:
            return "palette(mid)"

    def _restyle(self):
        line = self.line_color()
        if line != self._line:
            self._line = line
            self.setStyleSheet(STYLE.format(line=line))

    def changeEvent(self, e):
        super().changeEvent(e)
        try:
            if e.type() == QEvent.PaletteChange:             # el usuario cambió de tema claro/oscuro con el programa abierto
                self._restyle()
        except Exception:
            pass

    # ------------------------------------------------------------------ estado permanente (alertas y errores)
    @staticmethod
    def _ident(st):
        """Qué aviso es (sin el «(i/N)», que cambia cuando aparecen o se solucionan otros)."""
        return (st.level, st.prefix, st.body)

    def set_state(self, state):
        """Un solo estado permanente (`core.status.State`)."""
        self.set_states([state])

    def set_states(self, states):
        """Estados permanentes (`core.status.State`, uno por aviso abierto, de más grave a más leve): los calcula la ventana a partir
        de las incidencias abiertas. Si hay varios se turnan; el aviso que se está viendo se conserva mientras siga abierto y un
        ERROR nuevo salta a la barra de inmediato (las alertas nuevas entran en el turno sin interrumpir)."""
        states = [s for s in (states or ()) if isinstance(s, State)] or st_mod.compose_all()
        was_idle = self._states[0].level == OK
        old = [self._ident(s) for s in self._states]
        cur = self._ident(self._current())
        ids = [self._ident(s) for s in states]
        self._states = states
        fresh = [i for i, x in enumerate(ids) if x not in old and states[i].level == st_mod.ERROR]
        if fresh and not was_idle:
            self._idx = fresh[0]                         # error nuevo mientras se turnaban otros avisos: se ve ya
            if self._rot_on:
                self._rot.start()                        # y cuenta el intervalo completo desde ahora
        elif cur in ids:
            self._idx = ids.index(cur)                   # el aviso que se estaba leyendo sigue en pantalla
        else:
            self._idx = 0                                # lo más grave primero
        self._idx = min(max(self._idx, 0), len(self._states) - 1)
        self._sync_rot()
        self._refresh()

    def _current(self):
        return self._states[min(max(self._idx, 0), len(self._states) - 1)]

    @property
    def state(self):
        return self._current()

    @property
    def states(self):
        return list(self._states)

    # ------------------------------------------------------------------ turno entre varios avisos
    def _rot_wanted(self):
        return len(self._states) > 1 and self._states[0].level != OK

    def _sync_rot(self):
        """El reloj de turnos solo corre mientras hay más de un aviso abierto."""
        want = self._rot_wanted()
        if want and not self._rot_on:
            self._rot_on = True
            self._rot.start()
        elif not want and self._rot_on:
            self._rot_on = False
            self._rot.stop()

    def _rotate(self):
        """Pasa al siguiente aviso (cada ROTATE_S segundos). Con el puntero encima de la barra no cambia, para poder leerlo."""
        if self._hover or not self._rot_wanted():
            return
        self._idx = (self._idx + 1) % len(self._states)
        self._refresh()

    def eventFilter(self, obj, ev):
        try:
            if obj is self.field and ev.type() == QEvent.Resize and not self._busy:
                self._busy = True
                try:
                    self._elide()
                finally:
                    self._busy = False
        except Exception:
            pass
        return False

    def enterEvent(self, e):
        self._hover = True
        try:
            super().enterEvent(e)
        except Exception:
            pass

    def leaveEvent(self, e):
        self._hover = False
        try:
            super().leaveEvent(e)
        except Exception:
            pass

    def hideEvent(self, e):
        self._hover = False                              # si la barra se oculta con el puntero encima, el turno no se queda pausado
        try:
            super().hideEvent(e)
        except Exception:
            pass

    def _view(self):
        """Lo que se ve ahora: un aviso abierto (alerta o error) manda; si no, el mensaje pasajero; si no, «Todo está funcionando…»."""
        cur = self._current()
        if cur.level != OK:
            return cur
        if self._info:
            return State(OK, st_mod.PREFIX, self._info, "")
        return cur

    @property
    def level(self):
        return self._view().level

    def dot_color(self):
        return COLORS[self._view().level]

    # ------------------------------------------------------------------ API de QStatusBar (mensajes pasajeros)
    def showMessage(self, text, timeout=0):
        self._info = str(text or "").strip()
        if timeout and timeout > 0:
            self._timer.start(int(timeout))
        else:
            self._timer.stop()                           # 0 = se queda hasta que llegue otro mensaje o se borre
        self._refresh()

    def clearMessage(self):
        self._info = ""
        self._timer.stop()
        self._refresh()

    def currentMessage(self):
        return self._info

    def text(self):
        """Lo que dice la barra ahora (sin el círculo): «Estado: …» o «Alerta: …»."""
        return self._plain

    # ------------------------------------------------------------------ visibilidad
    def set_always(self, on):
        """True: la barra se ve siempre (con la casilla de «Desarrollador»); False: solo mientras haya algo que decir."""
        self._always = bool(on)
        self._refresh()

    @property
    def always(self):
        return self._always

    def is_shown(self):
        """¿Debe verse ahora? (siempre, o mientras haya un aviso abierto o un mensaje pasajero)."""
        return self._always or self._current().level != OK or bool(self._info)

    def _refresh(self):
        self._elide()
        self.setVisible(self.is_shown())

    def _elide(self):
        """Pone la esfera y el texto. Si no cabe se recorta el cuerpo del mensaje, nunca el final («Revisa el registro…»)."""
        v = self._view()
        pre, full, tail = T(v.prefix), T(v.body), T(v.tail)         # (con Español no cambian; con English se traducen al mostrarse)
        self._plain = f"{pre} {full}{tail}"
        body = full
        elided = False
        try:
            w = int(self.field.width()) - 4
            if w > 20:
                fm = self.field.fontMetrics()
                fixed = fm.horizontalAdvance(f"{pre} ") + fm.horizontalAdvance(tail)
                room = max(40, w - int(fixed))
                body = fm.elidedText(full, Qt.ElideRight, room)
                elided = body != full
        except Exception:
            body = full
        self.sphere.set_color(COLORS[v.level])
        try:
            self.field.setText(f'<b>{html.escape(pre)}</b> {html.escape(body)}{html.escape(tail)}')
            if len(self._states) > 1 and self._current().level != OK:
                self.field.setToolTip("\n".join(f"{T(x.prefix)} {T(x.body)}{T(x.tail)}" for x in self._states))     # todos los avisos abiertos
            else:
                self.field.setToolTip(self._plain if elided else "")
        except Exception:
            self.field.setText(self._plain)
        self._show_counts()

    def counts(self):
        """(errores, alertas) abiertos ahora: lo que muestran las dos zonas del extremo derecho."""
        return st_mod.counts(self._states)

    def _show_counts(self):
        errs, alerts = self.counts()
        for pane, lbl, n, what in ((self.pane_err, self.lbl_err, errs, "Errores abiertos"), (self.pane_alert, self.lbl_alert, alerts, "Alertas abiertas")):
            lbl.setText(str(n))
            pane.setToolTip(T(f"{what}: {n}"))
            try:
                f = lbl.font()
                f.setBold(n > 0)                         # en negrita cuando hay alguno
                lbl.setFont(f)
            except Exception:
                pass

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()
