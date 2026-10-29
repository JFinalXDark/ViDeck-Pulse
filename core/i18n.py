# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Idioma de la interfaz: Español (por defecto) o English.

El programa está escrito en español y toda su lógica interna (registro, avisos, filas del panel) sigue usando esos textos. Con
English, cada texto que llega a la pantalla se traduce **al mostrarse** (`tr`), así que el idioma no toca la lógica: con Español
`tr` devuelve el texto tal cual (no hace nada).

Cómo traduce `tr` (el catálogo está en `core/i18n_en.py`, «texto en español» → «English»):
  1. el texto entero, exacto o como plantilla («Deck {0}: {1}» → «Deck {0}: {1}»; cada {n} es una parte variable que también se
     traduce si tiene un texto conocido);
  2. sin los adornos de los extremos (⚠ ✓ ▶ … y espacios);
  3. por trozos: los mensajes del programa se arman uniendo frases con « · », « — », «; » o «. »: se traduce cada una;
  4. un texto con un paréntesis al final y una «etiqueta: valor».
Lo que no está en el catálogo se deja como está (nunca se pierde un mensaje). En las pruebas, `RECORD = True` anota los textos
que quedaron en español (`misses`) para completar el catálogo.

Mayúscula inicial (`cap`, en los dos idiomas): la primera palabra de cada línea que se muestra y de cada trozo que sigue a « · »,
« — » o al punto final de una frase empieza con mayúscula («Estado · Iniciando…», «Status · Starting…»). Se aplica en `tr`, que es
el último paso antes de la pantalla, así que los mensajes del programa pueden seguir armándose con trozos en minúscula. No se toca
lo que no es una palabra corriente: ffmpeg, libmpv, mpv…, nombres de archivo o ruta (llevan «.», «/», «\\» o «_»), números y siglas.
`tf` traduce igual que `tr` pero sin mayúscula: para un trozo que se pega en medio de otro texto («▶ sonando»).
"""
import os
import re

LANGS = {"es": "Español", "en": "English"}


def _boot_language():
    """El idioma con el que arranca el programa: el de config.json (clave «language»); en las pruebas, la variable VP_LANG."""
    code = os.environ.get("VP_LANG")
    if code is None:
        try:
            import json
            with open("config.json", encoding="utf-8-sig") as f:
                code = json.load(f).get("language")
        except FileNotFoundError:                                  # primer inicio (aún no hay config.json): el idioma del instalador, o el de Windows
            try:
                from . import paths
                code = paths.initial_language()
            except Exception:                                      # noqa: BLE001
                code = None
        except Exception:
            code = None
    return code if code in LANGS else "es"


LANG = _boot_language()
RECORD = bool(os.environ.get("VP_I18N_RECORD"))        # solo pruebas: anota los textos que no se pudieron traducir
misses = set()
if RECORD and os.environ.get("VP_I18N_DUMP"):                # solo pruebas: al terminar guarda los textos que no se pudieron traducir
    import atexit
    import json

    def _dump():
        with open(os.environ["VP_I18N_DUMP"], "w", encoding="utf-8") as f:
            json.dump(sorted(misses), f, ensure_ascii=False, indent=0)
    atexit.register(_dump)

_PH = re.compile(r"\{(\d+)\}")
_exact, _templates, _spanish_words, _cache = {}, [], set(), {}
_loaded = False

_LEAD = re.compile(r"^[\s⚠✓✗✔·•—–→▶⏸■✨⏳🎯⚙🔴🟡🟢️]+")
_TRAIL = re.compile(r"[\s⚠✓✗✔️]+$")
_SEPS = (" · ", " — ", "; ")
_OPEN_ENDS = (": ", " → ", " — ", " (", "(", "«", ". ")       # un texto que termina así es el comienzo de un mensaje más largo
_STOP = set("de el la los las en que se del un una con por para no es al lo su sus como más y o a e ya ni si cuando mientras desde "
            "hasta sin sobre entre este esta estos estas".split())
_ACCENT = re.compile(r"[áéíóúñÁÉÍÓÚÑ¿¡«»]")
_WORD = re.compile(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]+")


def language():
    return LANG


def set_language(code):
    """Cambia el idioma de los textos que se muestran (se llama una vez, al arrancar, antes de crear la interfaz)."""
    global LANG
    LANG = code if code in LANGS else "es"
    _cache.clear()
    _capcache.clear()
    return LANG


def _load():
    """Compila el catálogo la primera vez que hace falta (con Español nunca se carga)."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    from .i18n_en import EN
    tmpl = []
    for es, en in EN.items():
        if _PH.search(es):
            lits = _PH.split(es)[0::2]
            rx = re.compile("^" + "(.*?)".join(re.escape(x) for x in lits) + "$", re.S)
            need = sorted((x for x in lits if x), key=len, reverse=True)       # trozos fijos que el texto debe contener (filtro rápido)
            tmpl.append((sum(len(x) for x in lits), (need, rx, en)))
        else:
            _exact[es] = en
    for es, en in EN.items():                             # y sin espacios en los extremos (una frase suelta de un texto más largo)
        if es.strip() != es and es.strip() and not _PH.search(es):
            _exact.setdefault(es.strip(), en.strip())
        if "{" in es or "}" in es or "{" in en or "}" in en:
            continue
        if es.endswith(_OPEN_ENDS):                       # el principio de un mensaje armado con «texto» + algo: «texto{0}»
            lits = [es]
            rx = re.compile("^" + re.escape(es) + "(.*?)$", re.S)
            tmpl.append((len(es), ([es], rx, en + "{0}")))
    for es, en in EN.items():
        k = es.lstrip(" ·")
        if k != es and k and en.lstrip(" ·") != en and "{" not in es[:1]:
            e2 = en.lstrip(" ·")
            if _PH.search(es):
                lits = _PH.split(k)[0::2]
                rx = re.compile("^" + "(.*?)".join(re.escape(x) for x in lits) + "$", re.S)
                need = sorted((x for x in lits if x), key=len, reverse=True)
                tmpl.append((sum(len(x) for x in lits) - 1, (need, rx, e2)))
            else:
                _exact.setdefault(k, e2)
    tmpl.sort(key=lambda t: -t[0])                        # primero las plantillas más específicas (más texto fijo)
    _templates[:] = [t for _, t in tmpl]
    es_w = {w.lower() for k in EN for w in _WORD.findall(_PH.sub(" ", k))}
    en_w = {w.lower() for v in EN.values() for w in _WORD.findall(_PH.sub(" ", v))}
    _spanish_words.update(es_w - en_w)                    # palabras que solo aparecen en español (detecta lo que falte)


