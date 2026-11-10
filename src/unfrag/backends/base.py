"""Interface que um volume precisa oferecer ao motor."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator, Optional, Tuple

from ..model import FileInfo


class MoveError(Exception):
    """Falha ao mover clusters (arquivo em uso, destino ocupado, acesso negado...)."""


def root_error(root: str, letter: str) -> Optional[str]:
    """Mensagem de erro se `root` (pasta/arquivo da análise parcial) NÃO estiver na unidade `letter`.

    Misturar caminhos de outra unidade com o mapa de clusters desta daria resultados errados."""
    p = root[4:] if root.startswith("\\\\?\\") else root
    if len(p) >= 2 and p[1] == ":":
        if p[0].upper() != letter.upper().rstrip(":"):
            return "o caminho %s não está na unidade %s:" % (root, letter.upper().rstrip(":"))
    elif p.startswith("\\\\"):
        return "caminhos de rede não são suportados (%s)" % root
    return None


class VolumeBackend(ABC):
    cluster_size: int = 4096
    total_clusters: int = 0
    # arquivos que cabem em 1 cluster não podem fragmentar: o backend pode contá-los
    # sem abri-los (muito mais rápido) em vez de devolvê-los em iter_files
    trivial_files: int = 0
    trivial_bytes: int = 0
    # threads de leitura na análise (0 = automático); só o backend do Windows usa
    threads: int = 0

    @property
    def is_ssd(self) -> Optional[bool]:
        """True/False quando conhecido; None quando não foi possível detectar."""
        return None

    @property
    def supports_trim(self) -> bool:
        """O backend sabe pedir o TRIM (otimização) deste volume?"""
        return False

    def retrim(self, cancel=None) -> Tuple[bool, str]:
        """Envia o TRIM do espaço livre ao SSD. Devolve (deu certo, texto do resultado).
        `cancel` (threading.Event) interrompe a operação quando acionado."""
        raise NotImplementedError("este backend não oferece TRIM")

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
