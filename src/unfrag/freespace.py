"""Mapa de espaço livre: lista ordenada de corridas livres de clusters."""
from __future__ import annotations

import bisect
import re
from typing import Iterable, List, Optional, Tuple


_SEG = re.compile(rb"\xff+|\x00+|[^\x00\xff]+")

# Limiares (em clusters) das "classes de tamanho" usadas para acelerar o first-fit:
# 4 degraus por oitava (1, 2, 2, 3, 4, 5, 6, 8, ...), até 2**40 clusters.
_THRESH: List[int] = sorted({int(2 ** (j / 4)) for j in range(4 * 40 + 1)})


def runs_from_bitmap(bitmap: bytes, total: int) -> List[Tuple[int, int]]:
    """Converte o bitmap do volume (bit 1 = cluster em uso, LSB primeiro)
    em uma lista de corridas livres (início, tamanho).

    Rápido: uma regex separa o bitmap em blocos homogêneos (só 0xFF, só 0x00 ou
    misto); só os bytes mistos são examinados bit a bit."""
    runs: List[Tuple[int, int]] = []
    run_start: Optional[int] = None
    bm = bitmap[:(total + 7) // 8]
    for m in _SEG.finditer(bm):
        base = m.start() * 8
        first = bm[m.start()]
        if first == 0xFF:
            if run_start is not None:
                runs.append((run_start, base - run_start))
                run_start = None
        elif first == 0x00:
            if run_start is None:
                run_start = base
        else:
            for off, byte in enumerate(m.group()):
                bbase = base + off * 8
                for bit in range(8):
                    c = bbase + bit
                    if c >= total:
                        break
                    if (byte >> bit) & 1:
                        if run_start is not None:
                            runs.append((run_start, c - run_start))
                            run_start = None
                    elif run_start is None:
                        run_start = c
    if run_start is not None and run_start < total:
        runs.append((run_start, total - run_start))
    return runs


class FreeMap:
    def __init__(self, runs: Optional[Iterable[Tuple[int, int]]] = None):
        self.starts: List[int] = []
        self.lens: List[int] = []
        # _hint[k] = posição P tal que TODA corrida livre que começa antes de P tem menos de
        # _THRESH[k] clusters. O first-fit pula direto para P em vez de varrer desde o início.
        # Alocar só encolhe corridas (a dica continua válida); liberar pode criar/fundir uma
        # corrida maior, e aí `release` recua as dicas afetadas.
        self._hint: List[int] = [0] * len(_THRESH)
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
        if size <= 0:
            size = 1
        k = max(0, bisect.bisect_right(_THRESH, size) - 1)
        t = _THRESH[k]
        starts, lens = self.starts, self.lens
        count = len(starts)
        i = bisect.bisect_left(starts, self._hint[k])
        advancing = True  # ainda dentro do prefixo de corridas com menos de `t` clusters
        while i < count:
            s = starts[i]
            if before is not None and s >= before:
                break
            n = lens[i]
            if advancing and n >= t:
                self._hint[k] = s
                advancing = False
            if n >= size:
                return s
            i += 1
        if advancing:
            # nada com `t` clusters ou mais no trecho percorrido
            self._hint[k] = starts[i] if i < count else (starts[-1] + lens[-1] if count else 0)
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
            new_start, new_len = self.starts[idx - 1], self.lens[idx - 1]
        elif merge_prev:
            self.lens[idx - 1] += n
            new_start, new_len = self.starts[idx - 1], self.lens[idx - 1]
        elif merge_next:
            self.starts[idx] = start
            self.lens[idx] += n
            new_start, new_len = start, self.lens[idx]
        else:
            self.starts.insert(idx, start)
            self.lens.insert(idx, n)
            new_start, new_len = start, n
        # a corrida resultante pode caber em tamanhos que antes só existiam mais adiante
        hint = self._hint
        for k in range(bisect.bisect_right(_THRESH, new_len)):
            if hint[k] > new_start:
                hint[k] = new_start
