# SPDX-FileCopyrightText: 2026 JFinalXDark
# SPDX-License-Identifier: GPL-3.0-or-later
"""Lector de archivos .7z en Python puro (solo biblioteca estándar), para poder abrir las builds de mpv, que se publican en .7z.

Soporta lo que usan los empaquetadores habituales: contenedor 7z con cabecera normal o comprimida, bloques «sólidos», y los
métodos Copy, LZMA, LZMA2, BCJ (x86, ARM, ARMT, PPC, SPARC, IA64), Delta, Deflate y BZip2 encadenados de uno en uno. Lo demás (BCJ2,
PPMd, AES, varios volúmenes…) lanza `Unsupported` y quien llama prueba otro extractor. Cada archivo extraído se comprueba con el CRC-32
que guarda el .7z (`Corrupt` si no coincide).

Nunca extrae «a donde diga el archivo»: quien llama decide, para cada nombre, a qué archivo escribir (o lo salta) con `pick(nombre)`.
"""
import bz2
import lzma
import os
import struct
import zlib
from dataclasses import dataclass, field

SIGNATURE = b"7z\xbc\xaf\x27\x1c"
CHUNK = 1 << 20

K_END, K_HEADER, K_ARCHIVE_PROPS, K_ADD_STREAMS, K_MAIN_STREAMS, K_FILES_INFO = 0, 1, 2, 3, 4, 5
K_PACK_INFO, K_UNPACK_INFO, K_SUBSTREAMS, K_SIZE, K_CRC, K_FOLDER, K_CODERS_UNPACK, K_NUM_UNPACK = 6, 7, 8, 9, 10, 11, 12, 13
K_EMPTY_STREAM, K_EMPTY_FILE, K_ANTI, K_NAMES = 14, 15, 16, 17
K_ENCODED_HEADER = 0x17

BCJ_FILTERS = {  # id de método → filtro de lzma
    b"\x04": getattr(lzma, "FILTER_X86", None), b"\x03\x03\x01\x03": getattr(lzma, "FILTER_X86", None),
    b"\x05": getattr(lzma, "FILTER_POWERPC", None), b"\x03\x03\x02\x05": getattr(lzma, "FILTER_POWERPC", None),
    b"\x06": getattr(lzma, "FILTER_IA64", None), b"\x03\x03\x04\x01": getattr(lzma, "FILTER_IA64", None),
    b"\x07": getattr(lzma, "FILTER_ARM", None), b"\x03\x03\x05\x01": getattr(lzma, "FILTER_ARM", None),
    b"\x08": getattr(lzma, "FILTER_ARMTHUMB", None), b"\x03\x03\x07\x01": getattr(lzma, "FILTER_ARMTHUMB", None),
    b"\x09": getattr(lzma, "FILTER_SPARC", None), b"\x03\x03\x08\x05": getattr(lzma, "FILTER_SPARC", None),
}
if hasattr(lzma, "FILTER_ARM64"):
    BCJ_FILTERS[b"\x0a"] = lzma.FILTER_ARM64
M_COPY, M_LZMA, M_LZMA2, M_DELTA = b"\x00", b"\x03\x01\x01", b"\x21", b"\x03"
M_DEFLATE, M_BZIP2, M_BCJ2, M_AES = b"\x04\x01\x08", b"\x04\x02\x02", b"\x03\x03\x01\x1b", b"\x06\xf1\x07\x01"


class SevenZipError(Exception):
    pass


class Unsupported(SevenZipError):
    """El archivo usa algo que este lector no hace (hay que probar otro extractor)."""


class Corrupt(SevenZipError):
    """El archivo está dañado (suma o estructura incorrectas)."""


class Cancelled(SevenZipError):
    pass


@dataclass
class Coder:
    method: bytes
    n_in: int = 1
    n_out: int = 1
    props: bytes = b""


