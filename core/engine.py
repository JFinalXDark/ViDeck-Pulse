# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Proceso en segundo plano: ventana de Traktor -> vúmetro/reloj -> OCR -> doble check -> mpv."""
import json
import os
import random
import statistics
import threading
import time

from PySide6.QtCore import QThread, Signal

from . import autolink as al
from . import devlog as dl
from . import layout as lay_mod
from . import paths
from .capture import WARMUP_MSG, WindowUnavailable, backend_label, make_grabber
from .db import fmt_time, normalize, parse_duration
from .glyphs import PAUSE, PLAY
from .media import same, video_duration
from .synclog import signed, synclog
from .videohealth import Health
from .vision import DeckSensor, OcrReader, parse_bpm, parse_pitch, parse_time

ROI_KEYS = ("title", "bpm", "remaining", "duration", "pitch", "vu")
CORE_KEYS = ("title", "bpm", "remaining", "duration", "vu")           # sin ellos no hay lectura (el tempo es opcional)
LAYOUT_RETRY = (20.0, 60.0, 120.0)                                    # s entre intentos si algún deck aún no se pudo leer
WARMUP_MAX_S = 8.0                       # esperar el primer fotograma de la captura más que esto ya no es normal
LANDING_MIN_S = 0.25                     # s: antes de esto no se da un salto por terminado (mpv tarda un momento en marcar que está saltando)
LANDING_MAX_S = 3.0                      # s: un salto que tarda más que esto se anota como lento y se aprende de él
LEAD_MIN, LEAD_MAX = 0.05, 1.5           # s: límites del adelanto aprendido del salto
LEAD_UP, LEAD_DOWN = 0.7, 0.3            # cuánto se corrige el adelanto hacia lo medido: subir es rápido (un salto lento se ve), bajar es lento
TRIM_MIN_PTS, TRIM_MIN_SPAN = 4, 12.0    # ajuste fino de velocidad: revisiones precisas (y s que abarcan) hacen falta para ver una deriva sostenida
TRIM_MIN_SLOPE, TRIM_MIN_R2, TRIM_MAX = 0.004, 0.85, 0.02   # deriva mínima (s por s), ajuste de la recta y tope del ajuste (±2 %)
LOAD_COMP_MAX, LOAD_COMP_GAIN, LOAD_COMP_MAX_DRIFT, LOAD_COMP_MIN = 2.5, 0.8, 3.0, 0.15   # compensación de arranque: tope (s), cuánto se corrige hacia lo medido, deriva máxima creíble y mínima que se aprende
LEARNED_FILE = "sync_learned.json"        # lo aprendido (adelanto del salto y compensación de arranque por video) entre sesiones
FIRST_CHECK_S = 1.5                      # s tras cargar un video en marcha hasta su 1.ª revisión (antes: initial_every = 3 s): se alinea mientras aún se funde
SLEW_MIN, SLEW_RATE, SLEW_MAX_S = 0.05, 0.02, 6.0
# Lectura de ±1 s: el reloj de Traktor da segundos enteros, así que esa Δ oscila hasta ±0.5 s solo por redondeo (y, como las revisiones
# son cada ~5 s, recorre ese rango despacio: parece que el video se separa). Mientras concuerde con la última medición precisa
# (COARSE_NOISE s) se muestra ESA; si se aparta más (posible deriva real) se pide ya una medición precisa.
PREC_KEEP_S, COARSE_NOISE, PREC_RECHECK_S = 25.0, 0.6, 10.0
# Corrección del adelanto del salto con lo que se midió DESPUÉS de saltar: si el salto aterrizó adelantado (atrasado) por Δ, el adelanto de ese video
# era Δ mayor (menor). Lo que tarda el salto no siempre se puede medir (un salto de menos de un ciclo del motor), así que la medición precisa manda.
LEAD_FIX_WAIT_S, LEAD_FIX_MIN, LEAD_FIX_MAX, LEAD_FIX_GAIN = 1.0, 0.08, 1.2, 0.85
# Salto de posición en Traktor (hot cue, clic en la onda, loop…): la posición del track cambió más de lo que explica el tiempo transcurrido
# (cada lectura de ±1 s, así que la tolerancia es holgada). El video no se espera a la revisión normal: se vuelve a sincronizar ya, con medición precisa.
JUMP_TOL, JUMP_MAX_GAP_S = 1.8, 15.0   # ajuste suave (u14): deriva mínima (s) que se corrige sin saltar, cuánto se frena/acelera el video (±2 %) y cuánto dura como máximo
TRIM_MIN_RISE = 0.12                     # s: lo que debe haber cambiado la deriva en ese rato (con menos, el ruido de la medida se confunde con una recta)
HEALTH_FAIL_SRC = "video-vigilancia"        # fuente del aviso «falló la vigilancia del video» (se cierra cuando vuelve a funcionar)
HEALTH_EVERY_S = 1.0                    # cada cuánto se mira la salud del video que suena (core/videohealth.py)
ENGINE_RECOVERED_S = 5.0                 # segundos de ciclos completos tras un error del bucle para darlo por solucionado
SIZE_SETTLE = 1.0                                                     # s que debe mantenerse un tamaño de ventana nuevo antes de actuar


def next_watch(s):
    """Desde cuántos s restantes se vigila el deck siguiente: lo configurado, pero nunca menos que la anticipación + 10 s (si no,
    el visual del siguiente deck entraría más tarde de lo pedido: aún no estaría verificado cuando toca)."""
    return max(float(s["watch"]), float(s["lead"]) + 10.0)


def _ks(key):
    """(método, (ancho, alto)) -> "printwindow:1920x1080" (clave del layout guardado para ese tamaño)."""
    return f"{key[0]}:{key[1][0]}x{key[1][1]}"
# El reloj de Traktor muestra segundos enteros. En el instante en que cambia un dígito, el valor real está justo en el
# cambio de entero (según cómo redondee Traktor, entre 0 y 1 s del número mostrado): 0.5 s es el punto medio, y lo que
# quede de error constante se compensa con `sync.offset`.
DISPLAY_BIAS = 0.5
# Cada cambio de dígito del reloj da la posición del track con ~±0.1 s. Se guardan los últimos (FLIP_KEEP s, FLIP_MAX como mucho)
# y la revisión precisa usa su mediana: un cambio aislado mal medido no decide, y el ruido baja con cada cambio de más.
FLIP_KEEP, FLIP_MAX, FLIP_AGREE = 4.5, 4, 0.4     # FLIP_AGREE: s de diferencia con la mediana a partir de la cual un cambio se descarta
MINI_FLIPS = 3                                     # cambios de dígito que hacen falta en una revisión precisa breve (reproducción normal)
# Margen horizontal (px) que se añade al recorte de cada ROI de texto al hacer OCR: un ROI dibujado justo por la derecha
# corta el último dígito (un «6» recortado se lee como «1»: 02:16 → 02:11). Arriba y abajo no se toca (el reloj y la
# duración están pegados).
PAD = {"remaining": 6, "duration": 8, "bpm": 6, "pitch": 6}


def _padded(roi, px):
    l, t, w, h = (int(v) for v in roi)
    l2 = max(0, l - px)
    return (l2, t, w + (l - l2) + px, h)


class DeckInfo:
    """Lo que Traktor muestra en la cabecera de un deck. Se lee en TODOS los decks (también el que espera con el
    siguiente track cargado), así su visual y su velocidad ya están validados cuando el deck empieza a sonar."""

    def __init__(self):
        self.title, self.duration, self.bpm = "", None, None      # bpm: ya filtrado (sin lecturas erróneas)
        self.dur_alts = []                                         # otras lecturas de la duración (otro recorte del ROI)
        self.entry, self.check = None, "—"                         # coincidencia del doble check (título + duración)
        self.target, self.cand, self.cand_n = None, None, 0       # coincidencia confirmada con N lecturas iguales
        self.bpm_ok, self.bpm_pend, self.bpm_key = None, None, None
        self.al_key, self.al_n, self.al_bad = None, 0, 0           # autovinculación: lectura estable / intentos de leer el tempo


