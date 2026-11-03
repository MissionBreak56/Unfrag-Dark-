"""Varredura em paralelo: aplica uma função de E/S a muitos itens mantendo a ordem."""
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, Iterator, Optional, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def parallel_scan(items: Iterable[T], fn: Callable[[T], Optional[R]], workers: int = 8,
                  batch: int = 64, max_pending: Optional[int] = None) -> Iterator[R]:
    """Chama `fn(item)` em `workers` threads (em lotes de `batch`), devolvendo os resultados
    não nulos na mesma ordem dos itens. Com `workers <= 1` roda sem threads.

    Como a espera pelo disco (CreateFile/DeviceIoControl) libera o GIL, várias threads
    escondem a latência e aceleram muito a análise de milhares de arquivos."""
    if workers <= 1:
        for it in items:
            r = fn(it)
            if r is not None:
                yield r
        return

    def run(chunk):
        out = []
        for it in chunk:
            r = fn(it)
            if r is not None:
                out.append(r)
        return out

    limit = max_pending or workers * 4
    pending: deque = deque()
    chunk: list = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="unfrag-scan") as ex:
        try:
            for it in items:
                chunk.append(it)
                if len(chunk) >= batch:
                    pending.append(ex.submit(run, chunk))
                    chunk = []
                    while len(pending) >= limit:
                        yield from pending.popleft().result()
            if chunk:
                pending.append(ex.submit(run, chunk))
            while pending:
                yield from pending.popleft().result()
        finally:
            for f in pending:
                f.cancel()
