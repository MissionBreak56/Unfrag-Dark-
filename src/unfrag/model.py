"""Estruturas de dados compartilhadas pelo motor, pela CLI e pela GUI."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, NamedTuple, Optional


class Extent(NamedTuple):
    """Um trecho de arquivo: `length` clusters virtuais a partir de `vcn`,
    gravados a partir do cluster lógico `lcn` (lcn < 0 = buraco/sparse).

    NamedTuple (imutável, como antes) é bem mais barato de criar e de ordenar do que um
    dataclass congelado: a análise cria um Extent por trecho de cada arquivo do disco."""

    vcn: int
    lcn: int
    length: int

    @property
    def is_hole(self) -> bool:
        return self.lcn < 0


@dataclass
class FileInfo:
    path: str
    size: int
    extents: List[Extent] = field(default_factory=list)
    skip: Optional[str] = None  # motivo pelo qual o arquivo não pode ser movido

    def _ordered(self) -> List[Extent]:
        # vcn é o primeiro campo e é único por arquivo: ordenar a tupla = ordenar por vcn
        return sorted(self.extents)

    @property
    def fragments(self) -> int:
        """Número de pedaços fisicamente separados (extents adjacentes contam como um)."""
        ex = self.extents
        if len(ex) == 1:  # caso mais comum: um único trecho
            return 0 if ex[0].lcn < 0 else 1
        count = 0
        prev_end = None
        for e in self._ordered():
            if e.is_hole:
                prev_end = None
                continue
            if prev_end is None or e.lcn != prev_end:
                count += 1
            prev_end = e.lcn + e.length
        return count

    @property
    def clusters(self) -> int:
        return sum(e.length for e in self.extents if not e.is_hole)

    @property
    def has_holes(self) -> bool:
        for e in self.extents:
            if e.lcn < 0:
                return True
        return False

    @property
    def start_lcn(self) -> int:
        for e in self._ordered():
            if not e.is_hole:
                return e.lcn
        return -1

    @property
    def movable(self) -> bool:
        return self.skip is None and bool(self.extents) and not self.has_holes


@dataclass
class DriveInfo:
    letter: str
    label: str
    fs: str
    kind: str  # "HDD", "SSD", "Removível", "Desconhecido"
    total_bytes: int
    free_bytes: int
    supported: bool = True
    note: str = ""


@dataclass
class AnalysisResult:
    cluster_size: int = 0
    total_clusters: int = 0
    free_clusters: int = 0
    free_runs: int = 0
    largest_free_run: int = 0
    files: int = 0
    fragmented_files: int = 0
    fragments: int = 0
    total_bytes: int = 0
    fragmented_bytes: int = 0
    unmovable_files: int = 0
    seconds: float = 0.0

    @property
    def fragmentation_percent(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return min(100.0, 100.0 * self.fragmented_bytes / self.total_bytes)
