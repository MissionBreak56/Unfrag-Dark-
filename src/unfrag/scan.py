"""Varredura em paralelo: aplica uma função de E/S a muitos itens mantendo a ordem."""
from __future__ import annotations

import gc
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Callable, Iterable, Iterator, Optional, TypeVar

T = TypeVar("T")
R = TypeVar("R")


@contextmanager
def gc_paused():
    """Pausa o coletor de lixo cíclico durante a varredura.

    A análise cria milhões de objetos pequenos (extents, FileInfo) que ficam vivos até o fim;
    o GC geracional reexamina esses objetos várias vezes e chegou a custar mais de 2/3 do
    tempo de CPU em Python da análise (medido: 1,2 s -> 0,35 s para 300 mil arquivos).
    Esses objetos não formam ciclos, então pausar o GC não vaza memória. Restaura o estado
    anterior ao sair (seguro se for chamado de forma aninhada)."""
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()


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