def _lookup1(text):
    r = _exact.get(text)
    if r is not None:
        return r
    for need, rx, en in _templates:
        if all(x in text for x in need):
            m = rx.match(text)
            if m:
                return en.format(*[_inner(g) for g in m.groups()])
    return None


def _lookup(text):
    """Texto entero (exacto o plantilla) → English, o None. Si solo cambia la mayúscula de la primera letra («Fundido…» /
    «fundido…», como en las alertas del panel, que ponen la primera en mayúscula) se acepta y se conserva su mayúscula."""
    r = _lookup1(text)
    if r is None and text[:1].isalpha():
        c = text[0]
        r = _lookup1(c.swapcase() + text[1:])
        if r is not None and r[:1].isalpha():
            r = (r[0].upper() if c.isupper() else r[0].lower()) + r[1:]
    return r


def _inner(text):
    """Una parte variable de una plantilla: si es un texto conocido se traduce; si no (un número, un título) queda igual."""
    if not text or not _has_letters(text):
        return text
    r = _tr(text)
    return text if r is None else r


def _has_letters(text):
    return re.search(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]{2}", text) is not None


def _tr(text, depth=0):
    """Traducción de `text` o None si no se pudo traducir nada."""
    r = _lookup(text)
    if r is not None:
        return r
    if depth > 6:
        return None
    lead, tail = _LEAD.match(text), _TRAIL.search(text)
    if lead or tail:                                      # «⚠ texto», «texto ⚠», «✓ texto»…
        a, b = lead.end() if lead else 0, tail.start() if tail else len(text)
        core = text[a:b]
        if core and (a or b != len(text)):
            r = _tr(core, depth + 1)
            if r is not None:
                return text[:a] + r + text[b:]
    if "\n" in text:                                      # varias líneas (un tooltip, un aviso con detalle): cada línea por separado
        parts = text.split("\n")
        out = [_tr(p, depth + 1) if p.strip() else None for p in parts]
        if any(o is not None for o in out):
            return "\n".join(o if o is not None else p for o, p in zip(out, parts))
    for sep in _SEPS:                                     # frases unidas por un separador
        if sep in text:
            parts = text.split(sep)
            out = [_tr(p, depth + 1) for p in parts]
            if any(o is not None for o in out):
                return sep.join(o if o is not None else p for o, p in zip(out, parts))
    if ". " in text:                                      # varias frases (el punto se queda con su frase)
        parts = re.split(r"(?<=\.) ", text)
        if len(parts) > 1:
            out = [_tr(p, depth + 1) for p in parts]
            if any(o is not None for o in out):
                return " ".join(o if o is not None else p for o, p in zip(out, parts))
    if text.endswith(")") and " (" in text:               # «texto (detalle)»
        head, tail_ = text.rsplit(" (", 1)
        h, t = _tr(head, depth + 1), _tr(tail_[:-1], depth + 1)
        if h is not None or t is not None:
            return (h if h is not None else head) + " (" + (t if t is not None else tail_[:-1]) + ")"
    if text.endswith(":") and len(text) > 2:              # «Etiqueta:» (el catálogo puede tenerla con o sin los «:»)
        r = _tr(text[:-1], depth + 1)
        if r is not None:
            return r + ":"
    if ": " in text:                                      # «Etiqueta: valor»
        head, rest = text.split(": ", 1)
        h, t = _tr(head, depth + 1), _tr(rest, depth + 1)
        if h is None:                                     # la etiqueta puede estar en el catálogo con sus «:» («Estado:»)
            h = _tr(head + ":", depth + 1)
            h = h[:-1] if h is not None and h.endswith(":") else None
        if h is not None or t is not None:
            return (h if h is not None else head) + ": " + (t if t is not None else rest)
    return None


