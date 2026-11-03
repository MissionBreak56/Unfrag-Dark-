"""Interface que um volume precisa oferecer ao motor."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator, Optional

from ..model import FileInfo


class MoveError(Exception):
    """Falha ao mover clusters (arquivo em uso, destino ocupado, acesso negado...)."""


class VolumeBackend(ABC):
    cluster_size: int = 4096
    total_clusters: int = 0
    # arquivos que cabem em 1 cluster não podem fragmentar: o backend pode contá-los
    # sem abri-los (muito mais rápido) em vez de devolvê-los em iter_files
    trivial_files: int = 0
    trivial_bytes: int = 0

    @property
    def is_ssd(self) -> Optional[bool]:
        """True/False quando conhecido; None quando não foi possível detectar."""
        return None

    @abstractmethod
    def read_bitmap(self) -> bytes:
        """Bitmap do volume: bit 1 = cluster alocado (LSB primeiro)."""

    @abstractmethod
    def iter_files(self, root: Optional[str], recurse: bool) -> Iterator[FileInfo]:
        """Percorre arquivos (ou um único arquivo, se `root` for um arquivo)."""

    @abstractmethod
    def get_file(self, path: str) -> Optional[FileInfo]:
        """Lê de novo os extents de um arquivo."""

    @abstractmethod
    def move_clusters(self, path: str, vcn: int, lcn: int, count: int) -> None:
        """Move `count` clusters do arquivo, a partir do VCN `vcn`, para o LCN `lcn`."""

    def close(self) -> None:  # pragma: no cover - opcional
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
