"""Decodificação (pura, sem Win32) do buffer de FSCTL_GET_RETRIEVAL_POINTERS.

Fica fora de `backends/windows.py` para poder ser testada e medida em qualquer sistema.

Layout de RETRIEVAL_POINTERS_BUFFER (little-endian):
    DWORD ExtentCount; (4 bytes de preenchimento); LARGE_INTEGER StartingVcn;
    { LARGE_INTEGER NextVcn; LARGE_INTEGER Lcn; } Extents[ExtentCount]
"""
from __future__ import annotations

import struct
from itertools import repeat
from operator import sub
from typing import Dict, List, Tuple

from .model import Extent

_new = tuple.__new__
_HEADER = struct.Struct("<I4xq")
_BODY: Dict[int, struct.Struct] = {}  # um Struct por quantidade de extents (cache)


def _body(count: int) -> struct.Struct:
    s = _BODY.get(count)
    if s is None:
        s = _BODY[count] = struct.Struct("<%dq" % (count * 2))
    return s


def parse_retrieval(buf, length: int = -1) -> Tuple[List[Extent], int]:
    """Converte o buffer em extents. Devolve (extents, próximo_vcn).

    `buf` pode ser bytes ou qualquer objeto com protocolo de buffer (p.ex. um buffer ctypes,
    sem cópia). `length` limita os bytes válidos (retorno de DeviceIoControl); -1 = tudo.
    Levanta ValueError se o buffer estiver truncado."""
    size = len(buf) if length < 0 else length
    if size < 16:
        raise ValueError("buffer de retrieval pointers incompleto")
    count, vcn = _HEADER.unpack_from(buf, 0)
    if size < 16 + 16 * count:
        raise ValueError("buffer de retrieval pointers truncado")
    if count == 0:
        return [], vcn
    vals = _body(count).unpack_from(buf, 16)
    nxt = vals[0::2]  # NextVcn de cada extent
    lcns = vals[1::2]
    if count == 1:  # caso mais comum: um extent só
        return [_new(Extent, (vcn, lcns[0], nxt[0] - vcn))], nxt[0]
    starts = (vcn,) + nxt[:-1]
    # tudo em C (map/zip), sem laço Python por extent; tuple.__new__ evita o __new__ Python do NamedTuple
    return list(map(_new, repeat(Extent), zip(starts, lcns, map(sub, nxt, starts)))), nxt[-1]
