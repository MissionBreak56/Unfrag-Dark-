"""Mapa de espaço livre: lista ordenada de corridas livres de clusters."""
from __future__ import annotations

import bisect
from typing import Iterable, List, Optional, Tuple


def runs_from_bitmap(bitmap: bytes, total: int) -> List[Tuple[int, int]]:
    """Converte o bitmap do volume (bit 1 = cluster em uso, LSB primeiro)
    em uma lista de corridas livres (início, tamanho)."""
    runs: List[Tuple[int, int]] = []
    run_start: Optional[int] = None
    for i, b in enumerate(bitmap):
        base = i * 8
        if base >= total:
            break
        if b == 0xFF:
            if run_start is not None:
                runs.append((run_start, base - run_start))
                run_start = None
            continue
        if b == 0:
            if run_start is None:
                run_start = base
            continue
        for bit in range(8):
            c = base + bit
            if c >= total:
                break
            if (b >> bit) & 1:
                if run_start is not None:
                    runs.append((run_start, c - run_start))
                    run_start = None
            elif run_start is None:
                run_start = c
    if run_start is not None:
        runs.append((run_start, total - run_start))
    return runs


class FreeMap:
    def __init__(self, runs: Optional[Iterable[Tuple[int, int]]] = None):
        self.starts: List[int] = []
        self.lens: List[int] = []
        for s, n in runs or []:
            self.starts.append(s)
            self.lens.append(n)

    @classmethod
    def from_bitmap(cls, bitmap: bytes, total: int) -> "FreeMap":
        return cls(runs_from_bitmap(bitmap, total))

    # --- consultas ---------------------------------------------------------
    def runs(self) -> List[Tuple[int, int]]:
        return list(zip(self.starts, self.lens))

    @property
    def total_free(self) -> int:
        return sum(self.lens)

    @property
    def largest(self) -> int:
        return max(self.lens) if self.lens else 0

    def __len__(self) -> int:
        return len(self.starts)

    def first_fit(self, size: int, before: Optional[int] = None) -> Optional[int]:
        """Início da primeira corrida livre com `size` clusters.
        Com `before`, só aceita corridas que começam antes desse cluster."""
        for s, n in zip(self.starts, self.lens):
            if before is not None and s >= before:
                return None
            if n >= size:
                return s
        return None

    def last_fit(self, size: int, after: int) -> Optional[int]:
        """Posição mais ao fim do volume onde `size` clusters cabem,
        desde que fique depois de `after`."""
        for s, n in zip(reversed(self.starts), reversed(self.lens)):
            if n >= size:
                target = s + n - size
                return target if target > after else None
        return None

    # --- mutações ----------------------------------------------------------
    def allocate(self, start: int, n: int) -> None:
        idx = bisect.bisect_right(self.starts, start) - 1
        if idx < 0 or start + n > self.starts[idx] + self.lens[idx]:
            raise ValueError("intervalo não está livre: %d+%d" % (start, n))
        rs, rl = self.starts[idx], self.lens[idx]
        del self.starts[idx]
        del self.lens[idx]
        pos = idx
        if start > rs:
            self.starts.insert(pos, rs)
            self.lens.insert(pos, start - rs)
            pos += 1
        tail = rs + rl - (start + n)
        if tail > 0:
            self.starts.insert(pos, start + n)
            self.lens.insert(pos, tail)

    def release(self, start: int, n: int) -> None:
        idx = bisect.bisect_left(self.starts, start)
        if idx > 0 and self.starts[idx - 1] + self.lens[idx - 1] > start:
            raise ValueError("intervalo já livre: %d+%d" % (start, n))
        if idx < len(self.starts) and start + n > self.starts[idx]:
            raise ValueError("intervalo já livre: %d+%d" % (start, n))
        merge_prev = idx > 0 and self.starts[idx - 1] + self.lens[idx - 1] == start
        merge_next = idx < len(self.starts) and start + n == self.starts[idx]
        if merge_prev and merge_next:
            self.lens[idx - 1] += n + self.lens[idx]
            del self.starts[idx]
            del self.lens[idx]
        elif merge_prev:
            self.lens[idx - 1] += n
        elif merge_next:
            self.starts[idx] = start
            self.lens[idx] += n
        else:
            self.starts.insert(idx, start)
            self.lens.insert(idx, n)
