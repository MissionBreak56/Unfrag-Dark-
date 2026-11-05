"""Reduz o volume inteiro a N células para desenhar o mapa de disco."""
from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

from .model import FileInfo

FREE, USED, FRAGMENTED, UNMOVABLE = 0, 1, 2, 3
USED_FULL = 4  # só com shade=True: célula em uso e totalmente ocupada (tom mais escuro no mapa)


def build_map(total_clusters: int, free_runs: Iterable[Tuple[int, int]],
              files: Iterable[FileInfo], cells: int, shade: bool = False) -> List[int]:
    if cells <= 0 or total_clusters <= 0:
        return []
    per = total_clusters / cells
    free = [0.0] * cells
    frag = [False] * cells
    unmov = [False] * cells

    for s, n in free_runs:
        c0 = min(cells - 1, int(s / per))
        c1 = min(cells - 1, int((s + n - 1) / per))
        for c in range(c0, c1 + 1):
            lo = max(s, c * per)
            hi = min(s + n, (c + 1) * per)
            if hi > lo:
                free[c] += hi - lo

    for f in files:
        mark = None
        if not f.movable and f.extents:
            mark = unmov
        elif f.fragments > 1:
            mark = frag
        if mark is None:
            continue
        for e in f.extents:
            if e.is_hole:
                continue
            c0 = min(cells - 1, int(e.lcn / per))
            c1 = min(cells - 1, int((e.lcn + e.length - 1) / per))
            for c in range(c0, c1 + 1):
                mark[c] = True

    out: List[int] = []
    for c in range(cells):
        if free[c] >= per * 0.98:
            out.append(FREE)
        elif unmov[c]:
            out.append(UNMOVABLE)
        elif frag[c]:
            out.append(FRAGMENTED)
        elif shade and free[c] < per * 0.02:
            out.append(USED_FULL)
        else:
            out.append(USED)
    return out


def summary(cells: Sequence[int]) -> dict:
    return {k: sum(1 for c in cells if c == v)
            for k, v in (("free", FREE), ("used", USED), ("fragmented", FRAGMENTED),
                         ("unmovable", UNMOVABLE))} | {"used": sum(1 for c in cells if c in (USED, USED_FULL))}


# --- detalhe de um bloco (quadrado do mapa) -------------------------------------------------

def cell_span(total_clusters: int, cells: int, i: int) -> Tuple[int, int]:
    """Intervalo [lo, hi) de clusters que `build_map` atribui à célula `i`."""
    per = total_clusters / cells

    def start(c: int) -> int:
        if c <= 0:
            return 0
        if c >= cells:
            return total_clusters
        x = int(c * per)
        while x > 0 and int((x - 1) / per) >= c:  # acerta erros de ponto flutuante
            x -= 1
        while int(x / per) < c:
            x += 1
        return min(x, total_clusters)

    return start(i), start(i + 1)


def cell_free_clusters(free_runs: Iterable[Tuple[int, int]], lo: int, hi: int) -> int:
    """Quantos clusters livres existem em [lo, hi)."""
    total = 0
    for s, n in free_runs:
        a, b = max(s, lo), min(s + n, hi)
        if b > a:
            total += b - a
    return total


def fragmented_in_cell(files: Iterable[FileInfo], lo: int, hi: int) -> List[Tuple[FileInfo, int, int]]:
    """Arquivos fragmentados (mais de um pedaço) com dados em [lo, hi).

    Devolve (arquivo, nº de pedaços dentro do bloco, clusters dentro do bloco)."""
    out: List[Tuple[FileInfo, int, int]] = []
    for f in files:
        if f.fragments <= 1:
            continue
        pieces = clusters = 0
        for e in f.extents:
            if e.lcn < 0:
                continue
            a, b = max(e.lcn, lo), min(e.lcn + e.length, hi)
            if b > a:
                pieces += 1
                clusters += b - a
        if pieces:
            out.append((f, pieces, clusters))
    return out


def unmovable_in_cell(files: Iterable[FileInfo], lo: int, hi: int) -> int:
    """Quantos arquivos não movíveis têm dados em [lo, hi)."""
    n = 0
    for f in files:
        if f.movable or not f.extents:
            continue
        if any(e.lcn >= 0 and e.lcn < hi and e.lcn + e.length > lo for e in f.extents):
            n += 1
    return n