def looks_spanish(text):
    """¿Parece que `text` sigue en español? (acentos o signos, palabras de enlace, o palabras que solo están en el catálogo
    en español). Solo se usa para anotar lo que falta en el catálogo."""
    if _ACCENT.search(text):
        return True
    ws = [w.lower() for w in _WORD.findall(text)]
    return (len(ws) >= 2 and any(w in _STOP for w in ws)) or any(w in _spanish_words for w in ws)


def still_spanish(text):
    """¿Quedó algo en español en un texto ya traducido? (acentos o palabras que solo existen en el catálogo en español: no vale
    mirar las palabras de enlace, que en inglés dan falsos avisos). Solo para anotar lo que falta en el catálogo."""
    if re.search(r"[áéíóúñÁÉÍÓÚÑ¿¡]", text):
        return True
    return any(w.lower() in _spanish_words for w in _WORD.findall(text))


# ---------------------------------------------------------------- mayúscula inicial
_CAP_DECOR = set("⚠✓✗✔▶⏸■✨⏳🎯⚙🔴🟡🟢●○★☆▪•·–—→¿¡\ufe0f")      # lo que puede ir delante de la primera letra
_CAP_QUOTES = set("«\"“'‘")
_CAP_TAG = re.compile(r"<[^>]*>")
_CAP_BOUND = re.compile(r"\n|<br\s*/?>| · | — |(?<=[.?!]) ")                  # dónde empieza un trozo nuevo
_CAP_TOKEN = re.compile(r"[^\s<]+")
_CAP_TRIM = ".,;:!?…)»\"'”’"
_CAP_EXC = set("ffmpeg ffprobe libmpv mpv python-mpv x264 x265 h264 h265 hevc avc ocr bpm fps gpu cpu rgb rgba hz khz db kbps mbps "
               "mb gb kb ms px roi rois vu ui gui json mp4 mkv avi mov url ip ram hdr hwdec vo gpu-api printwindow wgc ".split())
