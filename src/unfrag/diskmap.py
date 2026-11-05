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