@dataclass
class Folder:
    coders: list = field(default_factory=list)
    bind: list = field(default_factory=list)          # [(in_index, out_index)]
    packed: list = field(default_factory=list)        # índices de entrada alimentados por los paquetes
    unpack_sizes: list = field(default_factory=list)  # por cada flujo de salida
    crc: int = None
    first_pack: int = 0                               # índice del primer flujo empaquetado de esta carpeta
    n_files: int = 1

    def total_in(self):
        return sum(c.n_in for c in self.coders)

    def total_out(self):
        return sum(c.n_out for c in self.coders)

    def final_out(self):
        used = {o for _i, o in self.bind}
        for k in range(self.total_out()):
            if k not in used:
                return k
        raise Corrupt("carpeta sin salida final")

    def size(self):
        return self.unpack_sizes[self.final_out()]


@dataclass
class Entry:
    name: str
    size: int = 0
    is_dir: bool = False
    crc: int = None
    folder: int = None        # None: no tiene datos (carpeta o archivo vacío)


class _Buf:
    """Lector de bytes con los números de longitud variable del formato 7z."""

    def __init__(self, data):
        self.d, self.p = memoryview(bytes(data)), 0

    def byte(self):
        if self.p >= len(self.d):
            raise Corrupt("cabecera truncada")
        b = self.d[self.p]
        self.p += 1
        return b

    def read(self, n):
        if n < 0 or self.p + n > len(self.d):
            raise Corrupt("cabecera truncada")
        v = bytes(self.d[self.p:self.p + n])
        self.p += n
        return v

    def u32(self):
        return struct.unpack("<I", self.read(4))[0]

    def num(self):
        first, mask, value = self.byte(), 0x80, 0
        for i in range(8):
            if first & mask == 0:
                return value + ((first & (mask - 1)) << (8 * i))
            value |= self.byte() << (8 * i)
            mask >>= 1
        return value

    def bits(self, n):
        out, cur, mask = [], 0, 0
        for _ in range(n):
            if mask == 0:
                cur, mask = self.byte(), 0x80
            out.append(bool(cur & mask))
            mask >>= 1
        return out

    def digests(self, n):
        defined = [True] * n if self.byte() else self.bits(n)
        return [self.u32() if d else None for d in defined]

    def skip_props(self):
        """Salta propiedades «tipo, tamaño, datos» hasta kEnd."""
        while True:
            t = self.num()
            if t == K_END:
                return
            self.read(self.num())