_CAP_SHORT = set("a e o y u al de el en es la lo mi ni no se si sí su tu un ya is it if in on to of or an at as by we do be".split())
_CAP_ABBR = set("p ej sup inf izq der etc vs máx mín aprox núm pág seg min".split())         # «p. ej.», «sup. izq.»: sigue en minúscula
_CAP_BAD_CHARS = set("./\\_@#%=+*&|{}[]~^$")


def _cap_at(text, q):
    """Índice de la letra que debe ir en mayúscula si el trozo que empieza en `q` lo pide; None si no."""
    n = len(text)
    while q < n:
        m = _CAP_TAG.match(text, q)
        if m:
            q = m.end()
        elif text[q] in _CAP_QUOTES:                          # «nombre.mp4», "título": lo que va entre comillas es un dato, no se toca
            return None
        elif text[q] in _CAP_DECOR or (text[q] == " " and q > 0 and (text[q - 1] in _CAP_DECOR or text[q - 1] == ">")):
            q += 1
        else:
            break
    if q >= n or not (text[q].isalpha() and text[q].islower()):
        return None
    tok = _CAP_TOKEN.match(text, q).group()
    core = tok.rstrip(_CAP_TRIM)
    low = core.lower()
    if (not core or low in _CAP_EXC or low.split("'")[0] in _CAP_EXC or low.split("-")[0] in _CAP_EXC or any(c in _CAP_BAD_CHARS for c in core)
            or any(c.isdigit() for c in core) or any(c.isupper() for c in core[1:])
            or (len(core) <= 2 and low not in _CAP_SHORT) or len(text[q].upper()) != 1):
        return None
    return q


def cap(text):
    """`text` con la primera palabra de cada línea y de cada trozo (tras « · », « — » o el punto de una frase) en mayúscula."""
    if not isinstance(text, str) or not text or not any(c.islower() for c in text):
        return text
    spots = []
    if not text.startswith((" ", "\t")):                  # un texto que empieza con espacio es la continuación de otro
        spots.append(0)
    for m in _CAP_BOUND.finditer(text):
        if m.group()[-1] == " " and m.group() not in (" · ", " — "):          # fin de frase: «p. ej.» o «e.g.» no lo son
            prev = text[:m.start()].split(" ")[-1].strip("(«\"'¿¡").rstrip(".?!").lower()
            if not prev or prev in _CAP_ABBR or "." in prev or prev in _CAP_EXC:
                continue
        spots.append(m.end())
    out = None
    for sp in spots:
        q = _cap_at(text, sp)
        if q is not None:
            if out is None:
                out = list(text)
            out[q] = text[q].upper()
    return text if out is None else "".join(out)


def _translate(text):
    if LANG == "es" or not isinstance(text, str) or not text:
        return text
    r = _cache.get(text)
    if r is None:
        _load()
        r = _tr(text) if (text in _exact or looks_spanish(text)) else None      # (un texto sin nada en español no se busca)
        r = text if r is None else r
        if RECORD and still_spanish(r):
            misses.add(text)
        if len(_cache) > 3000:
            _cache.clear()
        _cache[text] = r
    return r


_capcache = {}


def tr(text):
    """El texto en el idioma elegido y con mayúscula inicial. Si no es un texto se devuelve tal cual."""
    if not isinstance(text, str) or not text:
        return text
    key = (LANG, text)
    r = _capcache.get(key)
    if r is None:
        r = cap(_translate(text))
        if len(_capcache) > 3000:
            _capcache.clear()
        _capcache[key] = r
    return r


def tf(text):
    """Como `tr` pero sin mayúscula inicial: para un trozo que se pega en medio de otro texto («▶ sonando»)."""
    return _translate(text)
