"""Leitura direta da MFT do NTFS (parte pura, sem Win32), para analisar o volume sem abrir arquivo por arquivo.

A MFT guarda, para cada arquivo, o nome, a pasta-pai, o tamanho e a lista de extents (data runs).
Ler a MFT inteira em sequência é muito mais rápido do que fazer CreateFile + DeviceIoControl para
cada arquivo. Este módulo só decodifica; quem lê o volume bruto é o backend do Windows.

Qualquer coisa fora do esperado (registro inconsistente, atributo que não sabemos ler, buracos nos
extents) faz o arquivo voltar para o método clássico (`None` no lugar dos extents), e uma falha
geral levanta `Unsupported`, o que faz o backend usar o método clássico para o volume todo."""
from __future__ import annotations

import struct
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Set, Tuple

from .model import Extent

ATTR_STANDARD_INFORMATION = 0x10
ATTR_ATTRIBUTE_LIST = 0x20
ATTR_FILE_NAME = 0x30
ATTR_DATA = 0x80
ATTR_END = 0xFFFFFFFF

FLAG_IN_USE = 0x1
FLAG_DIRECTORY = 0x2
ROOT_INDEX = 5
FIRST_USER_RECORD = 16          # 0..15 são arquivos de metadados ($MFT, $LogFile, ...), exceto a raiz (5)
EXTEND_INDEX = 11               # $Extend
INDEX_MASK = 0xFFFFFFFFFFFF     # os 48 bits de baixo de uma referência de arquivo

# namespaces de $FILE_NAME: 0 POSIX, 1 Win32, 2 DOS (nome curto 8.3), 3 Win32+DOS
NS_DOS = 2

SECTOR = 512

_U16 = struct.Struct("<H")
_U32 = struct.Struct("<I")
_U64 = struct.Struct("<Q")
_I64 = struct.Struct("<q")
_ATTR_HEAD = struct.Struct("<II")
_FIXUP_HEAD = struct.Struct("<HH")


class Unsupported(Exception):
    """A MFT deste volume não pode ser lida por este módulo (o backend cai no método clássico)."""


# --- decodificação de baixo nível ------------------------------------------------------------

def apply_fixups(rec: bytearray) -> bool:
    """Desfaz o "update sequence": os 2 últimos bytes de cada setor de 512 B foram trocados pelo
    número de sequência; os valores originais ficam no array no cabeçalho. False se inconsistente."""
    usa_off, usa_cnt = _FIXUP_HEAD.unpack_from(rec, 4)
    if usa_cnt < 2 or usa_off + 2 * usa_cnt > len(rec):
        return False
    usn = bytes(rec[usa_off:usa_off + 2])
    for i in range(usa_cnt - 1):
        end = (i + 1) * SECTOR
        if end > len(rec):
            break
        if rec[end - 2:end] != usn:
            return False
        rec[end - 2:end] = rec[usa_off + 2 + 2 * i:usa_off + 4 + 2 * i]
    return True


def decode_runs(data, pos: int, end: int, start_vcn: int = 0) -> List[Extent]:
    """Decodifica uma lista de data runs. Cada run: 1 byte de cabeçalho (tamanho do comprimento no
    nibble baixo, tamanho do deslocamento no alto), comprimento, deslocamento com sinal em relação
    ao run anterior. Deslocamento de tamanho 0 = trecho esparso (lcn -1)."""
    out: List[Extent] = []
    lcn = 0
    vcn = start_vcn
    while pos < end:
        head = data[pos]
        if head == 0:
            break
        pos += 1
        ls, os_ = head & 0xF, head >> 4
        if ls == 0 or ls > 8 or os_ > 8 or pos + ls + os_ > end:
            raise ValueError("data run inválido")
        length = int.from_bytes(data[pos:pos + ls], "little")
        pos += ls
        if os_ == 0:
            out.append(Extent(vcn, -1, length))
        else:
            lcn += int.from_bytes(data[pos:pos + os_], "little", signed=True)
            pos += os_
            if lcn < 0:
                raise ValueError("data run com cluster negativo")
            out.append(Extent(vcn, lcn, length))
        vcn += length
    return out


