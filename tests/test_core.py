import threading

import pytest

from unfrag import diskmap, scheduler
from unfrag.backends.fake import FakeVolume
from unfrag.cli import main
from unfrag.engine import Engine, Options, SsdSkipped
from unfrag.freespace import FreeMap, runs_from_bitmap
from unfrag.model import Extent, FileInfo


# ---- freespace ---------------------------------------------------------
def test_runs_from_bitmap():
    # clusters 0-2 usados, 3-9 livres, 10-11 usados, 12-15 livres
    bits = [1, 1, 1] + [0] * 7 + [1, 1] + [0] * 4
    bm = bytearray(2)
    for i, b in enumerate(bits):
        if b:
            bm[i // 8] |= 1 << (i % 8)
    assert runs_from_bitmap(bytes(bm), 16) == [(3, 7), (12, 4)]
    assert runs_from_bitmap(b"\x00\x00", 12) == [(0, 12)]  # respeita o total
    assert runs_from_bitmap(b"\xff\xff", 16) == []


def test_freemap_allocate_release_merge():
    fm = FreeMap([(0, 10), (20, 10)])
    fm.allocate(3, 4)
    assert fm.runs() == [(0, 3), (7, 3), (20, 10)]
    fm.release(3, 4)
    assert fm.runs() == [(0, 10), (20, 10)]
    fm.release(10, 10)
    assert fm.runs() == [(0, 30)]
    with pytest.raises(ValueError):
        fm.release(5, 2)
    with pytest.raises(ValueError):
        FreeMap([(0, 5)]).allocate(3, 5)


def test_freemap_fits():
    fm = FreeMap([(0, 4), (10, 8), (30, 20)])
    assert fm.first_fit(5) == 10
    assert fm.first_fit(5, before=10) is None
    assert fm.last_fit(5, after=0) == 45
    assert fm.last_fit(25, after=0) is None
    assert fm.last_fit(5, after=60) is None


# ---- modelo ------------------------------------------------------------
def test_fragments_counts_adjacent_extents_as_one():
    f = FileInfo("a", 10, [Extent(0, 100, 5), Extent(5, 105, 5)])
    assert f.fragments == 1
    g = FileInfo("b", 10, [Extent(0, 100, 5), Extent(5, 200, 5)])
    assert g.fragments == 2 and g.clusters == 10 and g.start_lcn == 100
    h = FileInfo("c", 10, [Extent(0, 100, 5), Extent(5, -1, 5)])
    assert h.has_holes and not h.movable


# ---- engine ------------------------------------------------------------
def test_analyze_demo():
    vol = FakeVolume.demo(seed=1)
    r = Engine(vol).analyze()
    assert r.files > 20 and r.fragmented_files > 5
    assert 0 < r.fragmentation_percent <= 100
    assert r.free_clusters == len(vol.free_clusters())


@pytest.mark.parametrize("mode", ["defrag", "quick", "freespace"])
def test_defrag_reduces_fragmentation_and_keeps_integrity(mode):
    vol = FakeVolume.demo(seed=2)
    sizes = {p: len(c) for p, c in vol.files.items()}
    rep = Engine(vol).defragment(Options(mode=mode))
    vol.check_invariants()
    assert {p: len(c) for p, c in vol.files.items()} == sizes  # nenhum arquivo perdeu clusters
    assert rep.after.fragmented_files < rep.before.fragmented_files
    assert rep.defragmented > 0
    assert not rep.failed


def test_defrag_makes_files_contiguous_when_space_exists():
    vol = FakeVolume(1000)
    vol.add_file("C:\\a", [0, 1, 2])
    vol.add_file("C:\\frag", [10, 11, 20, 21, 30])
    vol.add_file("C:\\b", [3, 4])
    rep = Engine(vol).defragment(Options())
    assert vol.get_file("C:\\frag").fragments == 1
    assert rep.after.fragmented_files == 0
    vol.check_invariants()


def test_freespace_mode_leaves_less_scattered_free_space_than_plain_defrag():
    a, b = FakeVolume.demo(seed=4), FakeVolume.demo(seed=4)
    plain = Engine(a).defragment(Options(mode="defrag"))
    full = Engine(b).defragment(Options(mode="freespace"))
    assert full.compacted > 0
    assert full.after.free_runs < plain.after.free_runs
    assert full.after.largest_free_run > plain.after.largest_free_run
    assert full.after.fragmented_files == 0
    b.check_invariants()


def test_ssd_is_skipped_unless_forced():
    vol = FakeVolume.demo(seed=3, ssd=True)
    with pytest.raises(SsdSkipped):
        Engine(vol).defragment(Options())
    assert vol.moves == 0
    rep = Engine(vol).defragment(Options(force_ssd=True))
    assert rep.defragmented > 0


def test_min_fragmentation_threshold():
    vol = FakeVolume.demo(seed=2)
    rep = Engine(vol).defragment(Options(min_fragmentation=99.9))
    assert vol.moves == 0 and rep.notes


def test_failed_move_is_reported_and_state_stays_consistent():
    vol = FakeVolume.demo(seed=2)
    victim = next(f.path for f in Engine(vol).analyze() and Engine(vol).backend.iter_files(None, True)
                  if f.fragments > 1)
    vol.fail_paths.add(victim)
    rep = Engine(vol).defragment(Options())
    assert any(p == victim for p, _ in rep.failed)
    assert rep.defragmented > 0  # continuou com os outros
    vol.check_invariants()


def test_destination_busy_triggers_resync_not_corruption():
    vol = FakeVolume(200)
    vol.add_file("C:\\x", [0, 1, 10, 11])
    eng = Engine(vol)
    eng.analyze()
    # outro processo ocupa o destino previsto depois da análise
    vol.add_file("C:\\intruder", list(range(20, 24)))
    rep = eng.defragment(Options())  # reanalisa; deve encontrar outro destino
    assert vol.get_file("C:\\x").fragments == 1
    vol.check_invariants()
    assert not rep.failed


def test_move_large_to_end():
    vol = FakeVolume(1000)
    vol.add_file("C:\\big.iso", list(range(0, 100)))
    vol.add_file("C:\\small.txt", [200, 201])
    rep = Engine(vol).defragment(Options(move_large_mb=0, move_exts=[".iso"]))
    assert rep.moved_to_end == 1
    assert vol.get_file("C:\\big.iso").start_lcn == 900  # encostou no fim
    vol.check_invariants()


def test_cancel_stops_cleanly():
    vol = FakeVolume.demo(seed=2)
    ev = threading.Event()
    seen = []

    def prog(phase, done, total, msg):
        if phase == "defrag" and done >= 2:
            ev.set()
        seen.append(done)

    rep = Engine(vol, progress=prog, cancel=ev).defragment(Options())
    assert rep.cancelled
    vol.check_invariants()


def test_path_restriction():
    vol = FakeVolume.demo(seed=1)
    docs = [p for p in vol.files if "\\Documents\\" in p]
    r = Engine(vol).analyze("C:\\Users\\demo\\Documents")
    assert r.files == len(docs)
    single = docs[0]
    assert Engine(vol).analyze(single).files == 1


def test_unmovable_files_are_never_moved():
    vol = FakeVolume(100)
    vol.add_file("C:\\pagefile.sys", [0, 1, 10, 11], skip="arquivo de sistema")
    Engine(vol).defragment(Options())
    assert vol.files["C:\\pagefile.sys"] == [0, 1, 10, 11]


# ---- diskmap / scheduler / cli ----------------------------------------
def test_diskmap_states():
    vol = FakeVolume.demo(seed=1)
    eng = Engine(vol)
    eng.analyze()
    cells = diskmap.build_map(vol.total_clusters, eng.free.runs(), eng.files.values(), 200)
    s = diskmap.summary(cells)
    assert len(cells) == 200 and sum(s.values()) == 200
    assert s["fragmented"] > 0 and s["free"] > 0 and s["used"] > 0


def test_scheduler_args():
    a = scheduler.build_create_args("c:", "quick", "semanal", "03:30")
    assert a[:3] == ["schtasks", "/Create", "/F"]
    assert "UnfragDark\\C-weekly" in a and "WEEKLY" in a and "03:30" in a and "HIGHEST" in a
    assert "defrag C: --mode quick --skip-battery" in a[-1]
    d = scheduler.build_delete_args("C", "mensal")
    assert d[-1] == "UnfragDark\\C-monthly"


def test_cli_demo(capsys):
    assert main(["--demo", "list"]) == 0
    assert "C:" in capsys.readouterr().out
    assert main(["--demo", "analyze", "C:"]) == 0
    assert "Fragmentação" in capsys.readouterr().out
    assert main(["--demo", "defrag", "C:", "--mode", "quick"]) == 0
    out = capsys.readouterr().out
    assert "Antes" in out and "Depois" in out
    assert main(["--demo", "defrag", "E:"]) == 4  # SSD ignorado
    assert main(["schedule", "create", "C:", "--dry-run"]) == 0


# ---- velocidade da análise ---------------------------------------------
import random
import time

from unfrag.scan import parallel_scan


def _runs_reference(bitmap, total):
    runs, start = [], None
    for c in range(total):
        used = (bitmap[c >> 3] >> (c & 7)) & 1
        if used and start is not None:
            runs.append((start, c - start))
            start = None
        elif not used and start is None:
            start = c
    if start is not None:
        runs.append((start, total - start))
    return runs


@pytest.mark.parametrize("seed", range(8))
def test_fast_bitmap_parser_matches_bit_by_bit_reference(seed):
    rnd = random.Random(seed)
    parts = []
    for _ in range(rnd.randint(1, 40)):
        k = rnd.choice("fzm")
        n = rnd.randint(1, 30)
        parts.append(b"\xff" * n if k == "f" else b"\x00" * n if k == "z"
                     else bytes(rnd.randrange(256) for _ in range(n)))
    bm = b"".join(parts)
    total = len(bm) * 8 - rnd.randint(0, 7)  # total que não é múltiplo de 8
    assert runs_from_bitmap(bm, total) == _runs_reference(bm, total)


def test_fast_bitmap_parser_is_fast():
    bm = (b"\xff" * 5000 + b"\x00" * 700 + b"\x5a" * 3) * 400  # ~3,4 MB
    t = time.time()
    runs_from_bitmap(bm, len(bm) * 8)
    assert time.time() - t < 1.0


def test_parallel_scan_keeps_order_and_drops_none():
    def fn(i):
        time.sleep(0.001 * (i % 3))
        return None if i % 5 == 0 else i * 2

    items = list(range(300))
    expected = [i * 2 for i in items if i % 5]
    assert list(parallel_scan(items, fn, workers=8, batch=7)) == expected
    assert list(parallel_scan(items, fn, workers=1)) == expected


def test_parallel_scan_runs_concurrently():
    def slow(i):
        time.sleep(0.02)
        return i

    t = time.time()
    list(parallel_scan(range(64), slow, workers=8, batch=2))
    assert time.time() - t < 64 * 0.02 / 3  # bem mais rápido que sequencial


def test_parallel_scan_propagates_errors():
    def boom(i):
        if i == 10:
            raise RuntimeError("falha")
        return i

    with pytest.raises(RuntimeError):
        list(parallel_scan(range(50), boom, workers=4, batch=3))


def test_trivial_files_are_counted_without_changing_results():
    a, b = FakeVolume.demo(seed=1), FakeVolume.demo(seed=1)
    b.count_trivial = True
    # garante alguns arquivos de 1 cluster em ambos
    for v in (a, b):
        free = [c for c in v.free_clusters() if c > v.total_clusters - 100][:3]
        for i, c in enumerate(free):
            v.add_file("C:\\tiny%d.txt" % i, [c])
    ra, rb = Engine(a).analyze(), Engine(b).analyze()
    assert rb.files == ra.files and rb.total_bytes == ra.total_bytes
    assert rb.fragmented_files == ra.fragmented_files
    assert abs(rb.fragmentation_percent - ra.fragmentation_percent) < 1e-9
    assert Engine(b).analyze().seconds >= 0


# ---- regressão: "intervalo já livre" ----------------------------------------
def test_hard_links_do_not_break_free_map():
    vol = FakeVolume(2000)
    vol.add_file("C:\\real.dll", [10, 11, 50, 51, 90])
    for i in range(5):
        vol.add_hardlink("C:\\WinSxS\\link%d.dll" % i, "C:\\real.dll")
    vol.add_file("C:\\other.bin", [12, 13, 14])
    eng = Engine(vol)
    rep = eng.defragment(Options())
    assert not rep.failed
    assert eng.hardlinks == 5
    assert rep.after.files == 2  # o arquivo com 6 nomes conta uma vez
    assert vol.get_file("C:\\real.dll").fragments == 1
    # o mapa livre do motor bate com a realidade
    assert eng.free.runs() == FreeMap.from_bitmap(vol.read_bitmap(), vol.total_clusters).runs()
    vol.check_invariants()


def test_stale_state_is_tolerated_not_fatal():
    from unfrag.engine import Report

    vol = FakeVolume.demo(seed=2)
    eng = Engine(vol)
    eng.analyze()
    frag = [f for f in eng.files.values() if f.movable and f.fragments > 1]
    # outro programa move 3 desses arquivos depois da análise
    for f in frag[:3]:
        vol.move_clusters(f.path, 0, vol.free_clusters()[-f.clusters], f.clusters)
    eng.free = FreeMap.from_bitmap(vol.read_bitmap(), vol.total_clusters)
    rep = Report(mode="defrag")
    eng._defrag_pass(Options(), rep)  # usa self.files desatualizado: não pode lançar exceção
    vol.check_invariants()
    assert eng.free.runs() == FreeMap.from_bitmap(vol.read_bitmap(), vol.total_clusters).runs()
    assert any("mudou" in why for _, why in rep.skipped)


def test_inconsistent_free_map_triggers_resync_instead_of_crash():
    from unfrag.engine import Report

    vol = FakeVolume(500)
    vol.add_file("C:\\a", [0, 1, 5, 6])
    eng = Engine(vol)
    eng.analyze()
    eng.free.release(0, 2)  # o mapa local passa a achar que clusters EM USO estão livres
    rep = Report(mode="defrag")
    eng._defrag_pass(Options(), rep)  # antes: ValueError "intervalo já livre"
    # o destino errado é recusado pelo volume, a falha é registrada e o estado é relido
    assert len(rep.failed) == 1
    assert eng.free.runs() == FreeMap.from_bitmap(vol.read_bitmap(), vol.total_clusters).runs()
    rep2 = Report(mode="defrag")
    eng._defrag_pass(Options(), rep2)  # com o mapa já corrigido, funciona
    assert not rep2.failed and vol.get_file("C:\\a").fragments == 1
    assert eng.free.runs() == FreeMap.from_bitmap(vol.read_bitmap(), vol.total_clusters).runs()
    vol.check_invariants()


# ---- threads de leitura ----------------------------------------------------
def test_resolve_threads_explicit_env_auto_and_clamp(monkeypatch):
    from unfrag.system import MAX_THREADS, auto_threads, resolve_threads
    monkeypatch.delenv("UNFRAG_THREADS", raising=False)
    assert resolve_threads(0) == auto_threads()
    assert resolve_threads(3) == 3
    assert resolve_threads(10_000) == MAX_THREADS
    assert resolve_threads(-5) == auto_threads()
    monkeypatch.setenv("UNFRAG_THREADS", "5")
    assert resolve_threads(0) == 5
    assert resolve_threads(2) == 2  # o valor explícito vence a variável de ambiente
    monkeypatch.setenv("UNFRAG_THREADS", "abc")
    assert resolve_threads(0) == auto_threads()


def test_cli_threads_option(capsys):
    assert main(["--demo", "analyze", "C:", "--threads", "4"]) == 0
    assert "Fragmentação" in capsys.readouterr().out
    assert main(["schedule", "create", "C:", "--threads", "4", "--dry-run"]) == 0
    assert "--threads 4" in capsys.readouterr().out