def _lzma2_dict(b):
    if b > 40:
        raise Corrupt("tamaño de diccionario LZMA2 inválido")
    return 0xFFFFFFFF if b == 40 else (2 | (b & 1)) << (b // 2 + 11)


def _filter_for(coder):
    """Filtro de `lzma` (modo RAW) equivalente a un coder de 7z, o None si es otro tipo de método."""
    m, p = coder.method, coder.props
    if m == M_LZMA2:
        return {"id": lzma.FILTER_LZMA2, "dict_size": _lzma2_dict(p[0] if p else 0)}
    if m == M_LZMA:
        if len(p) < 5:
            raise Corrupt("propiedades LZMA inválidas")
        d = p[0]
        lc, d = d % 9, d // 9
        lp, pb = d % 5, d // 5
        return {"id": lzma.FILTER_LZMA1, "dict_size": struct.unpack("<I", p[1:5])[0], "lc": lc, "lp": lp, "pb": pb}
    if m == M_DELTA:
        return {"id": lzma.FILTER_DELTA, "dist": (p[0] if p else 0) + 1}
    f = BCJ_FILTERS.get(m)
    if f is not None:
        return {"id": f}
    return None


class _Decoder:
    """Decodifica los datos de una carpeta, en trozos. Cadena de coders 1→1: LZMA/LZMA2/BCJ/Delta juntos en una sola
    cadena de `lzma`; Copy, Deflate y BZip2 por separado."""

    def __init__(self, folder):
        order = self._order(folder)
        self.stages = []                                  # de la entrada empaquetada a la salida final
        chain = []
        for c in order:
            f = _filter_for(c)
            if f is not None:
                chain.append(f)
                continue
            if chain:
                self.stages.append(self._lz(chain))
                chain = []
            if c.method == M_COPY:
                self.stages.append(None)
            elif c.method == M_DEFLATE:
                self.stages.append(zlib.decompressobj(-15))
            elif c.method == M_BZIP2:
                self.stages.append(bz2.BZ2Decompressor())
            elif c.method == M_BCJ2:
                raise Unsupported("filtro BCJ2")
            elif c.method.startswith(M_AES):
                raise Unsupported("archivo cifrado")
            else:
                raise Unsupported(f"método de compresión {c.method.hex()}")
        if chain:
            self.stages.append(self._lz(chain))
        self.stages = [s for s in self.stages if s is not None]

    @staticmethod
    def _lz(chain):
        # `chain` viene de la entrada a la salida; lzma espera el orden de compresión (de la salida a la entrada)
        try:
            return lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=list(reversed(chain)))
        except (lzma.LZMAError, TypeError, ValueError) as e:
            raise Unsupported(f"filtro no disponible en este Python ({e})") from e

    @staticmethod
    def _order(folder):
        """Coders en el orden en que se aplican al descomprimir: del que lee los datos empaquetados al que da la salida final."""
        for c in folder.coders:
            if c.n_in != 1 or c.n_out != 1:
                raise Unsupported("método con varios flujos (BCJ2)")
        if len(folder.packed) != 1:
            raise Unsupported("carpeta con varios flujos empaquetados")
        in_of_coder = {}
        n = 0
        for idx, c in enumerate(folder.coders):
            in_of_coder[n] = idx
            n += c.n_in
        out_bound = {i: o for i, o in folder.bind}        # entrada i ← salida o
        cur = in_of_coder[folder.packed[0]]
        order, seen = [], set()
        while True:
            if cur in seen:
                raise Corrupt("coders en ciclo")
            seen.add(cur)
            order.append(folder.coders[cur])
            # la salida de `cur` alimenta a la entrada que tenga ese índice de salida en un bind pair
            nxt = next((i for i, o in out_bound.items() if o == cur), None)
            if nxt is None:
                break
            cur = in_of_coder[nxt]
        if len(order) != len(folder.coders):
            raise Corrupt("coders desconectados")
        return order

    def feed(self, data):
        for st in self.stages:
            if st is None:
                continue
            try:
                data = st.decompress(data)
            except (lzma.LZMAError, zlib.error, OSError, EOFError, ValueError) as e:
                raise Corrupt(f"datos comprimidos dañados ({e})") from e
            if not data:
                break
        return data