class Engine(QThread):
    state = Signal(dict)
    log = Signal(str)
    changed = Signal()                                             # el motor cambió la config (ROIs) o tracks_db.json (autovínculo)

    def __init__(self, cfg, db, player, videos):
        super().__init__()
        self.cfg, self.db, self.player = cfg, db, player
        self.videos = list(videos)
        self.forced = None
        self.last_reading = {}
        self.mode, self.check = "Visual Aleatorio", "—"
        self.capture_status = "—"
        self._alive = True
        self.info = {d: DeckInfo() for d in cfg["decks"]}
        self._target, self._last_entry, self._decks_lines = None, None, {}
        self._speed, self._rand_since = 1.0, 0.0
        # revisión periódica de sincronización
        self._last_check, self._sync_info = 0.0, "sin medir"
        self._drift_pending, self._resume_check, self._sync_now = False, False, False
        self._was_playing, self._ref_checked = False, False
        self._louder_n, self._why, self._ocr_msg = 0, "", ""
        self._cycle_ms, self._last_perr, self._ref_warn = 0.0, "", ""
        self._prec = None                                          # última medición precisa: (hora, Δ, video)
        self._jump = None                                          # última lectura de la posición del track: (posición, hora, video, velocidad)
        self._jump_cand = None                                     # posible salto de posición a la espera de confirmarse
        self._lead_probe = None                                    # último salto, a la espera de su 1.ª medición precisa: (video, adelanto usado, hora)
        self._slew, self._slew_sum = (0.0, 0.0, 0.0), 0.0         # ajuste suave en curso: (fracción de velocidad, hasta cuándo, desde cuándo) · s que ya desplazó
        self._applied, self._trim, self._hist = 1.0, 0.0, []     # velocidad dada al video · ajuste fino (fracción) · (hora, deriva) de revisiones precisas desde el último salto
        self._load_comp, self._load_probe = {}, None      # compensación de arranque aprendida (clave «tipo|video», s) · video recién cargado cuya 1.ª revisión se mide
        self._load_kind = "start"                         # cómo entró el video: «start» (el deck acaba de arrancar o se arrancó el motor: lectura del reloj con edad) · «ahead» (anticipación: el deck en espera se vigila de cerca)
        self._landing, self._lead, self._cur_deck = None, {}, None   # salto en curso (se mide lo que tarda) · adelanto aprendido por video · deck del visual
        self.health, self._health_t, self._health_failed = Health(), 0.0, False   # vigila el video: alerta si va con bajo rendimiento, error si sale pantalla negra
        # sincronía precisa y pre-roll
        self._dense_until = 0.0                          # hasta cuándo se revisa con frecuencia (inicio del video / cambio de tempo)
        self._rem_new, self._rem_last, self._rem_t, self._flip = None, None, 0.0, None   # lecturas del reloj y su último «tic»
        self._flips, self._rem_stamps, self._rem_seen = [], {}, None   # cambios de dígito recientes [(hora, restante)] · hora del fotograma de la
                                                                       # última lectura del reloj de cada deck · último fotograma ya mirado
        self._mini, self._mini_next, self._mini_cap = False, 0.0, 0.0  # revisión precisa breve en curso · cuándo toca la siguiente · tope de duración
        self._pre, self._pre_n, self._released, self._start_at = False, 0, False, None   # pre-roll: el track aún no empezó
        self._first_speed, self._drift_prev, self._tempo_ref = True, 0.0, 1.0
        # anticipación del siguiente deck
        self._ahead, self._next_text = None, ""           # {"frm", "to", "kind", "title"} mientras el siguiente visual ya entró
        self._rems, self._next_t = {}, {}                  # restante leído por deck (valor, instante) / último chequeo del deck en espera
        self._deck_on, self._drv_deck, self._drv_on = None, None, None   # ¿el deck que manda el visual suena él mismo?
        self._vu_moving = None                                           # ¿el vúmetro del deck actual se está moviendo? (None: sin ROI de vúmetro)
        self._fast_fix = False                             # el deck en espera acaba de arrancar: la 1.ª medición corrige sin confirmar
        # layout automático (ROIs detectados en la captura de la ventana)
        self._lay_thread, self._lay_res, self._lay_key = None, None, None
        self._lay_want, self._lay_forced, self._lay_next, self._lay_tries, self._lay_note = False, False, 0.0, 0, ""
        self._lay_hold, self._lay_img_key, self._size_seen = False, None, None   # hold: los ROIs son de otro tamaño de ventana
        self._prof_t, self._sensors_reset = 0.0, False
        # autovinculación track de Traktor Pro 4 <-> video
        self.autolink_hold = False                         # la ventana de configuración está abierta: no se toca tracks_db.json
        self._auto_text, self._auto_last, self._vdur = "", {}, {}
        self._load_learned()                               # lo aprendido en sesiones anteriores (si hay)

    def _emit(self, msg):
        """Mensaje del motor: va al registro (con su nivel) y a la interfaz, que solo marca ⚠ si es un error."""
        dl.explain(dl.classify(msg), "motor", str(msg).lstrip("⚠ ").strip() if str(msg).lstrip().startswith("⚠") else msg)
        self.log.emit(msg)

    @property
    def _lay_note(self):
        return self._lay_note_v

    @_lay_note.setter
    def _lay_note(self, v):
        self._lay_note_v = v
        if v and "⚠" not in v:                                    # los avisos ⚠ ya se registran como error
            dl.info("layout", v.strip(" ·"))

    def stop(self):
        self._alive = False

    def force(self, path):
        self.forced = path

    def deck_snapshot(self):
        """Lo que Traktor Pro 4 muestra ahora en cada deck (para la detección manual de la canción cargada)."""
        return {d: {"title": i.title, "duration": i.duration, "alts": list(i.dur_alts), "bpm": i.bpm}
                for d, i in self.info.items()}

    def redetect_layout(self):
        """Botón «Detectar layout ahora»: detecta los ROIs de la captura actual aunque el modo sea manual."""
        self._lay_forced, self._lay_tries, self._lay_next = True, 0, 0.0

    def sync_now(self):
        """Botón «Sincronizar ahora»: en la próxima lectura fuerza la posición del video."""
        self._sync_now = True

    # ------------------------------------------------------------------ bucle
    def run(self):
        cfg = self.cfg
        dl.devlog.resolve("motor")                               # un motor nuevo: los errores del anterior ya no cuentan (si siguen, vuelven a salir)
        self._warm_t = 0.0                                       # desde cuándo se espera el primer fotograma de la captura
        self._exc_t = 0.0                                        # instante del último error del bucle (para saber cuándo se recuperó)
        try:
            grab = make_grabber(cfg)              # ventana de Traktor (o pantalla); se crea en este hilo
        except Exception as e:
            dl.exception("motor", "no se pudo iniciar la captura")
            self._emit(f"No se pudo iniciar la captura: {e}")
            return
        ocr = OcrReader(cfg["ocr"].get("tesseract_cmd", ""))
        self._ocr_msg = "" if ocr.ok else ocr.status
        cc = cfg["capture"]
        dl.info("motor", f"Motor iniciado · captura {cc.get('mode')} / {cc.get('backend')} · layout {cc.get('layout', 'auto')} · "
                         f"OCR {'listo' if ocr.ok else 'NO disponible'}")
        if not ocr.ok:
            self._emit("⚠ " + ocr.status)
        sensors, deck, reading = {}, None, {}
        last_ocr = last_emit = 0.0
        level, playing, last_deck, actives = 0.0, False, None, {}
        try:
            while self._alive:
                t0, fresh = time.time(), False
                cycle_ok = True
                try:
                    grab.register(self._all_rois())          # solo se recortan los ROIs de decks y mixer
                    self._layout_tick(grab, t0)
                    if self._lay_hold:                       # la ventana cambió de tamaño (p. ej. pantalla completa): los ROIs
                        self.capture_status = grab.status() + self._lay_note       # actuales ya no valen y leerían basura
                        if t0 - last_emit > 0.4:                                   # (→ visual aleatorio): se mantiene el visual
                            last_emit = t0                                         # actual hasta tener los ROIs del nuevo tamaño
                            self.state.emit(self._snapshot(reading, deck, level, playing,
                                                           "⏸ layout en curso: se mantiene el visual actual", t0))
                        time.sleep(0.2)
                        continue
                    if self._sensors_reset:
                        sensors.clear()
                        self._sensors_reset = False
                    # 1) vúmetros + reloj -> ¿suena algo? ¿en qué deck?
                    levels, actives = {}, {}
                    for d, rois in cfg["decks"].items():
                        sen = sensors.setdefault(d, DeckSensor(d, cfg))
                        on_d = sen.update(grab, rois, t0)
                        if sen.just_saved:
                            self._emit(f"Deck {d}: referencia de silencio del vúmetro capturada automáticamente")
                        if on_d is not None:
                            actives[d], levels[d] = on_d, sen.level
                    if actives:
                        on = [d for d, a in actives.items() if a]
                        if on:
                            deck = self._pick_deck(deck, on, levels)
                        elif deck is None:
                            deck = next(iter(actives))
                        playing, level = bool(on), levels.get(deck, 0.0)
                    else:                                   # sin ROIs de vúmetro/reloj: no reactivo
                        deck, playing, level = deck or next(iter(cfg["decks"])), True, 0.0
                    deck = self._lookahead(deck, actives, playing, t0, grab, ocr)   # ¿entra ya el visual del siguiente deck?
                    self._cur_deck = deck
                    level = levels.get(deck, level)
                    self._deck_on = actives.get(deck)
                    self._vu_moving = sensors[deck].vu_on if deck in sensors else None
                    self._edge(deck, t0)
                    if playing and not self._was_playing:   # al reanudar se revisa la sincronía enseguida
                        self._resume_check = True
                    self._was_playing = playing
                    self._why = sensors[deck].reason if deck in sensors else ""
                    self.capture_status = grab.status() + self._ref_warn + self._lay_note

                    # 2) OCR de baja frecuencia + doble check de TODOS los decks (el de espera también)
                    if deck != last_deck:                   # deck nuevo: se lee ya su cabecera y su reloj
                        last_ocr, last_deck = 0.0, deck
                        self._rem_last, self._rem_seen = None, None
                        self._flips.clear()
                    if t0 - last_ocr >= cfg["ocr"]["interval"]:
                        last_ocr, fresh = t0, True
                        reading = self._ocr_tick(grab, ocr, deck, playing, t0)
                    cur = self.info[deck]
                    self._target, self._last_entry, self.check = cur.target, cur.entry, cur.check

                    # 2b) reloj del deck: en cada ciclo mientras haga falta (pre-roll o revisión precisa). El OCR tiene caché
                    #     por píxeles, así que solo llama a Tesseract cuando cambian los dígitos (1 vez por segundo)
                    try:
                        grab.boost = bool(self._wants_clock(t0, playing))     # (la captura de ventana lee más seguido mientras se mide con precisión)
                    except Exception:
                        pass
                    if fresh:
                        rem_new = reading.get("remaining")
                    elif self._wants_clock(t0, playing):
                        rem_new = self._read_remaining(grab, ocr, deck)
                    else:
                        rem_new = None
                    self._track_flip(rem_new, t0, grab, deck)
                    self._rem_new = rem_new

                    # 3) reproducción
                    self._apply(playing, reading, fresh, t0)
                    self._warm_t = 0.0                      # la captura ya entrega fotogramas
                    if t0 - self._health_t >= HEALTH_EVERY_S:
                        self._health_t = t0
                        self._health_tick(playing, t0)

                    if t0 - last_emit > 0.2:
                        last_emit = t0
                        dbg = "  |  ".join(x.debug() for x in sensors.values()) or "sin ROIs"
                        dbg += f"  |  ciclo {self._cycle_ms:.0f} ms"
                        perr = getattr(self.player, "last_error", "") or ""
                        if perr != self._last_perr:         # fallo al cargar un video: se avisa en la barra de estado
                            self._last_perr = perr
                            if perr:
                                self._emit("Reproductor: " + perr)
                        self._decks_lines = {d: self._deck_line(d, actives.get(d)) for d in self.info}
                        self.state.emit(self._snapshot(reading, deck, level, playing, dbg, t0))
                except WindowUnavailable as e:              # ventana minimizada / cerrada: se conserva el estado
                    msg = self._unavailable_text(str(e), t0)
                    self.capture_status = f"⚠ {msg}"
                    if t0 - last_emit > 0.4:
                        last_emit = t0
                        self.state.emit(self._snapshot(reading, deck, 0.0, False, f"⚠ {msg}", t0))
                    time.sleep(0.3)
                    continue
                except Exception as e:
                    dl.exception("motor", "excepción en el bucle del motor")      # con su traceback completo
                    self.log.emit(f"Motor: {e}")                                  # (a la interfaz: solo marca ⚠)
                    self._exc_t = time.time()
                    cycle_ok = False
                    time.sleep(0.5)
                if cycle_ok and self._exc_t and time.time() - self._exc_t > ENGINE_RECOVERED_S:
                    self._exc_t = 0.0                                             # varios segundos de ciclos completos sin fallar:
                    dl.devlog.resolve("motor")                                    # el error del bucle quedó solucionado
                self._cycle_ms = (time.time() - t0) * 1000
                time.sleep(max(0.0, cfg["vu"]["interval"] - (time.time() - t0)))
        finally:
            grab.close()
            self.player.set_playing(False)
            self.health.reset()                              # sin motor no hay video que vigilar: se cierran sus avisos
            if self._health_failed:
                self._health_failed = False
                dl.devlog.resolve(HEALTH_FAIL_SRC, level=dl.WARN)
            dl.info("motor", "Motor detenido")

    # ---------------------------------------------------------------- helpers
    def _health_tick(self, playing, now):
        """Una muestra por segundo de lo que mpv sabe del video que suena (bajo rendimiento → alerta; pantalla negra → error)."""
        try:
            fn = getattr(self.player, "health_sample", None)
            self.health.feed(fn() if fn else None, bool(playing), now)
            if self._health_failed:                           # volvió a funcionar: se cierra el aviso
                self._health_failed = False
                dl.devlog.resolve(HEALTH_FAIL_SRC, level=dl.WARN)
        except Exception:                                     # noqa: BLE001: vigilar nunca debe tirar el bucle del motor
            if not self._health_failed:                       # (una sola vez por racha: no se llena el registro cada segundo)
                self._health_failed = True
                dl.exception(HEALTH_FAIL_SRC, "falló la vigilancia del video", level=dl.WARN)

    def _unavailable_text(self, msg, now):
        """Texto del aviso de «captura no disponible». Esperar el primer fotograma al arrancar es normal (no es un error: queda como
        información); si pasan más de WARMUP_MAX_S s sin que llegue ninguno, deja de ser normal y se avisa como error."""
        if not msg.startswith(WARMUP_MSG.split(" de ")[0]):
            self._warm_t = 0.0
            return msg
        if not self._warm_t:
            self._warm_t = now
        if now - self._warm_t > WARMUP_MAX_S:
            return "no llega ninguna captura de esa región (¿Traktor minimizada, cerrada o tapada?)"
        return msg

    def _pick_deck(self, deck, on, levels):
        """Deck activo estable: se mantiene mientras suene; solo cambia si el actual se detiene o si otro suena
        claramente más fuerte durante 2 s seguidos (sin esto, con el vúmetro en silencio el deck saltaría de uno a otro)."""
        best = max(on, key=lambda d: levels[d])
        if deck not in on:
            self._louder_n = 0
            return best
        if best != deck and levels[best] - levels[deck] > 0.3:
            self._louder_n += 1
            if self._louder_n >= 20:
                self._louder_n = 0
                return best
        else:
            self._louder_n = 0
        return deck

    def _snapshot(self, reading, deck, level, playing, dbg, now):
        return {"mode": self.mode, "video": os.path.basename(self.player.current or ""),
                "bpm": reading.get("bpm"), "speed": self._speed, "deck": deck or "—",
                "level": level, "vu_moving": self._vu_moving, "playing": playing, "title": reading.get("title", ""),
                "check": ("⚠ Sin OCR: " + self._ocr_msg) if self._ocr_msg else self.check, "vu_dbg": dbg,
                "player": self._player_status(), "sync": self._sync_text(now), "next": self._next_text,
                "auto": self._auto_text,
                "capture": self.capture_status, "why": self._why, "speed_info": self._speed_text(reading),
                "decks": self._decks_lines}

    def _player_status(self):
        try:
            return self.player.status()
        except Exception:
            return ""

    def _sync_text(self, now):
        if self.mode != "Sincronizado":
            return "—"
        if self._pre:
            return self._sync_info
        if self._deck_on is False:
            return f"{self._sync_info} · deck en espera: sincroniza al arrancar"
        nxt = max(0.0, self._interval(now) - (now - self._last_check))
        left = self._dense_until - now
        extra = ((f" · revisión precisa (quedan {left:.0f} s)" if self._mini else f" · revisión frecuente (quedan {left:.0f} s)")
                 if self._dense(now) else "")
        return f"{self._sync_info} · próxima revisión en {nxt:.0f} s{extra}"

    def _check_ref(self, grab, key):
        """Modo manual: avisa si la captura ya no tiene el tamaño/método con el que se calibraron los ROIs."""
        ref = self.cfg["capture"].get("ref")
        size = key[1]
        self._ref_warn = ""
        if self.cfg["capture"].get("layout", "auto") == "auto" or not ref:
            return
        if list(ref.get("size", [])) != list(size) or ref.get("backend") != grab.backend_name:
            self._ref_warn = (f" · ⚠ los ROIs se calibraron en {ref['size'][0]}×{ref['size'][1]} ({backend_label(ref['backend'])}) y ahora se "
                              f"captura {size[0]}×{size[1]} ({backend_label(grab.backend_name)}): las lecturas saldrán desplazadas; "
                              f"recalibra, pulsa «Detectar layout ahora» o pon el layout en automático (pestaña Captura)")
            self._emit(f"Ahora se captura {size[0]}×{size[1]} ({backend_label(grab.backend_name)}) y los ROIs se calibraron en "
                          f"{ref['size'][0]}×{ref['size'][1]} ({backend_label(ref['backend'])}): recalibra si no lee bien")

    # --------------------------------------------------------- layout automático
    def _layout_needed(self, key):
        """¿Hay que detectar los ROIs? Si falta alguno, o si se calibraron con otro tamaño/método de captura. Una config
        antigua sin tamaño guardado se respeta."""
        decks = self.cfg["decks"]
        if any(not decks[d].get(k) for d in decks for k in CORE_KEYS):
            return True
        ref = self.cfg["capture"].get("ref")
        return bool(ref) and (ref.get("backend"), tuple(ref.get("size", ()))) != key

    def _ensure_pitch(self):
        """Calibraciones anteriores no tienen el ROI del tempo (%): se deduce de los de BPM y duración (mismo x que el BPM,
        misma fila que la duración: así lo definen las plantillas) sin necesidad de recalibrar."""
        changed = False
        for rois in self.cfg["decks"].values():
            b, d = rois.get("bpm"), rois.get("duration")
            if not rois.get("pitch") and b and d:
                rois["pitch"] = [int(b[0]), int(d[1]), int(b[2]), int(d[3])]
                changed = True
        if changed:
            self.changed.emit()

    def _ref_key(self):
        ref = self.cfg["capture"].get("ref")
        return (ref.get("backend"), tuple(ref.get("size", ()))) if ref else None

    def _store_profile(self, key, emit=True):
        """Guarda los ROIs actuales como el layout de ESTE tamaño de captura (ventana, maximizada, pantalla completa…)."""
        prof = self.cfg["capture"].setdefault("profiles", {})
        snap = {d: {k: [int(v) for v in r[k]] for k in ROI_KEYS if r.get(k)} for d, r in self.cfg["decks"].items()}
        if prof.get(_ks(key)) != snap:
            prof[_ks(key)] = snap
            if emit:
                self.changed.emit()

    def _apply_profile(self, key):
        """Si ya se conocía este tamaño, recupera sus ROIs al instante (sin detectar de nuevo). False si no hay."""
        snap = self.cfg["capture"].get("profiles", {}).get(_ks(key))
        if not snap:
            return False
        for d, rois in self.cfg["decks"].items():
            for k in ROI_KEYS:
                rois[k] = list(snap[d][k]) if snap.get(d, {}).get(k) else None
        self.cfg["capture"]["ref"] = {"backend": key[0], "size": list(key[1])}
        self._sensors_reset = True
        self.changed.emit()
        return True

    def _layout_tick(self, grab, now):
        size = getattr(grab, "size", None)
        if size is None:
            return
        self._ensure_pitch()
        key = (grab.backend_name, tuple(size))
        auto = self.cfg["capture"].get("layout", "auto") == "auto"
        rk = self._ref_key()
        # ROIs de otro tamaño de ventana: en automático no se lee nada (daría basura y el visual caería a aleatorio)
        self._lay_hold = bool(auto and rk is not None and rk != key)
        if self._lay_thread is not None:                         # detección en curso (hilo aparte: tarda unos 5-8 s)
            if not self._lay_thread.is_alive():
                self._layout_done(grab, key, now)
            return
        if key == self._lay_key:
            self._size_seen = None                               # (un cambio de tamaño de ida y vuelta no cuenta como estable)
        if key != self._lay_key:                                 # primer cuadro, o cambió el tamaño / el método de captura
            first = self._lay_key is None
            if not first:
                if self._size_seen is None or self._size_seen[0] != key:
                    self._size_seen = (key, now)
                if now - self._size_seen[1] < SIZE_SETTLE:       # el tamaño aún está cambiando (animación de pantalla completa)
                    return
            self._lay_key, self._size_seen = key, None
            if first and rk is None and not self._layout_needed(key):
                # config antigua con ROIs pero sin tamaño guardado: se da por buena para el tamaño con el que arranca
                self.cfg["capture"]["ref"] = {"backend": key[0], "size": list(key[1])}
                rk = key
                self.changed.emit()
            if rk is not None and rk != key:
                self._store_profile(rk)                          # lo de ahora vale para el tamaño anterior: se recuerda
                if self._apply_profile(key):
                    rk, self._lay_hold = key, False
                    self._lay_note = f" · Layout de {key[1][0]}×{key[1][1]} recuperado ✓ (ya se había detectado antes)"
                    self._emit(f"Layout: {key[1][0]}×{key[1][1]} ya conocido: se recuperan sus ROIs al instante")
            self._check_ref(grab, key)
            if auto and self._layout_needed(key):
                self._lay_want, self._lay_tries, self._lay_next = True, 0, 0.0
                if rk != key:
                    self._lay_note = (f" · Layout: la captura pasó a {key[1][0]}×{key[1][1]}; se detectan las zonas de nuevo "
                                      f"(mientras tanto se mantiene el visual actual)")
        if rk == key and not self._lay_want and not self._lay_forced and now - self._prof_t > 2.0:
            self._prof_t = now
            self._store_profile(key)                             # los ROIs vigentes (p. ej. calibrados a mano) quedan para este tamaño
        if (self._lay_want or self._lay_forced) and now >= self._lay_next:
            try:
                img = grab.full()
            except Exception:
                self._lay_next = now + 3.0                       # la ventana aún no entrega imagen
                return
            self._lay_res = None
            self._lay_img_key = (grab.backend_name, (img.shape[1], img.shape[0]))
            self._lay_thread = threading.Thread(target=self._layout_job, args=(img, list(self.cfg["decks"])), daemon=True)
            self._lay_thread.start()
            self._lay_note = " · Layout: detectando los ROIs…" + (" (se mantiene el visual actual)" if self._lay_hold else "")

    def _layout_job(self, img, names):
        try:
            self._lay_res = lay_mod.detect_layout(img, names)
        except Exception as e:                                   # Tesseract ausente, etc.
            dl.exception("layout", "falló la detección del layout")
            self._lay_res = e

    def _layout_done(self, grab, key, now):
        res, self._lay_res, self._lay_thread = self._lay_res, None, None
        forced, self._lay_forced = self._lay_forced, False
        img_key, self._lay_img_key = self._lay_img_key, None
        if isinstance(res, lay_mod.Layout):
            dl.devlog.resolve("layout")                          # la detección funcionó: el error anterior quedó solucionado
        if isinstance(res, lay_mod.Layout) and img_key != key:   # la ventana cambió de tamaño durante la detección
            self._lay_key, self._lay_want, self._lay_tries, self._lay_next = (), True, 0, 0.0
            self._lay_note = " · Layout: la ventana cambió de tamaño mientras se detectaba; se repite"
            return
        if not isinstance(res, lay_mod.Layout):
            self._lay_note = f" · ⚠ no se pudo detectar el layout: {res}"
            self._emit(self._lay_note.strip(" ·"))
            self._lay_want = False
            return
        if res.usable:
            old = self._ref_key()
            if old is not None and old != key:
                self._store_profile(old, emit=False)             # el layout del tamaño anterior se conserva
                for d, rois in self.cfg["decks"].items():        # lo que esta detección no encontró era de OTRO tamaño: no vale
                    for k in ROI_KEYS:
                        if (k == "vu" and d not in res.vu) or (k != "vu" and k not in res.decks.get(d, {})):
                            rois[k] = None
            lay_mod.apply_layout(self.cfg, res, key[0], key[1])
            self._store_profile(key, emit=False)
            self._sensors_reset = True
            self.changed.emit()
        txt = res.summary() + (" · " + " · ".join(res.notes) if res.notes else "")
        if res.complete:
            self._lay_want, self._lay_note = False, f" · Layout automático ✓ {res.summary()}"
        else:
            n = self._lay_tries
            self._lay_tries += 1
            if self._lay_want and n < len(LAYOUT_RETRY):
                self._lay_next = now + LAYOUT_RETRY[n]
                self._lay_note = f" · Layout automático (parcial; se reintenta en {LAYOUT_RETRY[n]:.0f} s): {txt}"
            else:
                held = self.cfg["capture"].get("layout", "auto") == "auto" and self._ref_key() != key
                if held:                                         # sin layout del tamaño nuevo no se lee nada: se sigue intentando
                    self._lay_want, self._lay_next = True, now + LAYOUT_RETRY[-1]
                else:
                    self._lay_want = False
                self._lay_note = (f" · Layout automático (parcial): {txt}. Carga una canción en cada deck y pulsa «Detectar layout ahora»"
                                  + (f" · mientras tanto se mantiene el visual actual (se reintenta cada {LAYOUT_RETRY[-1]:.0f} s)"
                                     if held else ""))
        self._emit("Layout: " + txt)

    def _all_rois(self):
        out = []
        for rois in self.cfg["decks"].values():
            for k in ROI_KEYS:
                if v := rois.get(k):
                    out.append(tuple(v))
                    if k in PAD:
                        out += [_padded(v, PAD[k]), _padded(v, 2 * PAD[k])]
        return out

    def _read_static(self, grab, ocr, deck):
        """Título, BPM y duración casi no cambian: el OCR tiene caché por píxeles y casi nunca llama a Tesseract."""
        rois = self.cfg["decks"][deck]

        def txt(key, kind, pad=0, tag=""):
            if not rois.get(key):
                return ""
            roi = _padded(rois[key], pad) if pad else tuple(rois[key])
            return ocr.read(f"{deck}.{key}{tag}", grab.grab(roi), kind)

        dur = parse_time(txt("duration", "time", PAD["duration"]))      # "02:52" -> 172 s
        alts = [parse_time(txt("duration", "time", 0, ".raw")), parse_time(txt("duration", "time", 2 * PAD["duration"], ".wide"))]
        return {"title": txt("title", "text"), "bpm": parse_bpm(txt("bpm", "bpm", PAD["bpm"])), "duration": dur,
                "dur_alts": [a for a in dict.fromkeys(alts) if a is not None and a != dur]}

    def _read_remaining(self, grab, ocr, deck):
        """El tiempo restante cambia cada segundo (siempre hay que hacer OCR): solo se lee cuando hace falta."""
        roi = self.cfg["decks"][deck].get("remaining")
        if not roi:
            return None
        roi = _padded(roi, PAD["remaining"])
        grab_t = getattr(grab, "grab_t", None)                  # el capturador de verdad da también la hora del fotograma
        img, self._rem_stamps[deck] = grab_t(roi) if grab_t else (grab.grab(roi), None)
        return parse_time(ocr.read(f"{deck}.remaining", img, "time"))   # "-02:52" -> 172 s

    def _dense(self, now):
        """¿Estamos en una ventana de revisiones frecuentes (inicio del video o cambio de tempo)?"""
        return now < self._dense_until

    def _interval(self, now):
        s = self.cfg["sync"]
        return s["initial_every"] if self._dense(now) else s["check_every"]

    def _wants_clock(self, now, playing):
        """¿Hay que leer el reloj del deck en cada ciclo? Sí en el pre-roll y durante las revisiones frecuentes."""
        e = self._target
        if self.forced or not e or not os.path.exists(e["video_path"]):
            return False
        return self._pre or (playing and self._deck_on is not False and self._dense(now))

    def _track_flip(self, rem, now, grab, deck=None):
        """Detecta el instante en que el reloj de Traktor pasa al segundo siguiente (solo si se leyó en el ciclo anterior
        y el restante bajó exactamente 1): con él la posición del track se conoce a ~0.1-0.2 s en vez de ±1 s.
        Con la hora real de cada fotograma (la da el capturador), el cambio ocurrió entre el último fotograma que mostraba el
        dígito viejo y el primero que muestra el nuevo; sin ella (otros capturadores) se usan las horas del ciclo."""
        self._flip = None
        if rem is None:
            return
        st = self._rem_stamps.get(deck)
        t = now if st is None else st
        if st is not None:
            if t == self._rem_seen:
                return                                                  # el mismo fotograma de antes: no hay nada nuevo que medir
            self._rem_seen = t
        prev, t_prev = self._rem_last, self._rem_t
        if rem == prev:
            self._rem_t = t                                             # el fotograma más nuevo que todavía muestra este valor
            return
        self._rem_last, self._rem_t = rem, t
        if prev is None or prev - rem != 1 or t - t_prev > 0.6:
            return
        if st is not None:
            tf = (t_prev + t) / 2
        else:
            fps = getattr(grab, "fps", 0) or 0
            age = 0.5 / fps if fps >= 1 else 0.1                          # el fotograma tiene ~medio período de antigüedad
            tf = (t_prev + t) / 2 - age
        self._flip = (tf, rem)                                          # el cambio ocurrió entre las dos lecturas
        self._flips = [x for x in self._flips if tf - x[0] <= FLIP_KEEP][-(FLIP_MAX - 1):] + [(tf, rem)]

    @staticmethod
    def _consensus(vals):
        """(mediana, cuántos concuerdan) de varias mediciones: se descartan las que difieren de la mediana más de FLIP_AGREE s
        y hace falta que concuerde la mayoría; si no, (None, n)."""
        if not vals:
            return None, 0
        m = statistics.median(vals)
        kept = [v for v in vals if abs(v - m) <= FLIP_AGREE]
        if len(kept) < len(vals) // 2 + 1:
            return None, len(vals)
        return statistics.median(kept), len(kept)

    def _flip_ref(self, dur, scale, f, now):
        """(posición del track en `now`, nº de cambios que concuerdan) según los cambios de dígito recientes; (None, n) si no hay o discrepan."""
        return self._consensus([dur - (d + DISPLAY_BIAS) * scale + (now - tf) * f for tf, d in self._flips if now - tf <= FLIP_KEEP])

    def _mini_tick(self, now, s, ok):
        """Revisiones precisas breves en la reproducción normal: cada `precise_every` s se abre una ventana de `precise_window` s en la
        que se lee el reloj en cada ciclo (el OCR tiene caché por píxeles: Tesseract solo trabaja cuando cambia un dígito) y la deriva
        se mide con la mediana de varios cambios de dígito. Sin ellas, tras los primeros 15 s solo se mediría con ±1 s."""
        every, win = s.get("precise_every") or 0, s.get("precise_window") or 0
        if not every or not win or not ok:
            return
        if self._dense(now):
            if not self._mini:
                self._mini_next = max(self._mini_next, self._dense_until + every)   # las ventanas del inicio y del tempo también cuentan
            elif self._drift_pending and now < self._mini_cap:
                self._dense_until = max(self._dense_until, now + 1.5)           # deja confirmar una deriva con otra medición
            return
        self._mini = False
        if now >= self._mini_next:
            self._mini, self._mini_cap, self._mini_next = True, now + 3 * win, now + every
            self._dense_until = now + win
            self._last_check = max(self._last_check, now - s["initial_every"])  # toca revisar ya, pero sin que salte la lectura de ±1 s

    def _needs_time(self, entry, playing, now):
        """¿Hace falta la posición del track? Solo al cargar un video o cuando toca revisar la sincronización."""
        if self.forced or entry is None:
            return False
        if not same(self.player.current, entry["video_path"]) or self._wants_clock(now, playing):
            return True                                                  # posición inicial del video / pre-roll / revisión precisa
        return playing and self._deck_on is not False and (now - self._last_check >= self._interval(now)
                                                           or self._resume_check or self._sync_now or self._drift_pending)

    def _ocr_tick(self, grab, ocr, deck, playing, now):
        for d in self.info:                                  # cabecera de cada deck (caché por píxeles: casi gratis)
            self._read_deck(grab, ocr, d)
        info = self.info[deck]
        reading = {"title": info.title, "bpm": info.bpm, "duration": info.duration}
        reading["remaining"] = self._read_remaining(grab, ocr, deck) if self._needs_time(info.entry, playing, now) else None
        if playing and self.cfg["next"].get("enabled", True):   # cuánto le queda al track que suena (para anticipar el siguiente)
            far = next_watch(self.cfg["next"]) + 30
            for d in {deck, self._ahead["frm"] if self._ahead else deck}:
                r = reading["remaining"] if d == deck else None
                if r is None:
                    v = self._rems.get(d)
                    if v and v[0] - (now - v[1]) > far and now - v[1] < 5.0:
                        continue                         # aún falta mucho: basta con mirar el reloj cada 5 s
                    r = self._read_remaining(grab, ocr, d)
                self._note_rem(d, r, now)
        self.last_reading = dict(reading, deck=deck)
        return reading

    def _read_deck(self, grab, ocr, d):
        info, raw = self.info[d], self._read_static(grab, ocr, d)
        info.title, info.duration, info.dur_alts = raw["title"], raw["duration"], raw["dur_alts"]
        info.bpm = self._smooth_bpm(info, raw["bpm"])
        info.entry, info.check = self.db.match(info.title, info.duration, self.cfg["ocr"], info.dur_alts)
        self._confirm(info)
        self._autolink(grab, ocr, d)

    def _confirm(self, info):
        """Exige N lecturas consecutivas iguales antes de cambiar de modo/video (anti-parpadeo)."""
        key = info.entry["video_path"] if info.entry else None
        if key == info.cand:
            info.cand_n += 1
        else:
            info.cand, info.cand_n = key, 1
        if info.cand_n >= self.cfg["ocr"]["confirm_reads"]:
            info.target = info.entry

    # ------------------------------------------------------------- autovinculación
    def _video_dur(self, path):
        """Duración del archivo de video (con caché por ruta y fecha: abrirlo no es gratis)."""
        try:
            key = (path, os.path.getmtime(path))
        except OSError:
            return None
        if key not in self._vdur:
            self._vdur[key] = video_duration(path)
        return self._vdur[key]

    def _read_pitch(self, grab, ocr, d):
        """Tempo (%) bajo el BPM del deck, leído con dos recortes distintos que deben coincidir: ('ok', %) · ('bad', None)
        si no se lee con seguridad · ('none', None) si el ROI no está calibrado."""
        roi = self.cfg["decks"][d].get("pitch")
        if not roi:
            return "none", None
        a = parse_pitch(ocr.read(f"{d}.pitch", grab.grab(tuple(roi)), "pitch"))
        b = parse_pitch(ocr.read(f"{d}.pitch.pad", grab.grab(_padded(roi, PAD["pitch"])), "pitch"))
        return ("ok", a) if a is not None and a == b else ("bad", None)

    def _auto_say(self, d, msg):
        if msg and self._auto_last.get(d) != msg:
            self._auto_last[d] = msg
            self._auto_text = f"Deck {d}: {msg}"
            self._emit(f"Autovinculación · Deck {d}: {msg}")

    def _autolink(self, grab, ocr, d):
        """Track cargado en Traktor Pro 4 sin visual en ViDeck Pulse: si su título se parece (≥ `autolink.min_title`) al
        nombre de un video sin vincular y la duración del video coincide con la del track, se crea la entrada solo
        (título tal como lo muestra Traktor, BPM base y duración). Si el título coincide pero la duración no, se avisa
        y no se vincula. Se puede corregir a mano en Configuración."""
        cfg, info = self.cfg.get("autolink", {}), self.info[d]
        if (not cfg.get("enabled", True) or self.autolink_hold or info.entry is not None or not info.title
                or info.duration is None or not info.check.startswith("título sin coincidencia")):
            info.al_key, info.al_n, info.al_bad = None, 0, 0
            return
        key = (normalize(info.title), info.duration)
        info.al_n = info.al_n + 1 if key == info.al_key else 1
        if key != info.al_key:
            info.al_bad = 0
        info.al_key = key
        if info.al_n < max(3, self.cfg["ocr"]["confirm_reads"] + 1):    # el mismo track leído igual durante unos segundos
            return
        cands = al.candidates(info.title, self.videos, self.db, float(cfg.get("min_title", 0.70)))[:4]
        if not cands:
            return
        durs = {v: self._video_dur(v) for _, v in cands}
        video, ratio, vdur, msg = al.decide(info.title, [info.duration, *info.dur_alts], cands, durs,
                                            self.cfg["ocr"]["duration_tolerance"])
        if video is None:
            self._auto_say(d, msg)
            return
        fill = bool(cfg.get("fill_bpm", False))
        if fill and not info.bpm:                                        # BPM aún sin confirmar por el filtro
            return
        state, pitch = self._read_pitch(grab, ocr, d)
        if fill and state == "bad":
            info.al_bad += 1
            if info.al_bad >= 8:
                self._auto_say(d, f"«{info.title}» ↔ {os.path.basename(video)}: no se pudo leer el tempo (%) del deck "
                                  f"para calcular el BPM base; vincúlalo a mano en Configuración")
            return
        base = al.base_bpm(info.bpm, pitch) if state == "ok" else info.bpm   # BPM del track con el tempo en 0 %
        if base is not None and not 40 <= base <= 250:
            base = None
        if fill and not base:
            return
        info.al_bad = 0
        # Por defecto el BPM base queda PENDIENTE (None): si el fader de tempo está movido o SYNC activo, el BPM que muestra
        # Traktor no es el del track y daría una velocidad equivocada. Hasta que se escriba en Configuración, el visual va a ×1.
        entry = {"traktor_track_name": info.title, "video_path": video,
                 "base_bpm": round(float(base), 2) if fill else None, "duration": fmt_time(info.duration)}
        self.db.tracks.append(entry)
        try:
            self.db.save()
        except OSError as e:
            self._emit(f"Autovinculación: no se pudo guardar tracks_db.json ({e}); el vínculo vale hasta cerrar el programa")
        info.entry, info.check = self.db.match(info.title, info.duration, self.cfg["ocr"], info.dur_alts)
        self._confirm(info)
        shown = f"Traktor Pro 4 muestra {info.bpm:.2f}" if info.bpm else "BPM de Traktor Pro 4 sin leer"
        if state == "ok" and info.bpm and abs(pitch) >= 0.05:
            shown += f" con tempo {pitch:+.1f} % (≈ {base:.2f} con el tempo en 0 %)"
        elif state == "ok" and info.bpm:
            shown += " con el tempo en 0 %"
        elif info.bpm:
            shown += " (tempo sin verificar)"
        bpm_txt = (f"BPM base {entry['base_bpm']:.2f} ({shown})" if fill
                   else f"⏳ BPM Base Pendiente: escríbelo en Configuración ({shown})")
        text = (f"✨ Autovinculado: «{info.title}» ↔ {os.path.basename(video)} (título {ratio:.0%}, duración "
                f"{fmt_time(info.duration)} ≈ video {fmt_time(vdur)}) · {bpm_txt}")
        self._auto_text = f"Deck {d}: {text}"
        self._emit(text)
        self.changed.emit()

    def _smooth_bpm(self, info, bpm):
        """Filtro anti-lecturas erróneas del BPM: un cambio pequeño (mover el pitch) se acepta al instante; un salto grande
        (un dígito mal leído por el OCR, un track nuevo o el BPM que cambia al activar Sync) solo se acepta si la
        lectura siguiente lo confirma. Cada deck lleva su propio filtro, que se reinicia al cambiar de track."""
        tol = self.cfg["sync"].get("bpm_tol", 0.015)
        if info.title != info.bpm_key:
            info.bpm_key, info.bpm_ok, info.bpm_pend = info.title, None, None
        if not bpm:
            return info.bpm_ok
        near = lambda ref: ref is not None and abs(bpm / ref - 1) <= tol
        if near(info.bpm_ok) or near(info.bpm_pend):
            info.bpm_ok, info.bpm_pend = bpm, None
        else:
            info.bpm_pend = bpm
        return info.bpm_ok

    def _deck_line(self, d, on):
        """Resumen de un deck para el panel: track leído, visual validado y velocidad que tendría su video."""
        i, s = self.info[d], self.cfg["sync"]
        head = PLAY if on else (PAUSE if on is not None else "·")
        if not i.title:
            return f"{head} sin track cargado ({i.check})"
        if i.target and i.target is i.entry:
            if not i.target.get("base_bpm"):
                f = "×1 (⏳ BPM Base Pendiente en Configuración)"
            else:
                f = f"×{max(s['min_speed'], min(s['max_speed'], i.bpm / i.target['base_bpm'])):.3f}" \
                    if i.bpm else "×? (BPM sin confirmar)"
            bpm = f"{i.bpm:.2f}" if i.bpm else "?"
            return f"{head} «{i.title}» → {os.path.basename(i.target['video_path'])} ✓ · {bpm} BPM → {f}"
        return f"{head} «{i.title}» → Random Visuals ({i.check})"

    def _factor(self, reading, e):
        bpm, s = reading.get("bpm"), self.cfg["sync"]
        if not bpm or not e.get("base_bpm"):
            return self._speed
        return max(s["min_speed"], min(s["max_speed"], bpm / e["base_bpm"]))   # Factor = BPM_actual / BPM_base

    def _speed_text(self, reading):
        """«×1.040 = 133.12 / 128.00 BPM» para el panel (indica si el factor quedó limitado)."""
        e, bpm = self._target, reading.get("bpm")
        if self.mode == "Sincronizado" and e and not e.get("base_bpm"):
            return f"×{self._speed:.4f} (⏳ BPM Base Pendiente: escríbelo en Configuración)"
        if self.mode != "Sincronizado" or not e or not bpm or not e.get("base_bpm"):
            return f"×{self._speed:.4f}" + (" (sin BPM base: ×1)" if self.mode != "Sincronizado" else "")
        raw, s = bpm / e["base_bpm"], self.cfg["sync"]
        lim = " — limitada" if not s["min_speed"] <= raw <= s["max_speed"] else ""
        return f"×{self._speed:.4f}  =  {bpm:.2f} (Traktor Pro 4) / {float(e['base_bpm']):.2f} (ViDeck Pulse){lim}"

    def _set_speed(self, f, now):
        """La velocidad sigue de cerca el BPM de Traktor (con sus 2 decimales): se aplica con cambios desde 0.03 %. Las
        revisiones frecuentes solo se abren cuando el tempo se ha movido de verdad (> 0.3 % desde la última vez). Al factor del
        tempo se le suma el ajuste fino (`_trim`, ver `_trim_step`) cuando el video se desvía de forma sostenida."""
        changed = abs(f - self._speed) > 0.0003
        if changed:
            self._speed = f
        want = self._speed * (1.0 + self._trim + self._slew_now(now))
        if changed or abs(want - self._applied) > 1e-6:
            self.player.set_speed(want)
            self._applied = want
        if self._first_speed or self._pre:
            self._tempo_ref = f
        elif abs(f - self._tempo_ref) > 0.003:                           # cambio de tempo durante la reproducción
            old, self._tempo_ref = self._tempo_ref, f
            s = self.cfg["sync"]
            self._flips.clear()                                          # lo medido con el tempo anterior ya no vale
            self._hist.clear()                                           # (ni la deriva medida: cambió la velocidad)
            self._dense_until = max(self._dense_until, now + s["tempo_window"])
            self._sync_info = f"cambio de tempo (×{f:.3f}): se revisa cada {s['initial_every']:g} s durante {s['tempo_window']:g} s"
            self._slog("tempo", [f"×{old:.4f} → ×{f:.4f}", f"revisión cada {s['initial_every']:g} s durante {s['tempo_window']:g} s"])
        self._first_speed = False

    # ---------------------------------------------------------------- registro de sincronía, salto y ajuste fino
    def _slog(self, kind, parts=(), action="", video=None):
        """Una línea en el registro de sincronía (core/synclog.py): qué deck, qué video y qué pasó."""
        cur = video if video is not None else getattr(self.player, "current", None)
        synclog.add(self._cur_deck, cur, kind, parts, action)

    def _lead_for(self, path):
        """Adelanto del salto (s): lo que tarda mpv en saltar. Es el configurado, o el que se aprendió midiendo los saltos de ESE video
        (uno con keyframes muy espaciados tarda mucho más en saltar que uno preparado)."""
        base = float(self.cfg["sync"]["seek_lead"])
        if not self.cfg["sync"].get("adaptive_lead", True):
            return base
        v = self._lead.get(str(path))
        if v is not None:
            return v
        return self._prior(self._lead, base, LEAD_MIN, LEAD_MAX)       # video sin medidas: lo que tardaron los saltos de los demás

    @staticmethod
    def _prior(learned, default, lo, hi):
        """Valor de partida para un video nuevo: la mediana de lo aprendido con los otros (todos los videos corren en la misma máquina, con
        el mismo mpv y la misma GPU, así que su adelanto de salto y su atraso de arranque se parecen); sin nada aprendido, `default`."""
        vals = [v for v in learned.values() if isinstance(v, (int, float))]
        return max(lo, min(hi, statistics.median(vals))) if vals else default

    def _lc_key(self, path):
        return f"{self._load_kind}|{path}"

    def _load_comp_for(self, path):
        """Lo que se adelanta el punto de arranque de un video recién cargado (s): el video siempre arranca algo atrasado (la lectura
        del reloj ya tiene su edad y mpv tarda en empezar a avanzar). Se aprende midiendo la 1.ª revisión de ese mismo video, por separado
        según cómo entró (`_load_kind`): un deck que acaba de arrancar se detecta ~2 s tarde (≈1.5 s de atraso), mientras que el visual
        que entra por anticipación se lee de cerca (≈0.1 s)."""
        if not self.cfg["sync"].get("adaptive_load", True):
            return 0.0
        v = self._load_comp.get(self._lc_key(path))
        if v is not None:
            return v
        pre = f"{self._load_kind}|"
        return self._prior({k: x for k, x in self._load_comp.items() if k.startswith(pre)}, 0.0, 0.0, LOAD_COMP_MAX)

    def _learn_load(self, path, drift):
        """1.ª revisión tras cargar `path`: si el video arrancó atrasado (adelantado), la próxima vez arranca más adelante (atrás)."""
        if not self.cfg["sync"].get("adaptive_load", True) or abs(drift) <= LOAD_COMP_MIN:
            return
        old = self._load_comp_for(path)                  # lo que se aplicó en esa carga (el aprendido de ese video o el de partida)
        new = max(0.0, min(LOAD_COMP_MAX, old - LOAD_COMP_GAIN * drift))
        self._load_comp[self._lc_key(path)] = new
        self._slog("carga", [f"Δ {signed(drift)} s en la 1.ª revisión"], f"aprende la compensación de arranque: {new:.2f} s", video=path)
        self._save_learned()

    # lo aprendido se guarda por video: en el archivo la clave lleva la ruta RELATIVA a la carpeta de datos (si está dentro de ella); en memoria, absoluta
    def _learned_path(self, p):
        full = paths.resolve(p)
        if isinstance(full, str) and not os.path.exists(full):                    # el video ya no está ahí (carpeta movida): ¿está en la carpeta de videos actual?
            full = paths.find_moved(p, [self.cfg.get("videos_dir", "")]) or full
        return full

    def _learned_key(self, k):
        kind, sep, p = k.partition("|")
        return kind + sep + self._learned_path(p) if sep else self._learned_path(k)

    @staticmethod
    def _disk_key(k):
        kind, sep, p = k.partition("|")
        return kind + sep + paths.store(p) if sep else paths.store(k)

    def _load_learned(self):
        """Lee `sync_learned.json` (lo aprendido por video en sesiones anteriores); un archivo ausente, roto o con valores raros se ignora."""
        if not self.cfg["sync"].get("persist_learned", True):
            return
        try:
            with open(LEARNED_FILE, encoding="utf-8") as f:
                d = json.load(f)
            lead = {str(k): max(LEAD_MIN, min(LEAD_MAX, float(v))) for k, v in dict(d.get("lead", {})).items() if isinstance(v, (int, float))}
            comp = {str(k): max(0.0, min(LOAD_COMP_MAX, float(v))) for k, v in dict(d.get("load_comp", {})).items()
                    if isinstance(v, (int, float)) and str(k).split("|", 1)[0] in ("start", "ahead")}
        except Exception:
            return
        self._lead.update({self._learned_path(k): v for k, v in lead.items()})
        self._load_comp.update({self._learned_key(k): v for k, v in comp.items()})

    def _save_learned(self):
        """Guarda lo aprendido (archivo pequeño, escritura atómica); un fallo de escritura no afecta a la sincronía."""
        if not self.cfg["sync"].get("persist_learned", True):
            return
        try:
            tmp = LEARNED_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "lead": {paths.store(k): v for k, v in self._lead.items()},
                           "load_comp": {self._disk_key(k): v for k, v in self._load_comp.items()}}, f, indent=1, sort_keys=True)
            os.replace(tmp, LEARNED_FILE)
        except Exception:
            pass

    def _seek(self, p, target, ref, lead, t):
        """Salta a `target` y deja anotado el salto para medir cuánto tarda (`_landing_tick`)."""
        p.seek(target)
        self._hist.clear()
        self._slew, self._slew_sum = (0.0, 0.0, 0.0), 0.0   # tras un salto la deriva se mide de nuevo
        self._prec = None
        self._lead_probe = (p.current, lead, t)
        self._landing = {"t": t, "target": target, "lead": lead, "speed": self._speed, "path": p.current, "seen": False}

    def _landing_tick(self, p):
        """Mide cuánto tarda el salto en terminar y, con eso, aprende el adelanto de ese video (si no, tras cada salto el video queda
        atrasado lo que tardó el salto). Con mpv se usa su propiedad «seeking»; sin ella, la posición dice cuándo ya sigue de corrido."""
        L = self._landing
        if not L:
            return
        if not same(p.current, L["path"]):
            self._landing = None
            return
        dt = time.time() - L["t"]
        if dt < 0.05:
            return
        fn = getattr(p, "seeking", None)
        sk = fn() if callable(fn) else None
        took, exact = None, sk is not None
        if exact:
            if sk:
                L["seen"] = True
            elif L["seen"] or dt >= LANDING_MIN_S:
                took = dt
        else:
            pos = p.position()
            if pos is not None and pos >= L["target"] - 0.02 and pos - L["target"] < 2.5:
                took = max(0.0, dt - (pos - L["target"]) / max(L["speed"], 0.1))
        if took is None:
            if dt > LANDING_MAX_S:
                self._landing = None
                self._learn_lead(L, LANDING_MAX_S, True)
                self._slog("aterrizaje", [f"el salto lleva más de {LANDING_MAX_S:g} s sin terminar"], video=L["path"])
            return
        self._landing = None
        self._learn_lead(L, took, exact and L["seen"])
        new = self._lead_for(L["path"])
        self._slog("aterrizaje", [f"el salto tardó {took:.2f} s", f"adelanto {L['lead']:.2f} s → {new:.2f} s"], video=L["path"])

    def _learn_lead(self, L, took, precise):
        """Acerca el adelanto de ese video a lo que tardó el salto: sube deprisa (un salto lento se nota) y baja despacio (y solo
        si la medida es fina: con un salto más corto que un ciclo del motor no se sabe cuánto tardó en realidad)."""
        if not self.cfg["sync"].get("adaptive_lead", True):
            return
        lead = L["lead"]
        if took > lead + 0.05:
            new = lead + LEAD_UP * (took - lead)
        elif precise and took < lead - 0.1:
            new = lead - LEAD_DOWN * (lead - took)
        else:
            return
        self._lead[str(L["path"])] = max(LEAD_MIN, min(LEAD_MAX, new))
        self._save_learned()

    def _jump_step(self, raw, f, now, p, running, s):
        """¿La posición del track dio un salto desde la lectura anterior (se movió a otro sector de la canción)? Un OCR que lee mal el reloj
        también parecería un salto, así que se confirma con la lectura siguiente (que se pide ya: el reloj se lee en cada ciclo un momento).
        Confirmado: el video ya no corresponde; se descartan las mediciones y ajustes anteriores, se abre una ventana de revisiones precisas
        (la captura sube a 15 fps) y la primera corrección salta sin esperar confirmación."""
        base, cand = self._jump, self._jump_cand
        new = (raw, now, p.current, f)
        if not running or self._pre or base is None or not same(base[2], p.current) or not 0 < now - base[1] <= JUMP_MAX_GAP_S:
            self._jump, self._jump_cand = (new if running and not self._pre else None), None
            return
        moved = raw - (base[0] + (now - base[1]) * base[3])    # lo que se movió de más (+ adelante, − atrás) respecto de lo que corresponde al tiempo pasado
        if abs(moved) <= JUMP_TOL:                             # el track sigue donde se esperaba (una lectura rara anterior se descarta)
            self._jump, self._jump_cand = new, None
            return
        if cand is not None and same(cand[2], p.current) and 0 < now - cand[1] <= JUMP_MAX_GAP_S \
                and abs(raw - (cand[0] + (now - cand[1]) * cand[3])) <= JUMP_TOL:
            self._jump, self._jump_cand = new, None            # la nueva posición se mantiene: es un salto de verdad
            self._hist.clear()
            self._slew, self._slew_sum, self._prec = (0.0, 0.0, 0.0), 0.0, None
            self._flips.clear()
            self._drift_pending, self._resume_check, self._fast_fix = False, True, True
            self._dense_until = max(self._dense_until, now + s["initial_window"])
            self._last_check = now - s["initial_every"]
            self._sync_info = f"salto de posición en Traktor ({signed(moved)} s): se vuelve a sincronizar"
            self._slog("salto en Traktor", [f"la posición del track cambió {signed(moved)} s"], "se revisa ya con medición precisa")
            return
        self._jump_cand = new                                  # posible salto: se confirma con la lectura siguiente
        self._dense_until = max(self._dense_until, now + 2.0)  # (el reloj se lee en cada ciclo un momento)

    def _fix_lead(self, lp, drift, ahead):
        """El salto de `lp` aterrizó con una Δ medida con precisión: el adelanto de ese video se corrige hacia lo que habría hecho falta."""
        if not self.cfg["sync"].get("adaptive_lead", True) or not (LEAD_FIX_MIN <= abs(drift) <= LEAD_FIX_MAX):
            return
        path, old = lp[0], lp[1]
        new = max(LEAD_MIN, min(LEAD_MAX, old - LEAD_FIX_GAIN * drift))
        self._lead[str(path)] = new
        self._save_learned()
        self._slog("adelanto", [f"Δ {signed(drift + ahead)} s tras el salto"], f"aprende el adelanto del salto: {old:.2f} s → {new:.2f} s", video=path)

    def _slew_now(self, now):
        """Fracción de velocidad del ajuste suave en curso (0 si no hay o ya terminó; al terminar se anota lo que desplazó)."""
        rate, until, since = self._slew
        if rate and now >= until:
            self._slew_sum += rate * (until - since) * self._speed         # s de video que ese ajuste adelantó (+) o atrasó (−)
            self._slew = (0.0, 0.0, 0.0)
            return 0.0
        return rate

    def _slew_step(self, drift, now):
        """Deriva pequeña y constante (más chica que el umbral que hace saltar): se corrige SIN saltar, frenando o acelerando el video un
        2 % el rato justo para absorberla (un video adelantado 0.1 s se frena 5 s). Así un video que se queda unas décimas adelantado
        o atrasado —muy notable en las letras— vuelve a Sincronía sin tirones. `sync.slew` lo desactiva. Para el ajuste fino de velocidad
        la deriva se mide «sin» estos ajustes (`_trim_drift`), así una deriva sostenida se sigue aprendiendo."""
        if abs(drift) < SLEW_MIN or not self.cfg["sync"].get("slew", True):
            return
        dur = min(abs(drift) / SLEW_RATE, SLEW_MAX_S)
        self._slew = (-SLEW_RATE if drift > 0 else SLEW_RATE, now + dur, now)
        self._slog("ajuste suave", [f"Δ {signed(drift)} s", f"{SLEW_RATE * 100:g} % · {dur:.1f} s"], "el video se frena" if drift > 0 else "el video se acelera")

    def _trim_drift(self, t, drift, now):
        """Anota la deriva medida para el ajuste fino de velocidad, descontando lo que ya corrigieron los ajustes suaves (con uno en curso
        la medida no es del video: no se anota)."""
        if not self._slew_now(now):
            self._trim_step(t, drift - self._slew_sum)

    def _trim_step(self, t, drift):
        """Ajuste fino de velocidad: si las revisiones precisas muestran una deriva SOSTENIDA (el video va siempre un poco más lento
        o más rápido que el track), en vez de saltar cada vez se corrige la velocidad un poco (hasta ±2 %). Hace falta una recta clara
        (varias revisiones, un rato y bien alineadas); una deriva puntual (un tirón, un salto) no la mueve."""
        if not self.cfg["sync"].get("trim", True):
            return
        h = self._hist
        h.append((t, drift))
        del h[:-8]
        if len(h) < TRIM_MIN_PTS or h[-1][0] - h[0][0] < TRIM_MIN_SPAN:
            return
        n = len(h)
        mt, md = sum(x for x, _ in h) / n, sum(y for _, y in h) / n
        sxx = sum((x - mt) ** 2 for x, _ in h)
        syy = sum((y - md) ** 2 for _, y in h)
        sxy = sum((x - mt) * (y - md) for x, y in h)
        if sxx <= 0 or syy <= 1e-9:
            return
        slope, r2 = sxy / sxx, (sxy * sxy) / (sxx * syy)
        if abs(slope) < TRIM_MIN_SLOPE or r2 < TRIM_MIN_R2 or abs(slope) * (h[-1][0] - h[0][0]) < TRIM_MIN_RISE:
            return
        old = self._trim
        self._trim = max(-TRIM_MAX, min(TRIM_MAX, old - slope / max(self._speed, 0.1)))
        self._hist.clear()
        if self._trim != old:
            self._slog("ajuste fino", [f"deriva sostenida {slope * 60:+.2f} s/min", f"velocidad {self._trim * 100:+.2f} %"],
                       "el video se acelera" if slope < 0 else "el video se frena")

    def _check_sync(self, p, ref0, t_ref, f, now, precise):
        """Compara la posición del video con la del track en Traktor y corrige la deriva confirmada. La posición del track
        era `ref0` en el instante `t_ref` y avanza al ritmo `f`. `precise`: se midió en el cambio de dígitos del reloj
        (≈0.2 s) y se usa el umbral fino. Cada revisión queda en el registro de sincronía."""
        sync, manual = self.cfg["sync"], self._sync_now
        fast, self._fast_fix = self._fast_fix, False        # el deck acaba de arrancar: se corrige sin esperar confirmación
        ahead = float(sync.get("ahead", 0.0))                 # el video se deja esto por delante (ya va incluido en `ref0`): la Δ que se muestra lo suma
        t = time.time()
        pos = p.position()
        if pos is None:                                 # mpv aún no entrega posición: se reintenta en la próxima lectura
            self._fast_fix = fast
            self._sync_info = "esperando la posición del video (mpv no la entrega)…"
            return
        ref = ref0 + (t - t_ref) * f                    # posición del track en el instante en que se leyó la del video
        self._last_check, self._resume_check, self._sync_now = now, False, False
        drift = pos - ref
        thr = sync["fine_drift"] if precise else sync["resync_drift"]
        probe, self._load_probe = self._load_probe, None
        load_fix = False
        if probe is not None and not manual and precise and same(probe, p.current) and abs(drift) <= LOAD_COMP_MAX_DRIFT:
            self._learn_load(probe, drift)               # 1.ª revisión (precisa) del video recién cargado: aprende cuánto arranca atrasado
            load_fix = abs(drift) > thr and bool(sync.get("adaptive_load", True))   # y lo que arrancó mal se corrige ya, sin confirmar
        how = "preciso" if precise else "±1 s"
        if precise:
            self._prec = (t, drift, p.current)
        lp = self._lead_probe
        if lp is not None and (not same(lp[0], p.current) or t - lp[2] > 30.0):
            self._lead_probe = lp = None
        if lp is not None and precise and not manual and self._landing is None and t - lp[2] >= LEAD_FIX_WAIT_S:
            self._lead_probe = None
            self._fix_lead(lp, drift, ahead)                  # 1.ª medición precisa tras el salto: dice cuánto se pasó (o se quedó corto) el adelanto
        lead = self._lead_for(p.current)
        target = max(0.0, ref + lead * self._speed)
        data = [f"video {pos:.2f} s", f"track {ref:.2f} s", f"Δ {signed(drift + ahead)} s", how, f"tempo ×{self._speed:.4f}", f"umbral {thr:g} s"]
        if self._trim:
            data.append(f"ajuste de velocidad {self._trim * 100:+.2f} %")
        if manual:
            self._seek(p, target, ref, lead, t)
            self._drift_pending, self._sync_info = False, f"sincronizado manualmente ({drift + ahead:+.2f} s)"
            self._slog("revisión", data, f"sincronización manual: salta a {target:.2f} s (adelanto {lead:.2f} s)")
        elif abs(drift) <= thr and not load_fix:
            shown, shown_how, pr = drift + ahead, how, self._prec
            if not precise and pr is not None and same(pr[2], p.current) and t - pr[0] <= PREC_KEEP_S:
                if abs(drift - pr[1]) <= COARSE_NOISE:
                    shown, shown_how = pr[1] + ahead, "última medición precisa"
                elif t - pr[0] >= PREC_RECHECK_S:
                    self._mini_next = 0.0                    # la lectura se aparta más de lo que explica el redondeo: se mide con precisión ya
            self._drift_pending, self._sync_info = False, f"En Sincronía (Δ {shown:+.2f} s, {shown_how})"
            self._slog("revisión", data, "dentro del umbral")
            if precise:
                active = self._slew_now(now)
                self._trim_drift(t, drift, now)
                if not active:
                    self._slew_step(drift, now)
        elif fast or load_fix or (self._drift_pending and drift * self._drift_prev > 0):   # deck recién arrancado, o 2.ª lectura con la misma deriva
            self._drift_pending = False
            if sync["auto_resync"]:
                self._seek(p, target, ref, lead, t)
                self._sync_info = f"corregido ({drift + ahead:+.2f} s, {how})"
                self._slog("revisión", data + (["deck recién arrancado"] if fast else ["video recién cargado"] if load_fix else []), f"corrige: salta a {target:.2f} s (adelanto {lead:.2f} s)")
            else:
                self._sync_info = f"deriva {drift + ahead:+.2f} s (corrección automática desactivada)"
                self._slog("revisión", data, "corrección automática desactivada")
        else:
            self._drift_pending, self._drift_prev = True, drift
            self._sync_info = f"deriva {drift + ahead:+.2f} s, confirmando…"
            self._slog("revisión", data, "confirmando la deriva")
            if precise:
                self._trim_drift(t, drift, now)

    def _pre_step(self, raw, dur, f, scale, now, p):
        """Pre-roll: Traktor muestra un restante MAYOR que la duración (la cabeza está antes del inicio del track). El video
        queda cargado y en espera (pantalla en negro) y arranca cuando el restante coincide con la duración total."""
        s = self.cfg["sync"]
        if self._pre and self._flip is not None:        # cuándo llegará el track a su inicio, medido en el cambio de dígitos
            ff = max(f, 0.1)
            ests = [tf + max(0.0, -(dur - (d + DISPLAY_BIAS) * scale)) / ff for tf, d in self._flips if now - tf <= FLIP_KEEP]
            t_fl, d = self._flip
            m, _ = self._consensus(ests)
            self._start_at = m if m is not None else t_fl + max(0.0, -(dur - (d + DISPLAY_BIAS) * scale)) / ff
        before = raw < -s["preroll_tol"]
        holding = self._pre and self._start_at is not None and now < self._start_at
        if before:
            self._pre_n += 1
            if not self._pre and (not self._released or self._pre_n >= 2):   # (en marcha: se pide confirmación, el OCR puede fallar)
                self._pre, self._start_at, self._drift_pending = True, None, False
                p.seek(0.0)
                self._slog("pre-roll", [f"el track aún no empezó (restante {-raw:.1f} s antes del inicio)"], "video en espera")
        else:
            self._pre_n = 0
            if self._pre and not holding:
                self._pre, self._start_at, self._released = False, None, True
                self._dense_until = max(self._dense_until, now + s["initial_window"])
                self._last_check, self._drift_pending = now, False
                self._sync_info = "el track empezó: video en marcha"
                self._slog("pre-roll", ["el track empezó"], "video en marcha")

    def _pre_text(self, raw, f, now, playing):
        if self._start_at is not None:
            left = max(0.0, self._start_at - now)
        else:
            left = max(0.0, -(raw or 0.0)) / max(f, 0.1)
        return (f"preparándose: el track empieza en {left:.0f} s, el video espera en negro"
                + ("" if playing else " (deck en pausa antes del inicio)"))

    def _duration_for(self, e, reading):
        """Duración del track para calcular su posición: la que muestra Traktor; si el OCR la leyó mal (el doble check la dio
        por buena con otra lectura) se usa la guardada en ViDeck Pulse."""
        dur, jd = reading.get("duration"), parse_duration(e.get("duration"))
        if jd is not None and (dur is None or abs(dur - jd) > self.cfg["ocr"]["duration_tolerance"]):
            return jd
        return dur

    # ----------------------------------------------- anticipación del visual del siguiente deck
    def _note_rem(self, d, v, now):
        if v is not None:
            self._rems[d] = (float(v), now)

    def _rem_est(self, d, now, actives):
        """Segundos que le quedan al track del deck `d`: última lectura del reloj, descontando lo transcurrido si suena."""
        v = self._rems.get(d)
        if v is None or now - v[1] > 4.0:
            return None
        rem, t = v
        return max(0.0, rem - (now - t)) if actives.get(d) else rem

    def _pos_of(self, d, now, actives):
        """Posición del track del deck `d` (< 0: la cabeza aún está antes del inicio)."""
        i, r = self.info[d], self._rem_est(d, now, actives)
        dur = i.duration if i.duration is not None else next(iter(i.dur_alts), None)
        if i.entry is not None and parse_duration(i.entry.get("duration")) is not None:
            jd = parse_duration(i.entry["duration"])
            dur = jd if dur is None or abs(dur - jd) > self.cfg["ocr"]["duration_tolerance"] else dur
        return None if r is None or dur is None else dur - r

    def _next_candidate(self, cur, actives):
        """El deck que viene después: otro deck con un track cargado (el que ya suena, si hay varios)."""
        def finished(d):                                   # su track ya llegó al final (restante ≈ 0 y detenido)
            v = self._rems.get(d)
            return bool(v) and v[0] <= 1.0 and not actives.get(d)

        cands = [d for d, i in self.info.items() if d != cur and i.title and not finished(d)]
        cands.sort(key=lambda d: not actives.get(d))
        return cands[0] if cands else None

    def _no_next_reason(self, cur, actives):
        """Por qué no hay un deck siguiente: no es lo mismo que el otro deck no tenga ningún track, que su track ya haya terminado o
        que su título aún no se haya leído (el aviso dice cuál de los tres es)."""
        others = [d for d in self.info if d != cur]
        if not others:
            return "no hay otro deck"
        titled = [d for d in others if self.info[d].title]
        if not titled:
            return "el otro deck no tiene un track cargado (o su título aún no se lee)"
        return "el track del otro deck ya terminó"

    def _next_kind(self, d):
        """«video»: su visual está en la lista (doble check confirmado) y el archivo existe · «random»: tiene un track cargado
        pero no hay visual disponible · «verifying»: el doble check aún se está confirmando."""
        i = self.info[d]
        if i.entry is None:
            return "verifying" if i.target is not None else "random"
        if not os.path.exists(i.entry["video_path"]):
            return "random"
        return "video" if i.target is i.entry else "verifying"

    def _watch_next(self, cur, R, now, grab, ocr):
        """Comprueba el deck en espera: su cabecera (título/duración → doble check) y su reloj. Con frecuencia creciente:
        cada `far_every` s hasta los `mid_window` s, cada `mid_every` s hasta los `fast_window` s y en cada ciclo después."""
        s = self.cfg["next"]
        every = 0.0 if R <= s["fast_window"] else (s["mid_every"] if R <= s["mid_window"] else s["far_every"])
        for d in self.info:
            if d != cur and now - self._next_t.get(d, 0.0) >= every:
                self._next_t[d] = now
                self._read_deck(grab, ocr, d)
                self._note_rem(d, self._read_remaining(grab, ocr, d), now)

    def _next_msg(self, cur, nxt, kind, R, a, started, waiting, actives):
        s, i = self.cfg["next"], self.info[nxt]
        if R <= s["fast_window"]:
            chk = "comprobación continua"
        else:
            chk = f"comprobación cada {(s['mid_every'] if R <= s['mid_window'] else s['far_every']):g} s"
        if kind == "video":
            what = f"{os.path.basename(i.entry['video_path'])} ✓"
        elif kind == "random":
            what = f"sin visual disponible ({i.check}) → Random Visuals"
        else:
            what = "verificando el visual…"
        if a:
            when = "su visual ya entró" + ("" if started else " (sincroniza al arrancar)")
        elif waiting:
            when = waiting
        else:
            when = f"entra cuando queden {s['lead']:g} s (en {max(0.0, R - s['lead']):.0f} s)"
        return (f"Deck {cur} termina en {R:.0f} s → Deck {nxt} «{i.title}»: {what} · "
                f"{'ya suena' if started else 'en espera (aún no empezó)'} · {when} · {chk}")

    def _lookahead(self, picked, actives, playing, now, grab, ocr):
        """Con el track actual por terminar vigila el otro deck: ¿hay un track cargado?, ¿su visual está en la lista?, ¿ya
        arrancó? Cuando quedan `lead` s (y el deck siguiente no está antes de su inicio) entra su visual —o un Random si no
        hay video—, en vez de esperar a que el actual se detenga. Devuelve el deck cuyo track manda el visual."""
        s = self.cfg["next"]
        if picked is None or self.forced or not s.get("enabled", True) or len(self.info) < 2:
            self._ahead, self._next_text = None, ""
            return picked
        a = self._ahead
        if a and picked != a["frm"]:                 # el flujo normal ya llegó al otro deck (o cambió el deck de origen)
            a = self._ahead = None
        cur, lead, watch = picked, float(s["lead"]), next_watch(s)
        R = self._rem_est(cur, now, actives)
        if playing and a and (R is None or R > max(watch, lead + 8) or self.info[cur].title != a["title"]):
            a = self._ahead = None                   # rebobinaron el track o cargaron otro: se vuelve a evaluar
        if R is None or R > watch:
            self._next_text = ""
            return a["to"] if a and a["kind"] == "video" else cur
        nxt = self._next_candidate(cur, actives)
        if nxt is None:
            self._ahead = None
            self._next_text = f"Deck {cur} termina en {R:.0f} s · {self._no_next_reason(cur, actives)}"
            return cur
        self._watch_next(cur, R, now, grab, ocr)
        kind = self._next_kind(nxt)
        if a and (a["to"] != nxt or (kind != "verifying" and a["kind"] != kind)):
            a = self._ahead = None                   # cambió lo que hay en el deck siguiente: se reevalúa
        pos, waiting = self._pos_of(nxt, now, actives), ""
        if not a and playing and R <= lead and kind != "verifying":
            if kind == "video" and pos is not None and pos < 0.0:
                waiting = "su track aún no empieza (pre-roll): entra al empezar"
            else:
                a = self._ahead = {"frm": cur, "to": nxt, "kind": kind, "title": self.info[cur].title}
        self._next_text = self._next_msg(cur, nxt, kind, R, a, bool(actives.get(nxt)), waiting, actives)
        return a["to"] if a and a["kind"] == "video" else cur

    def _edge(self, deck, now):
        """El deck que manda el visual pasó de espera a sonar (p. ej. el siguiente track ya entrado): se sincroniza ya."""
        on = self._deck_on
        if deck == self._drv_deck and on and self._drv_on is False:
            self._resume_check = self._fast_fix = True
            self._dense_until = max(self._dense_until, now + self.cfg["sync"]["initial_window"])
        self._drv_deck, self._drv_on = deck, on

    def _apply(self, playing, reading, fresh, now):
        p, cfg = self.player, self.cfg

        if self.forced:                                              # override manual
            self.mode = "Manual"
            if not same(p.current, self.forced):
                p.load(self.forced, loop=True, kind="manual")
                self._speed, self._applied, self._trim, self._hist, self._landing, self._slew, self._slew_sum = 1.0, 1.0, 0.0, [], None, (0.0, 0.0, 0.0), 0.0
            self._pre = False
            p.set_playing(True)
            return

        ahead_rand = bool(self._ahead and self._ahead["kind"] == "random")   # el siguiente track no tiene visual: entra un Random
        e = None if ahead_rand else self._target
        dok = self._deck_on is not False                              # ¿el deck de este visual suena él mismo?
        if e and os.path.exists(e["video_path"]):                    # ---- Sincronizado
            self.mode, s = "Sincronizado", cfg["sync"]
            f = self._factor(reading, e)
            dur, rem = self._duration_for(e, reading), self._rem_new   # rem: solo si se leyó el reloj en este ciclo
            scale = f if cfg["ocr"]["time_scaled_by_tempo"] else 1.0
            raw = dur - rem * scale if dur is not None and rem is not None else None   # posición del track (< 0: aún no empezó)
            el_v = max(0.0, raw) if raw is not None else None
            if not same(p.current, e["video_path"]):
                before = raw is not None and raw < -s["preroll_tol"]               # restante > duración: pre-roll
                self._pre, self._pre_n, self._start_at, self._released = before, 0, None, not before
                self._load_kind = "ahead" if (self._ahead and self._ahead.get("kind") == "video") else "start"
                self._first_speed, self._dense_until = True, (0.0 if before else now + s["initial_window"])
                p.load(e["video_path"], start=0.0 if before else (el_v or 0.0) + self._load_comp_for(e["video_path"]), live=(not before) and dok, kind="sync")
                self._speed, self._drift_pending, self._last_check, self._fast_fix = 1.0, False, now, False
                if not before and dok:                       # en marcha: la 1.ª revisión no espera los 3 s de las revisiones frecuentes
                    self._last_check = now - max(0.0, s["initial_every"] - FIRST_CHECK_S)
                self._applied, self._trim, self._hist, self._landing, self._slew, self._slew_sum = 1.0, 0.0, [], None, (0.0, 0.0, 0.0), 0.0      # (cada video empieza sin ajuste fino)
                self._load_probe = e["video_path"] if (not before and dok) else None            # su 1.ª revisión mide el atraso de arranque
                self._fast_fix = bool(self._load_probe) and bool(cfg["sync"].get("adaptive_load", True))   # y se corrige sin esperar confirmación
                self._slog("carga", [f"inicio {(0.0 if before else (el_v or 0.0) + self._load_comp_for(e['video_path'])):.2f} s", "pre-roll: espera el inicio del track" if before else "en marcha"],
                           video=e["video_path"])
                self._flips.clear()
                self._mini, self._mini_next = False, 0.0
                self._sync_info = "video cargado"
            elif raw is not None:
                self._pre_step(raw, dur, f, scale, now, p)
                self._jump_step(raw, f, now, p, playing and dok, s)
            self._set_speed(f, now)
            self._landing_tick(p)                                    # ¿ya terminó el último salto? (aprende lo que tarda)
            if self._pre:                                            # el video espera (cargado, en negro) al inicio del track
                p.set_playing(False)
                self._sync_info = self._pre_text(raw, f, now, playing)
                return
            p.set_playing(playing)
            matched = self._last_entry is not None and self._last_entry["video_path"] == e["video_path"]
            if not (playing and dok):
                self._flips.clear()                                  # con el reloj detenido los cambios guardados ya no valen
            self._mini_tick(now, s, playing and dok and matched and el_v is not None)
            dense, interval = self._dense(now), self._interval(now)
            due = (now - self._last_check >= interval or self._resume_check or self._sync_now or self._drift_pending)
            if playing and dok and due and matched and el_v is not None:
                # en las revisiones frecuentes se espera al próximo cambio de dígitos del reloj (≤1 s) para medir con precisión
                ref, n = self._flip_ref(dur, scale, f, now) if dense and self._flip is not None else (None, 0)
                precise = ref is not None and n >= (MINI_FLIPS if self._mini else 1)
                if precise or (not self._mini and (not dense or now - self._last_check >= interval + 1.5)):
                    self._check_sync(p, (ref if precise else el_v) + s["offset"] + s.get("ahead", 0.0), now, f, now, precise)
            return

        if not ahead_rand and self._last_entry is not None and self._target is None:   # coincidencia a la espera de confirmarse
            return                                                   # (1-2 s): no arrancar un random solo para cambiarlo enseguida

        self.mode, self._pre = "Visual Aleatorio", False             # ---- Random Visuals
        pool = self.db.orphans(self.videos)
        every = cfg["random"]["change_every"]
        in_pool = any(same(p.current, v) for v in pool)
        if pool and (not in_pool or (every and playing and now - self._rand_since > every)):
            choices = [v for v in pool if not same(v, p.current)] or pool
            p.load(random.choice(choices), loop=True, kind="random")
            self._speed, self._rand_since = 1.0, now
            self._applied, self._trim, self._hist, self._landing, self._slew, self._slew_sum = 1.0, 0.0, [], None, (0.0, 0.0, 0.0), 0.0
        p.set_playing(playing and bool(pool))