def encode_runs(extents: List[Tuple[int, int]]) -> bytes:
    """Inverso de decode_runs, [(lcn, length)] -> bytes (lcn -1 = esparso). Usado nos testes."""
    out = bytearray()
    prev = 0
    for lcn, length in extents:
        ls = max(1, (length.bit_length() + 7) // 8)
        if lcn < 0:
            out.append(ls)
            out += length.to_bytes(ls, "little")
            continue
        delta = lcn - prev
        os_ = 1
        while not -(1 << (8 * os_ - 1)) <= delta < (1 << (8 * os_ - 1)):
            os_ += 1
        out.append(ls | (os_ << 4))
        out += length.to_bytes(ls, "little")
        out += delta.to_bytes(os_, "little", signed=True)
        prev = lcn
    out.append(0)
    return bytes(out)


# --- registro (FILE record) --------------------------------------------------------------------

# (flags, base_index, nomes[(pai, nome, namespace)], atributos_win32|None, pedaços_do_$DATA, tem_lista_de_atributos)
# pedaço = (vcn_inicial, não_residente, tamanho_real, extents|None)
Parsed = Tuple[int, int, List[Tuple[int, str, int]], Optional[int], List[tuple], bool]


def parse_record(rec: bytearray) -> Optional[Parsed]:
    """Lê um registro de 1 arquivo. None se não for um registro FILE válido em uso."""
    if rec[:4] != b"FILE":
        return None
    flags = _U16.unpack_from(rec, 22)[0]
    if not flags & FLAG_IN_USE:
        return None
    if not apply_fixups(rec):
        return None
    base = _U64.unpack_from(rec, 32)[0] & INDEX_MASK
    pos = _U16.unpack_from(rec, 20)[0]
    limit = min(_U32.unpack_from(rec, 24)[0], len(rec))
    names: List[Tuple[int, str, int]] = []
    pieces: List[tuple] = []
    std: Optional[int] = None
    has_list = False
    while pos + 16 <= limit:
        atype, alen = _ATTR_HEAD.unpack_from(rec, pos)
        if atype == ATTR_END:
            break
        if alen < 16 or alen % 8 or pos + alen > limit:
            return None  # registro corrompido
        nonres = rec[pos + 8]
        if atype == ATTR_STANDARD_INFORMATION:
            if not nonres:
                co = pos + _U16.unpack_from(rec, pos + 20)[0]
                if co + 36 <= limit:
                    std = _U32.unpack_from(rec, co + 32)[0]
        elif atype == ATTR_FILE_NAME:
            if not nonres:
                co = pos + _U16.unpack_from(rec, pos + 20)[0]
                nlen = rec[co + 64]
                if co + 66 + 2 * nlen <= limit:
                    parent = _U64.unpack_from(rec, co)[0] & INDEX_MASK
                    names.append((parent, bytes(rec[co + 66:co + 66 + 2 * nlen]).decode("utf-16-le", "replace"),
                                  rec[co + 65]))
        elif atype == ATTR_DATA:
            if rec[pos + 9] == 0:  # só o fluxo sem nome (os com nome são fluxos alternativos)
                if nonres:
                    start_vcn = _I64.unpack_from(rec, pos + 16)[0]
                    runs_off = _U16.unpack_from(rec, pos + 32)[0]
                    real = _U64.unpack_from(rec, pos + 48)[0]
                    try:
                        ex = decode_runs(rec, pos + runs_off, pos + alen, start_vcn)
                    except ValueError:
                        ex = None
                    pieces.append((start_vcn, True, real, ex))
                else:
                    pieces.append((0, False, _U32.unpack_from(rec, pos + 16)[0], None))
        elif atype == ATTR_ATTRIBUTE_LIST:
            has_list = True
        pos += alen
    return flags, base, names, std, pieces, has_list


# --- varredura completa ----------------------------------------------------------------------------

class MftScanner:
    """Recebe os registros da MFT (feed) e depois monta caminhos, tamanhos e extents (results)."""

    def __init__(self, drive_root: str, cluster_size: int = 4096):
        self.cluster_size = cluster_size
        self.root = drive_root.rstrip("\\") + "\\"
        self.dirs: Dict[int, Tuple[int, str]] = {}                  # índice -> (pai, nome)
        self.files: Dict[int, list] = {}                              # índice -> [nomes, atributos, pedaços, incompleto]
        self.pending: Dict[int, list] = {}                            # extensões que chegaram antes da base
        self.records = 0
        self.skipped_records = 0

    def feed(self, idx: int, rec: bytearray) -> None:
        self.records += 1
        parsed = parse_record(rec)
        if parsed is None:
            return
        flags, base, names, std, pieces, has_list = parsed
        if base:  # registro de extensão: complementa o registro-base (arquivo muito fragmentado)
            ent = self.files.get(base)
            if ent is not None:
                ent[0].extend(names)
                ent[2].extend(pieces)
            else:
                self.pending.setdefault(base, []).append((names, pieces))
            return
        if idx < FIRST_USER_RECORD and idx != ROOT_INDEX:
            return
        if flags & FLAG_DIRECTORY:
            best = _pick_name(names)
            if best is not None:
                self.dirs[idx] = (best[0], best[1])
            return
        self.files[idx] = [list(names), std, list(pieces), has_list]

    def _dir_path(self, idx: int, cache: Dict[int, Optional[str]], bad: Set[int]) -> Optional[str]:
        """Caminho de uma pasta; None se estiver dentro de uma pasta excluída ou a cadeia estiver quebrada."""
        if idx == ROOT_INDEX:
            return self.root
        if idx in cache:
            return cache[idx]
        chain: List[int] = []
        cur = idx
        result: Optional[str] = None
        while True:
            if cur == ROOT_INDEX:
                result = self.root
                break
            if cur in cache:
                result = cache[cur]
                break
            ent = self.dirs.get(cur)
            if ent is None or cur in bad or len(chain) > 4096:
                result = None
                break
            chain.append(cur)
            cur = ent[0]
        for c in reversed(chain):
            if result is not None:
                result = result + self.dirs[c][1] + "\\"
            cache[c] = result
        return cache[idx] if idx in cache else result

    def results(self) -> Iterator[Tuple[str, int, int, Optional[List[Extent]]]]:
        """(caminho, tamanho, atributos Win32, extents). extents None = não deu para ler com segurança
        (o backend consulta o arquivo da forma clássica)."""
        for base, items in self.pending.items():
            ent = self.files.get(base)
            if ent is not None:
                for names, pieces in items:
                    ent[0].extend(names)
                    ent[2].extend(pieces)
        self.pending = {}
        # pastas que nunca devem aparecer (o Windows também não as lista para o usuário)
        bad: Set[int] = {EXTEND_INDEX}
        for idx, (parent, name) in self.dirs.items():
            if parent == ROOT_INDEX and name.lower() == "system volume information":
                bad.add(idx)
        cache: Dict[int, Optional[str]] = {}
        for idx, (names, std, pieces, has_list) in self.files.items():
            if not pieces:
                continue
            size, extents = _assemble(pieces)
            if extents and not _complete(extents, size, self.cluster_size):
                extents = None  # faltam pedaços (registro de extensão não visto): confira o arquivo da forma clássica
            attrs = std or 0
            seen: Set[Tuple[int, str]] = set()
            for parent, name, ns in names:
                if ns == NS_DOS and len(names) > 1:
                    continue  # nome curto 8.3 do mesmo arquivo
                key = (parent, name)
                if key in seen:
                    continue
                seen.add(key)
                base = self._dir_path(parent, cache, bad)
                if base is None:
                    continue
                yield base + name, size, attrs, extents


def _pick_name(names: List[Tuple[int, str, int]]) -> Optional[Tuple[int, str]]:
    best = None
    for parent, name, ns in names:
        if ns != NS_DOS:
            return parent, name
        best = (parent, name)
    return best


def _assemble(pieces: List[tuple]) -> Tuple[int, Optional[List[Extent]]]:
    """Junta os pedaços do $DATA (vários só quando há registros de extensão)."""
    first = None
    for p in pieces:
        if p[0] == 0:
            first = p
            break
    if first is None:
        return 0, None
    size = first[2]
    if not first[1]:
        return size, []  # residente: sem clusters
    if len(pieces) == 1:
        return size, first[3]
    ordered = sorted(pieces, key=lambda p: p[0])
    out: List[Extent] = []
    for p in ordered:
        if not p[1] or p[3] is None:
            return size, None
        out.extend(p[3])
    return size, out


def _complete(extents: List[Extent], size: int, cluster_size: int) -> bool:
    """Os extents são contínuos a partir do vcn 0 e cobrem o tamanho do arquivo?"""
    nxt = 0
    for e in extents:
        if e.vcn != nxt:
            return False
        nxt = e.vcn + e.length
    return nxt * cluster_size >= size


# --- leitura da MFT a partir dos data runs do $MFT ----------------------------------------------------

def iter_mft_records(read: Callable[[int, int], bytes], runs: List[Extent], cluster_size: int,
                     record_size: int, total_records: int, chunk_bytes: int = 4 << 20,
                     progress: Optional[Callable[[int, int], None]] = None) -> Iterator[Tuple[int, bytearray]]:
    """Lê o fluxo de dados do $MFT run a run (`read(deslocamento_no_volume, bytes)`) e entrega
    (índice, registro) em ordem."""
    buf = bytearray()
    idx = 0
    for run in runs:
        if run.lcn < 0:
            raise Unsupported("a MFT tem trecho esparso")
        offset = run.lcn * cluster_size
        remaining = run.length * cluster_size
        while remaining > 0 and idx < total_records:
            n = min(chunk_bytes, remaining)
            data = read(offset, n)
            if len(data) != n:
                raise Unsupported("leitura curta da MFT")
            buf += data
            offset += n
            remaining -= n
            pos = 0
            end = len(buf) - record_size + 1
            while pos < end and idx < total_records:
                yield idx, bytearray(buf[pos:pos + record_size])
                idx += 1
                pos += record_size
            del buf[:pos]
            if progress is not None:
                progress(idx, total_records)
        if idx >= total_records:
            break


def mft_runs_from_record0(rec0: bytearray) -> Tuple[List[Extent], int]:
    """Extents e tamanho do fluxo $DATA do próprio $MFT (registro 0)."""
    parsed = parse_record(rec0)
    if parsed is None:
        raise Unsupported("registro 0 da MFT ilegível")
    _flags, base, _names, _std, pieces, has_list = parsed
    if base or has_list or len(pieces) != 1 or not pieces[0][1] or not pieces[0][3]:
        raise Unsupported("MFT fragmentada demais (lista de atributos)")
    return pieces[0][3], pieces[0][2]