class SevenZip:
    def __init__(self, path):
        self.path = path
        self.f = open(path, "rb")
        try:
            self._parse()
        except BaseException:
            self.f.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def close(self):
        self.f.close()

    # -------------------------------------------------------------------------------- estructura
    def _parse(self):
        head = self.f.read(32)
        if len(head) < 32 or head[:6] != SIGNATURE:
            raise Corrupt("no es un archivo 7z")
        crc = struct.unpack("<I", head[8:12])[0]
        if zlib.crc32(head[12:32]) & 0xFFFFFFFF != crc:
            raise Corrupt("cabecera inicial dañada")
        off, size, ncrc = struct.unpack("<QQI", head[12:32])
        self.folders, self.pack_sizes, self.pack_pos, self.entries = [], [], 32, []
        if size == 0:
            return
        self.f.seek(32 + off)
        raw = self.f.read(size)
        if len(raw) != size or zlib.crc32(raw) & 0xFFFFFFFF != ncrc:
            raise Corrupt("cabecera dañada")
        for _ in range(4):                                # una cabecera comprimida puede contener otra
            b = _Buf(raw)
            t = b.num()
            if t == K_HEADER:
                self._header(b)
                return
            if t != K_ENCODED_HEADER:
                raise Corrupt("cabecera desconocida")
            streams = self._streams_info(b)
            folders, psizes, ppos = streams["folders"], streams["pack_sizes"], streams["pack_pos"]
            if not folders:
                raise Corrupt("cabecera comprimida vacía")
            out = bytearray()
            dec = _Decoder(folders[0])
            self.f.seek(ppos)
            left = psizes[0]
            while left > 0:
                chunk = self.f.read(min(CHUNK, left))
                if not chunk:
                    raise Corrupt("archivo truncado")
                left -= len(chunk)
                out += dec.feed(chunk)
            raw = bytes(out[:folders[0].size()])
            if folders[0].crc is not None and zlib.crc32(raw) & 0xFFFFFFFF != folders[0].crc:
                raise Corrupt("cabecera comprimida dañada")
        raise Corrupt("cabeceras anidadas de más")

    def _header(self, b):
        t = b.num()
        if t == K_ARCHIVE_PROPS:
            b.skip_props()
            t = b.num()
        if t == K_ADD_STREAMS:
            raise Unsupported("flujos adicionales")
        streams = None
        if t == K_MAIN_STREAMS:
            streams = self._streams_info(b)
            t = b.num()
        self.folders = streams["folders"] if streams else []
        self.pack_sizes = streams["pack_sizes"] if streams else []
        self.pack_pos = streams["pack_pos"] if streams else 32
        files = None
        if t == K_FILES_INFO:
            files = self._files_info(b, streams)
            t = b.num()
        if t != K_END:
            raise Corrupt("cabecera con datos de más")
        self.entries = files or []

    def _streams_info(self, b):
        s = {"folders": [], "pack_sizes": [], "pack_pos": 32, "sub": None}
        while True:
            t = b.num()
            if t == K_END:
                break
            if t == K_PACK_INFO:
                s["pack_pos"] = 32 + b.num()
                n = b.num()
                while True:
                    u = b.num()
                    if u == K_END:
                        break
                    if u == K_SIZE:
                        s["pack_sizes"] = [b.num() for _ in range(n)]
                    elif u == K_CRC:
                        b.digests(n)
                    else:
                        raise Corrupt("propiedad de paquetes desconocida")
                s["n_pack"] = n
            elif t == K_UNPACK_INFO:
                if b.num() != K_FOLDER:
                    raise Corrupt("falta la lista de carpetas")
                nf = b.num()
                if b.byte() != 0:
                    raise Unsupported("carpetas externas")
                folders, fp = [], 0
                for _ in range(nf):
                    fo = self._folder(b)
                    fo.first_pack = fp
                    fp += len(fo.packed)
                    folders.append(fo)
                if b.num() != K_CODERS_UNPACK:
                    raise Corrupt("faltan los tamaños de descompresión")
                for fo in folders:
                    fo.unpack_sizes = [b.num() for _ in range(fo.total_out())]
                while True:
                    u = b.num()
                    if u == K_END:
                        break
                    if u == K_CRC:
                        for fo, c in zip(folders, b.digests(nf)):
                            fo.crc = c
                    else:
                        raise Corrupt("propiedad de carpetas desconocida")
                s["folders"] = folders
            elif t == K_SUBSTREAMS:
                s["sub"] = self._substreams(b, s["folders"])
            else:
                raise Corrupt("información de flujos desconocida")
        if s["sub"] is None:                              # sin SubStreamsInfo: un archivo por carpeta
            s["sub"] = [[(fo.size(), fo.crc)] for fo in s["folders"]]
        return s

    def _folder(self, b):
        fo = Folder()
        for _ in range(b.num()):
            flag = b.byte()
            if flag & 0x80:
                raise Unsupported("métodos alternativos")
            method = b.read(flag & 0x0F)
            n_in, n_out = (b.num(), b.num()) if flag & 0x10 else (1, 1)
            props = b.read(b.num()) if flag & 0x20 else b""
            fo.coders.append(Coder(method, n_in, n_out, props))
        n_bind = fo.total_out() - 1
        fo.bind = [(b.num(), b.num()) for _ in range(n_bind)]
        n_packed = fo.total_in() - n_bind
        if n_packed == 1:
            bound = {i for i, _o in fo.bind}
            fo.packed = [next(i for i in range(fo.total_in()) if i not in bound)]
        else:
            fo.packed = [b.num() for _ in range(n_packed)]
        return fo

    def _substreams(self, b, folders):
        counts = [1] * len(folders)
        t = b.num()
        if t == K_NUM_UNPACK:
            counts = [b.num() for _ in folders]
            t = b.num()
        sizes = []
        for fo, n in zip(folders, counts):
            if n == 0:
                sizes.append([])
                continue
            if t == K_SIZE:
                part = [b.num() for _ in range(n - 1)]
            else:
                part = [] if n == 1 else None
                if part is None:
                    raise Corrupt("faltan los tamaños de los archivos")
            part.append(fo.size() - sum(part))
            sizes.append(part)
        if t == K_SIZE:
            t = b.num()
        crcs = [[None] * len(s) for s in sizes]
        for k, (fo, s) in enumerate(zip(folders, sizes)):
            if len(s) == 1 and fo.crc is not None:
                crcs[k][0] = fo.crc
        while t != K_END:
            if t == K_CRC:
                need = [(k, j) for k, s in enumerate(sizes) for j in range(len(s)) if crcs[k][j] is None]
                for (k, j), c in zip(need, b.digests(len(need))):
                    crcs[k][j] = c
            else:
                raise Corrupt("propiedad de archivos desconocida")
            t = b.num()
        return [[(sz, c) for sz, c in zip(s, cs)] for s, cs in zip(sizes, crcs)]

    def _files_info(self, b, streams):
        n = b.num()
        empty = [False] * n
        empty_file = None
        names = []
        while True:
            t = b.num()
            if t == K_END:
                break
            size = b.num()
            data = b.read(size)
            sub = _Buf(data)
            if t == K_EMPTY_STREAM:
                empty = sub.bits(n)
            elif t == K_EMPTY_FILE:
                empty_file = sub.bits(sum(empty))
            elif t == K_NAMES:
                if sub.byte() != 0:
                    raise Unsupported("nombres externos")
                names = self._utf16_names(bytes(data[1:]), n)
        if len(names) != n:
            raise Corrupt("faltan nombres de archivo")
        sub_lists = streams["sub"] if streams else []
        flat = [(k, sz, c) for k, lst in enumerate(sub_lists) for sz, c in lst]
        entries, si, ei = [], 0, 0
        for i in range(n):
            if empty[i]:
                is_file = bool(empty_file[ei]) if empty_file is not None and ei < len(empty_file) else False
                ei += 1
                entries.append(Entry(names[i], 0, not is_file, None, None))
            else:
                if si >= len(flat):
                    raise Corrupt("más archivos que datos")
                k, sz, c = flat[si]
                si += 1
                entries.append(Entry(names[i], sz, False, c, k))
        return entries

    @staticmethod
    def _utf16_names(raw, n):
        out, i = [], 0
        while len(out) < n and i < len(raw):
            j = i
            while j + 1 < len(raw) and not (raw[j] == 0 and raw[j + 1] == 0):
                j += 2
            out.append(raw[i:j].decode("utf-16-le", "replace"))
            i = j + 2
        return out

    # -------------------------------------------------------------------------------- uso
    def names(self):
        return [(e.name, e.size, e.is_dir) for e in self.entries]

    def extract(self, pick, progress=None, cancel=None, max_total=None):
        """Extrae los archivos para los que `pick(nombre)` devuelve una ruta de destino (None = saltarlo). Devuelve {nombre: ruta}.
        `max_total`: tope de bytes descomprimidos (protege de un archivo malicioso)."""
        wanted = {}
        total = 0
        for e in self.entries:
            if e.is_dir:
                continue
            dest = pick(e.name)
            if dest:
                wanted[id(e)] = dest
                total += e.size
        if max_total is not None and total > max_total:
            raise SevenZipError("los archivos pedidos son demasiado grandes")
        out, done = {}, 0
        by_folder = {}
        for e in self.entries:
            by_folder.setdefault(e.folder, []).append(e)
        for e in by_folder.get(None, []):                 # archivos vacíos
            if id(e) in wanted:
                os.makedirs(os.path.dirname(wanted[id(e)]) or ".", exist_ok=True)
                open(wanted[id(e)], "wb").close()
                out[e.name] = wanted[id(e)]
        for k, fo in enumerate(self.folders):
            entries = by_folder.get(k, [])
            last = max((i for i, e in enumerate(entries) if id(e) in wanted), default=-1)
            if last < 0:
                continue
            self._extract_folder(k, fo, entries[:last + 1], wanted, out, cancel,
                                 (lambda d, t=total: progress and progress(done + d, t)) if progress else None)
            done += sum(e.size for e in entries if id(e) in wanted)
        return out

    def _extract_folder(self, k, fo, entries, wanted, out, cancel, progress):
        if len(fo.packed) != 1:
            raise Unsupported("carpeta con varios flujos empaquetados")
        dec = _Decoder(fo)
        pack_i = fo.first_pack
        start = self.pack_pos + sum(self.pack_sizes[:pack_i])
        left = self.pack_sizes[pack_i]
        self.f.seek(start)
        idx, remaining, fh, crc, written = 0, entries[0].size if entries else 0, None, 0, 0

        def open_current():
            nonlocal fh, crc
            e = entries[idx]
            crc = 0
            fh = None
            if id(e) in wanted:
                os.makedirs(os.path.dirname(wanted[id(e)]) or ".", exist_ok=True)
                fh = open(wanted[id(e)], "wb")

        def close_current():
            nonlocal fh
            e = entries[idx]
            if fh is not None:
                fh.close()
                fh = None
                out[e.name] = wanted[id(e)]
                if e.crc is not None and crc & 0xFFFFFFFF != e.crc:
                    raise Corrupt(f"el CRC de «{e.name}» no coincide: archivo dañado")

        try:
            open_current()
            while idx < len(entries) and left > 0:
                if cancel is not None and cancel.is_set():
                    raise Cancelled("cancelado")
                raw = self.f.read(min(CHUNK, left))
                if not raw:
                    raise Corrupt("archivo truncado")
                left -= len(raw)
                data = memoryview(dec.feed(raw))
                pos = 0
                while pos < len(data) and idx < len(entries):
                    take = min(remaining, len(data) - pos)
                    piece = data[pos:pos + take]
                    if fh is not None:
                        fh.write(piece)
                        crc = zlib.crc32(piece, crc)
                    pos += take
                    remaining -= take
                    written += take
                    if progress:
                        progress(written)
                    if remaining == 0:
                        close_current()
                        idx += 1
                        if idx < len(entries):
                            remaining = entries[idx].size
                            open_current()
            if idx < len(entries):
                raise Corrupt("el archivo termina antes de lo esperado")
        finally:
            if fh is not None:
                fh.close()


