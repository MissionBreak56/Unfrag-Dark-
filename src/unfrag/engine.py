"""Motor de análise e desfragmentação (independente de plataforma).

Estratégia (código próprio, desenhado a partir da documentação pública da API
de desfragmentação do Windows):

* defrag      - arquivos fragmentados são regravados em uma única corrida livre
                (first-fit), os menores primeiro.
* quick       - como `defrag`, mas só arquivos de até 256 MiB.
* freespace   - `defrag` + compactação: arquivos contíguos do fim do volume são
                puxados para os primeiros buracos que os comportam, agrupando
                o espaço livre no final.
* opcional    - mover arquivos grandes/por extensão para o fim do volume.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .backends.base import MoveError, VolumeBackend
from .freespace import FreeMap
from .model import AnalysisResult, Extent, FileInfo

ProgressFn = Callable[[str, int, int, str], None]
MIB = 1 << 20
QUICK_MAX_BYTES = 256 * MIB
MOVE_CHUNK_CLUSTERS = 32768


class Cancelled(Exception):
    pass


class SsdSkipped(Exception):
    """O volume é SSD e o usuário não forçou a desfragmentação."""


@dataclass
class Options:
    mode: str = "defrag"  # defrag | quick | freespace
    root: Optional[str] = None
    recurse: bool = True
    move_large_mb: int = 0
    move_exts: Sequence[str] = ()
    force_ssd: bool = False
    min_fragmentation: float = 0.0
    verify: bool = True


@dataclass
class Report:
    mode: str
    before: Optional[AnalysisResult] = None
    after: Optional[AnalysisResult] = None
    defragmented: int = 0
    compacted: int = 0
    moved_to_end: int = 0
    clusters_moved: int = 0
    failed: List[Tuple[str, str]] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    cancelled: bool = False
    seconds: float = 0.0


class Engine:
    def __init__(self, backend: VolumeBackend, progress: Optional[ProgressFn] = None,
                 cancel: Optional[threading.Event] = None,
                 chunk: int = MOVE_CHUNK_CLUSTERS):
        self.backend = backend
        self.progress = progress
        self.cancel = cancel or threading.Event()
        self.chunk = chunk
        self.files: Dict[str, FileInfo] = {}
        self.free = FreeMap()
        self.result = AnalysisResult()

    # --- utilidades --------------------------------------------------------
    def _emit(self, phase: str, done: int, total: int, msg: str = "") -> None:
        if self.progress:
            self.progress(phase, done, total, msg)

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()

    # --- análise -----------------------------------------------------------
    def analyze(self, root: Optional[str] = None, recurse: bool = True) -> AnalysisResult:
        self.files = {}
        bitmap = self.backend.read_bitmap()
        self.free = FreeMap.from_bitmap(bitmap, self.backend.total_clusters)
        n = 0
        for f in self.backend.iter_files(root, recurse):
            self.files[f.path] = f
            n += 1
            if n % 200 == 0:
                self._check_cancel()
                self._emit("scan", n, 0, f.path)
        self._emit("scan", n, n, "")
        self.result = self._summarize()
        return self.result

    def _summarize(self) -> AnalysisResult:
        r = AnalysisResult(cluster_size=self.backend.cluster_size,
                           total_clusters=self.backend.total_clusters,
                           free_clusters=self.free.total_free,
                           free_runs=len(self.free),
                           largest_free_run=self.free.largest)
        for f in self.files.values():
            if not f.extents:
                continue
            r.files += 1
            r.total_bytes += f.size
            if not f.movable:
                r.unmovable_files += 1
            frags = f.fragments
            if frags > 1:
                r.fragmented_files += 1
                r.fragments += frags
                r.fragmented_bytes += f.size
        return r

    # --- desfragmentação ---------------------------------------------------
    def defragment(self, opts: Options) -> Report:
        t0 = time.time()
        report = Report(mode=opts.mode)
        try:
            report.before = self.analyze(opts.root, opts.recurse)
            if self.backend.is_ssd and not opts.force_ssd:
                raise SsdSkipped("o volume é um SSD; use force_ssd para desfragmentar mesmo assim")
            if (report.before.fragmentation_percent < opts.min_fragmentation
                    and opts.mode != "freespace"):
                report.notes.append("fragmentação abaixo do mínimo (%.1f%% < %.1f%%); nada a fazer"
                                    % (report.before.fragmentation_percent, opts.min_fragmentation))
            else:
                self._defrag_pass(opts, report)
                if opts.mode == "freespace":
                    self._compact(opts, report)
                if opts.move_large_mb > 0 or opts.move_exts:
                    self._to_end(opts, report)
        except Cancelled:
            report.cancelled = True
        if report.cancelled or not opts.verify:
            self.result = self._summarize()
            report.after = self.result
        else:
            report.after = self.analyze(opts.root, opts.recurse)
        report.seconds = time.time() - t0
        return report

    def _defrag_pass(self, opts: Options, report: Report) -> None:
        cands = [f for f in self.files.values() if f.movable and f.fragments > 1]
        if opts.mode == "quick":
            cands = [f for f in cands if f.size <= QUICK_MAX_BYTES]
        cands.sort(key=lambda f: f.clusters)
        for i, f in enumerate(cands):
            self._check_cancel()
            self._emit("defrag", i, len(cands), f.path)
            target = self.free.first_fit(f.clusters)
            if target is None:
                report.skipped.append((f.path, "sem espaço livre contíguo suficiente"))
                continue
            if self._relocate(f, target, report):
                report.defragmented += 1
        self._emit("defrag", len(cands), len(cands), "")

    def _is_large(self, f: FileInfo, opts: Options) -> bool:
        if opts.move_large_mb > 0 and f.size >= opts.move_large_mb * MIB:
            return True
        low = f.path.lower()
        return any(low.endswith(e.lower()) for e in opts.move_exts if e)

    def _compact(self, opts: Options, report: Report, max_passes: int = 4) -> None:
        """Puxa arquivos do fim do volume para os primeiros buracos que os comportam,
        repetindo até estabilizar (ou `max_passes`)."""
        for _ in range(max_passes):
            cands = [f for f in self.files.values()
                     if f.movable and f.fragments == 1 and not self._is_large(f, opts)]
            cands.sort(key=lambda f: -f.start_lcn)
            moved = 0
            for i, f in enumerate(cands):
                self._check_cancel()
                self._emit("compact", i, len(cands), f.path)
                target = self.free.first_fit(f.clusters, before=f.start_lcn)
                if target is None:
                    continue
                if self._relocate(f, target, report):
                    report.compacted += 1
                    moved += 1
            self._emit("compact", len(cands), len(cands), "")
            if not moved:
                break

    def _to_end(self, opts: Options, report: Report) -> None:
        cands = [f for f in self.files.values()
                 if f.movable and f.fragments == 1 and self._is_large(f, opts)]
        cands.sort(key=lambda f: -f.clusters)
        for i, f in enumerate(cands):
            self._check_cancel()
            self._emit("toend", i, len(cands), f.path)
            target = self.free.last_fit(f.clusters, after=f.start_lcn)
            if target is None:
                continue
            if self._relocate(f, target, report):
                report.moved_to_end += 1
        self._emit("toend", len(cands), len(cands), "")

    # --- movimentação ------------------------------------------------------
    def _relocate(self, f: FileInfo, target: int, report: Report) -> bool:
        old = [(e.lcn, e.length) for e in f.extents if not e.is_hole]
        first_vcn = min(e.vcn for e in f.extents)
        clusters = f.clusters
        done = 0
        try:
            while done < clusters:
                n = min(self.chunk, clusters - done)
                self.backend.move_clusters(f.path, first_vcn + done, target + done, n)
                done += n
        except (MoveError, OSError) as exc:
            report.failed.append((f.path, str(exc)))
            self._resync(f.path)
            return False
        for s, n in old:
            self.free.release(s, n)
        self.free.allocate(target, clusters)
        self.files[f.path] = FileInfo(f.path, f.size, [Extent(first_vcn, target, clusters)], f.skip)
        report.clusters_moved += clusters
        return True

    def _resync(self, path: str) -> None:
        """Após uma falha, relê o bitmap e o arquivo para não confiar em estado velho."""
        self.free = FreeMap.from_bitmap(self.backend.read_bitmap(), self.backend.total_clusters)
        fresh = self.backend.get_file(path)
        if fresh is not None:
            self.files[path] = fresh
