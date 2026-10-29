# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Autovinculación: si el track que Traktor Pro 4 tiene cargado se parece (≥ 70 % por defecto) al nombre de un video aún sin
vincular y la duración coincide con la del video, se crea solo la entrada de ViDeck Pulse (título, BPM base y duración).
Aquí solo está la lógica pura (sin Qt ni hilos): el motor la usa y la prueba."""
import os
from difflib import SequenceMatcher

from .db import fmt_time, normalize

# palabras que suelen añadirse al nombre del archivo de un visual y no forman parte del título del track
NOISE = {"visual", "visuals", "video", "videoclip", "vj", "loop", "hd", "uhd", "1080p", "720p", "4k", "60fps", "30fps"}


NOT_DETECTED = "Canción no detectada en ningún Deck"      # texto de la alerta de la detección manual


def _clean(text):
    return " ".join(w for w in normalize(text).split() if w not in NOISE)


def clean_name(path):
    """Nombre de archivo comparable con un título de Traktor: sin extensión, sin ruido típico, normalizado."""
    return _clean(os.path.splitext(os.path.basename(path))[0])


def names(path):
    """Variantes comparables del nombre de archivo: completo y, si es «Artista - Título», solo el título (Traktor muestra
    el título en la 1.ª línea del deck y el artista aparte)."""
    stem = os.path.splitext(os.path.basename(path))[0]
    out = [_clean(stem)] + ([_clean(stem.split(" - ", 1)[1])] if " - " in stem else [])
    return [n for n in dict.fromkeys(out) if n]


def best_similarity(title, path):
    return max((similarity(title, n) for n in names(path)), default=0.0)


def similarity(title, name):
    """Parecido (0-1) entre el título leído y el nombre del archivo ya limpio. Como en el doble check normal, si el deck
    recortó un título largo se compara también con el principio del nombre."""
    nt = normalize(title)
    if not nt or not name:
        return 0.0
    r = SequenceMatcher(None, nt, name).ratio()
    if len(nt) < len(name):
        r = max(r, SequenceMatcher(None, nt, name[:len(nt) + 2]).ratio())
    return r


def candidates(title, videos, db, min_title):
    """[(parecido, ruta)] de los videos SIN vincular que se parecen al título, de mayor a menor."""
    out = []
    for v in videos:
        if db.entry_for_video(v) is not None:
            continue
        r = best_similarity(title, v)
        if r >= min_title:
            out.append((r, v))
    return sorted(out, key=lambda x: -x[0])


def base_bpm(bpm, pitch):
    """BPM original del track a partir del que muestra Traktor y su tempo (%): mostrado = base × (1 + tempo/100).
    Con el tempo en ±0.0 % es el mismo número. Devuelve None si el tempo no se pudo leer."""
    if bpm is None or pitch is None:
        return None
    return round(bpm / (1 + pitch / 100.0), 2)


def decide(title, reads, cands, durations, tol):
    """Elige el video para `title`. `reads`: lecturas de la duración en Traktor Pro 4 (la principal y las alternativas);
    `cands`: [(parecido, ruta)]; `durations`: {ruta: segundos | None}; `tol`: tolerancia en s.
    Devuelve (ruta | None, parecido, duración del video, mensaje)."""
    if not cands:
        return None, 0.0, None, ""
    reads = [x for x in dict.fromkeys(reads) if x is not None]
    ok = [(r, v, durations.get(v)) for r, v in cands
          if durations.get(v) is not None and any(abs(x - durations[v]) <= tol for x in reads)]
    if not ok:
        r, v = cands[0]
        vd = durations.get(v)
        seen = " / ".join(fmt_time(x) for x in reads) or "ilegible"
        why = (f"la duración no coincide (Traktor Pro 4 {seen} vs video {fmt_time(vd)})" if vd is not None
               else "no se pudo leer la duración del video")
        return None, r, vd, (f"«{title}» se parece a {os.path.basename(v)} ({r:.0%}) pero {why}: no se vincula solo; "
                             f"hazlo a mano en Configuración si es el mismo")
    if len(ok) > 1 and ok[0][0] - ok[1][0] < 0.03:
        listed = ", ".join(os.path.basename(v) for _, v, _ in ok[:3])
        return None, ok[0][0], ok[0][2], (f"«{title}» coincide con varios videos de la misma duración ({listed}): "
                                          f"vincula el correcto a mano en Configuración")
    r, v, vd = ok[0]
    return v, r, vd, ""


def scan_decks(decks, videos, min_title, tol, duration_of):
    """Detección manual: compara lo que Traktor Pro 4 tiene cargado en cada deck con los videos candidatos.
    `decks`: {"A": {"title", "duration", "alts", "bpm"}}; `videos`: rutas candidatas; `duration_of(ruta)` -> s | None.
    Devuelve (coincidencias, notas): cada coincidencia es {"deck", "video", "ratio", "vdur", "title", "duration", "bpm"} y las
    notas explican, deck por deck, por qué no hubo coincidencia (para la alerta «Canción no detectada en ningún Deck»)."""
    matches, notes, used = [], [], set()
    for d, info in decks.items():
        title = (info.get("title") or "").strip()
        if not title:
            notes.append(f"Deck {d}: sin canción cargada (no se leyó ningún título)")
            continue
        head = f"Deck {d}: «{title}»" + (f" ({fmt_time(info['duration'])})" if info.get("duration") is not None else "")
        sims = sorted(((best_similarity(title, v), v) for v in videos), key=lambda x: -x[0])
        cands = [(r, v) for r, v in sims if r >= min_title and v not in used][:4]
        taken = [v for r, v in sims if r >= min_title and v in used]
        if not cands and taken:
            notes.append(f"{head} → su video ({os.path.basename(taken[0])}) ya se asignó al otro deck")
            continue
        if not cands:
            best = f"; el más parecido: {os.path.basename(sims[0][1])} ({sims[0][0]:.0%})" if sims else ""
            notes.append(f"{head} → ningún video llega al {min_title:.0%} de parecido{best}" if sims
                         else f"{head} → no hay videos con los que comparar")
            continue
        durs = {v: duration_of(v) for _, v in cands}
        video, ratio, vdur, msg = decide(title, [info.get("duration"), *info.get("alts", [])], cands, durs, tol)
        if video is None:
            notes.append(f"Deck {d}: {msg}")
            continue
        used.add(video)
        reads = [x for x in [info.get("duration"), *info.get("alts", [])] if x is not None]
        dur = next((x for x in reads if abs(x - vdur) <= tol), None)          # la lectura que confirmó la duración
        matches.append({"deck": d, "video": video, "ratio": ratio, "vdur": vdur, "title": title,
                        "duration": dur, "bpm": info.get("bpm")})
    return matches, notes