def is_7z(path):
    try:
        with open(path, "rb") as f:
            return f.read(6) == SIGNATURE
    except OSError:
        return False


# ------------------------------------------------------------------------------------------------ escritura
def _wnum(v):
    """Número de longitud variable del formato 7z."""
    if v < 0x80:
        return bytes([v])
    for n in range(1, 9):
        if v < (1 << (7 * (n + 1))):
            first = (0xFF00 >> n) & 0xFF
            return bytes([first | (v >> (8 * n))]) + (v & ((1 << (8 * n)) - 1)).to_bytes(n, "little")
    return b"\xff" + v.to_bytes(8, "little")


def _bitfield(bits):
    out, cur, mask = bytearray(), 0, 0x80
    for b in bits:
        if b:
            cur |= mask
        mask >>= 1
        if mask == 0:
            out.append(cur)
            cur, mask = 0, 0x80
    if mask != 0x80:
        out.append(cur)
    return bytes(out)


def _lzma2_prop(dict_size):
    for b in range(40):
        if ((2 | (b & 1)) << (b // 2 + 11)) >= dict_size:
            return b
    return 40


def write_7z(dest, files, progress=None, cancel=None, dict_size=1 << 23):
    """Crea un .7z (un único bloque sólido LZMA2, cabecera sin comprimir) con `files` = [(nombre dentro del archivo, ruta de origen)].
    Se escribe primero a «dest.part» y recién al final se renombra: nunca queda un archivo a medias. Devuelve el tamaño del .7z."""
    files = [(n.replace("\\", "/").strip("/"), p) for n, p in files]
    sized = [(n, p, os.path.getsize(p)) for n, p in files]
    total = sum(s for _n, _p, s in sized)
    streams = [(n, p, s) for n, p, s in sized if s > 0]
    empties = [n for n, _p, s in sized if s == 0]
    tmp = dest + ".part"
    comp = lzma.LZMACompressor(format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2, "preset": 6, "dict_size": dict_size}])
    sizes, crcs, packed, done = [], [], 0, 0
    try:
        with open(tmp, "wb") as out:
            out.write(b"\0" * 32)
            for n, p, s in streams:
                crc, got = 0, 0
                with open(p, "rb") as src:
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise Cancelled("cancelado")
                        chunk = src.read(CHUNK)
                        if not chunk:
                            break
                        crc = zlib.crc32(chunk, crc)
                        got += len(chunk)
                        piece = comp.compress(chunk)
                        if piece:
                            out.write(piece)
                            packed += len(piece)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
                if got != s:
                    raise SevenZipError(f"«{p}» cambió mientras se comprimía")
                sizes.append(got)
                crcs.append(crc & 0xFFFFFFFF)
            tail = comp.flush()
            out.write(tail)
            packed += len(tail)
            h = bytearray([K_HEADER])
            if streams:
                h += bytes([K_MAIN_STREAMS, K_PACK_INFO]) + _wnum(0) + _wnum(1) + bytes([K_SIZE]) + _wnum(packed) + bytes([K_END])
                h += bytes([K_UNPACK_INFO, K_FOLDER]) + _wnum(1) + b"\x00" + _wnum(1) + bytes([0x21, 0x21]) + _wnum(1) + bytes([_lzma2_prop(dict_size)])
                h += bytes([K_CODERS_UNPACK]) + _wnum(sum(sizes)) + bytes([K_END])
                h += bytes([K_SUBSTREAMS, K_NUM_UNPACK]) + _wnum(len(streams))
                if len(streams) > 1:
                    h += bytes([K_SIZE]) + b"".join(_wnum(x) for x in sizes[:-1])
                h += bytes([K_CRC, 1]) + b"".join(struct.pack("<I", c) for c in crcs) + bytes([K_END, K_END])
            names = [n for n, _p, _s in streams] + empties
            if names:
                h += bytes([K_FILES_INFO]) + _wnum(len(names))
                if empties:
                    es = _bitfield([False] * len(streams) + [True] * len(empties))
                    ef = _bitfield([True] * len(empties))
                    h += bytes([K_EMPTY_STREAM]) + _wnum(len(es)) + es + bytes([K_EMPTY_FILE]) + _wnum(len(ef)) + ef
                nm = b"\x00" + b"".join(n.encode("utf-16-le") + b"\x00\x00" for n in names)
                h += bytes([K_NAMES]) + _wnum(len(nm)) + nm + bytes([K_END])
            h += bytes([K_END])
            h = bytes(h)
            out.write(h)
            start = struct.pack("<QQI", packed, len(h), zlib.crc32(h) & 0xFFFFFFFF)
            out.seek(0)
            out.write(SIGNATURE + b"\x00\x04" + struct.pack("<I", zlib.crc32(start) & 0xFFFFFFFF) + start)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return os.path.getsize(dest)
