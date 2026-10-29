# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Texto y color de la barra de estado (sin Qt, para poder probarlo).

La barra siempre empieza con una esfera de color y «Estado:»:
  · verde    → todo funcionando correctamente (o un mensaje pasajero: «Se aplica al reiniciar el programa.»)
  · amarillo → alerta: algo no va como debería pero el programa sigue funcionando. El texto empieza con «Alerta:» en vez de
               «Estado:» y el aviso se mantiene hasta que el problema se soluciona.
  · rojo     → error: algo impide el funcionamiento correcto. Resumen del error y la indicación de revisar el registro de
               errores (Log). Mantiene «Estado:» al principio y SE MANTIENE hasta que el error se soluciona (revisar o limpiar el
               registro no lo apaga).
Si hay varios avisos (alertas y/o errores) se TURNAN en la barra, uno tras otro cada ROTATE_S segundos, para que se alcancen a leer;
cada uno se ve con su propio color y su «(1/3)», «(2/3)»… (primero los errores, que son lo más grave). Un error nuevo salta de
inmediato a la barra.
En el extremo derecho, dos zonas con la misma esfera amarilla (alertas) y roja (errores) cuentan los avisos abiertos ahora.
"""
from collections import namedtuple

from . import devlog as dl

OK, ALERT, ERROR = "ok", "alert", "error"
IDLE = "Todo está funcionando correctamente"
HINT = "Revisa el registro de errores (Log)."
HINT_OFF = "Activa y revisa el registro de errores (Log) en la pestaña Desarrollador."
PREFIX, PREFIX_ALERT = "Estado:", "Alerta:"
ROTATE_S = 5          # segundos que se ve cada aviso cuando hay varios (el texto de un error + «Revisa el registro…» es largo)
COLORS = {OK: "#2fb344", ALERT: "#f0b400", ERROR: "#e5383b"}      # verde · amarillo · rojo (se ven en tema claro y oscuro)

# level: OK/ALERT/ERROR · prefix: «Estado:»/«Alerta:» · body: lo que se puede recortar si no cabe · tail: lo que nunca se recorta
State = namedtuple("State", "level prefix body tail")


def plain(st):
    """El texto completo de un estado, sin recortes."""
    return f"{st.prefix} {st.body}{st.tail}"


def _cap(text):
    text = str(text).strip()
    return text[:1].upper() + text[1:]


def compose_all(incidents=(), info="", log_on=True):
    """Estados a mostrar, uno por incidencia abierta (`devlog.incidents()`): primero los errores y después las alertas. Con más de
    uno, cada texto lleva su «(i/N)». Sin incidencias: un solo estado verde con el mensaje pasajero `info` (o «Todo está funcionando
    correctamente»)."""
    items = [i for i in incidents if i.level == dl.ERROR] + [i for i in incidents if i.level != dl.ERROR]
    n = len(items)
    if not n:
        return [State(OK, PREFIX, str(info).strip() or IDLE, "")]
    out = []
    for k, inc in enumerate(items, 1):
        count = f" ({k}/{n})" if n > 1 else ""
        if inc.level == dl.ERROR:
            out.append(State(ERROR, PREFIX, "Error — " + _cap(inc.text).rstrip(" .:;"), count + ". " + (HINT if log_on else HINT_OFF)))
        else:
            out.append(State(ALERT, PREFIX_ALERT, _cap(inc.text), count))
    return out


def counts(states):
    """(errores, alertas) abiertos a partir de los estados de `compose_all` (los que van en los dos contadores de la barra: alertas a la izquierda, errores a la derecha)."""
    return (sum(1 for x in states if x.level == ERROR), sum(1 for x in states if x.level == ALERT))


def compose(incidents=(), info="", log_on=True):
    """El primer estado de `compose_all` (el más grave)."""
    return compose_all(incidents, info, log_on)[0]
