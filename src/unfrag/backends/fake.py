"""Volume simulado em memória: usado nos testes e no modo --demo.

Reproduz a semântica do FSCTL_MOVE_FILE: os clusters de destino precisam
estar livres; os de origem são liberados após a cópia."""
from __future__ import annotations

import random
import time
from typing import Dict, Iterator, List, Optional

from ..model import Extent, FileInfo
from .base import MoveError, VolumeBackend


def _extents_from_lcns(lcns: List[int]) -> List[Extent]:
    out: List[Extent] = []
    start = 0
    for i in range(1, len(lcns) + 1):
        if i == len(lcns) or lcns[i] != lcns[i - 1] + 1:
            out.append(Extent(start, lcns[start], i - start))
            start = i
    return out


class FakeVolume(VolumeBackend):
    def __init__(self, total_clusters: int = 20000, cluster_size: int = 4096,
                 ssd: Optional[bool] = False, delay: float = 0.0, count_trivial: bool = False):
        self.total_clusters = total_clusters
        self.cluster_size = cluster_size
        self._ssd = ssd
        self.delay = delay
        self.count_trivial = count_trivial
        self.owner: List[Optional[str]] = [None] * total_clusters
        self.files: Dict[str, List[int]] = {}
        self.skip: Dict[str, str] = {}
        self.alias: Dict[str, str] = {}  # hard links: nome extra -> nome real
        self.fail_paths: set = set()
        self.moves = 0
        self.trims = 0

    @property
    def is_ssd(self) -> Optional[bool]:
        return self._ssd

    @property
    def supports_trim(self) -> bool:
        return True

    def retrim(self, cancel=None):
        if cancel is not None and cancel.is_set():
            return False, "cancelado"
        if self.delay:
            time.sleep(self.delay * 50)
        self.trims += 1
        return True, "TRIM simulado concluído"

    # --- construção --------------------------------------------------------
    def add_file(self, path: str, lcns: List[int], skip: Optional[str] = None) -> None:
        for c in lcns:
            if self.owner[c] is not None:
                raise ValueError("cluster %d ocupado" % c)
            self.owner[c] = path
        self.files[path] = list(lcns)
        if skip:
            self.skip[path] = skip

    def remove_file(self, path: str) -> None:
        for c in self.files.pop(path):
            self.owner[c] = None
        self.skip.pop(path, None)

    def free_clusters(self) -> List[int]:
        return [i for i, o in enumerate(self.owner) if o is None]

    def check_invariants(self) -> None:
        seen = {}
        for path, lcns in self.files.items():
            for c in lcns:
                assert self.owner[c] == path, (path, c)
                assert c not in seen, "cluster compartilhado %d" % c
                seen[c] = path
        used = sum(1 for o in self.owner if o is not None)
        assert used == len(seen)

    @classmethod
    def demo(cls, seed: int = 1, total_clusters: int = 24000, delay: float = 0.0,
             target_used: float = 0.62, ssd: Optional[bool] = False) -> "FakeVolume":
        rnd = random.Random(seed)
        v = cls(total_clusters, ssd=ssd, delay=delay)
        names = ["Windows\\System32\\mod%03d.dll", "Users\\demo\\Documents\\doc%03d.docx",
                 "Program Files\\App\\data%03d.bin", "Users\\demo\\Videos\\clip%03d.mp4"]
        cursor, n = 0, 0
        # fase 1: arquivos contíguos até ~45% do volume
        while cursor < total_clusters * 0.45:
            size = rnd.choice([2, 4, 8, 16, 32, 64, 120, 300, 900])
            if cursor + size >= total_clusters:
                break
            v.add_file("C:\\" + rnd.choice(names) % n, list(range(cursor, cursor + size)))
            cursor += size
            n += 1
        # fase 2: apaga ~35% para abrir buracos
        victims = rnd.sample(sorted(v.files), int(len(v.files) * 0.35))
        for p in victims:
            v.remove_file(p)
        # fase 3: grava arquivos novos que se espalham por pedaços embaralhados
        # dos buracos (gera fragmentação realista), depois usa o final do volume
        holes = [c for c in v.free_clusters() if c < cursor]
        pieces, i = [], 0
        while i < len(holes):
            step = rnd.randint(3, 40)
            pieces.append(holes[i:i + step])
            i += step
        rnd.shuffle(pieces)
        seq = [c for piece in pieces for c in piece] + [c for c in v.free_clusters() if c >= cursor]
        used = sum(1 for o in v.owner if o is not None)
        pos = 0
        while pos < len(seq) and used < total_clusters * target_used:
            size = rnd.choice([6, 12, 24, 48, 90, 200, 450])
            chunk = seq[pos:pos + size]
            if len(chunk) < size:
                break
            v.add_file("C:\\" + rnd.choice(names) % n, chunk)
            pos += size
            used += size
            n += 1
        # alguns arquivos que não podem ser movidos
        for p in list(v.files)[:3]:
            v.skip[p] = "arquivo de sistema"
        return v

    # --- VolumeBackend -----------------------------------------------------
    def read_bitmap(self) -> bytes:
        out = bytearray((self.total_clusters + 7) // 8)
        for i, o in enumerate(self.owner):
            if o is not None:
                out[i >> 3] |= 1 << (i & 7)
        return bytes(out)

    def add_hardlink(self, alias: str, target: str) -> None:
        self.alias[alias] = target

    def _real(self, path: str) -> str:
        return self.alias.get(path, path)

    def _info(self, path: str) -> FileInfo:
        lcns = self.files[self._real(path)]
        return FileInfo(path, len(lcns) * self.cluster_size, _extents_from_lcns(lcns),
                        self.skip.get(self._real(path)))

    def iter_files(self, root: Optional[str], recurse: bool) -> Iterator[FileInfo]:
        prefix = (root or "").lower().rstrip("\\")
        self.trivial_files = self.trivial_bytes = 0
        for path in sorted(list(self.files) + list(self.alias)):
            low = path.lower()
            if prefix and low != prefix:
                if not low.startswith(prefix + "\\"):
                    continue
                if not recurse and "\\" in low[len(prefix) + 1:]:
                    continue
            if self.count_trivial and len(self.files[self._real(path)]) <= 1 and self._real(path) not in self.skip:
                self.trivial_files += 1
                self.trivial_bytes += self.cluster_size
                continue
            yield self._info(path)

    def get_file(self, path: str) -> Optional[FileInfo]:
        return self._info(path) if self._real(path) in self.files else None

    def move_clusters(self, path: str, vcn: int, lcn: int, count: int) -> None:
        path = self._real(path)
        if path in self.fail_paths:
            raise MoveError("acesso negado (simulado)")
        lcns = self.files.get(path)
        if lcns is None or vcn < 0 or vcn + count > len(lcns):
            raise MoveError("intervalo de VCN inválido")
        if lcn < 0 or lcn + count > self.total_clusters:
            raise MoveError("destino fora do volume")
        for c in range(lcn, lcn + count):
            if self.owner[c] is not None:
                raise MoveError("cluster de destino %d ocupado" % c)
        for i in range(count):
            old = lcns[vcn + i]
            self.owner[old] = None
            lcns[vcn + i] = lcn + i
            self.owner[lcn + i] = path
        self.moves += 1
        if self.delay:
            time.sleep(self.delay)
