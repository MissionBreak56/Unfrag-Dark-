"""Detalhe do bloco do mapa, instância única e clique no mapa."""
import os
import random
import sys
import time
import types

import pytest

os.environ.setdefault("UNFRAG_TRAY", "0")

from unfrag import diskmap, single
from unfrag.model import Extent, FileInfo


# --- mapeamento célula <-> clusters ---------------------------------------------------------

@pytest.mark.parametrize("total,cells", [(1000, 7), (1_000_003, 972), (97, 97), (10, 3), (5_000_000, 1360)])
def test_cell_span_matches_build_map_assignment(total, cells):
    per = total / cells
    prev_hi = 0
    for i in range(cells):
        lo, hi = diskmap.cell_span(total, cells, i)
        assert lo == prev_hi and hi >= lo  # intervalos contíguos, sem buracos
        prev_hi = hi
        for x in {lo, max(lo, hi - 1)} if hi > lo else set():
            assert min(cells - 1, int(x / per)) == i  # mesma regra do build_map
    assert prev_hi == total


def test_fragmented_in_cell_consistent_with_map():
    rnd = random.Random(5)
    total, cells = 20_000, 50
    files = []
    for n in range(300):
        k = rnd.choice([1, 1, 2, 3])
        ex, vcn = [], 0
        for _ in range(k):
            ln = rnd.randint(1, 120)
            ex.append(Extent(vcn, rnd.randint(0, total - ln - 1), ln))
            vcn += ln
        files.append(FileInfo("f%d" % n, vcn * 4096, ex))
    cmap = diskmap.build_map(total, [], files, cells)
    for i, st in enumerate(cmap):
        lo, hi = diskmap.cell_span(total, cells, i)
        hits = diskmap.fragmented_in_cell(files, lo, hi)
        if st == diskmap.FRAGMENTED:
            assert hits, "célula vermelha precisa listar ao menos um arquivo"
        for f, pieces, clusters in hits:
            assert f.fragments > 1 and pieces >= 1 and clusters >= 1


def test_free_and_unmovable_helpers():
    assert diskmap.cell_free_clusters([(0, 10), (50, 10)], 5, 55) == 5 + 5
    f = FileInfo("sys", 4096, [Extent(0, 100, 5)], skip="sistema")
    assert diskmap.unmovable_in_cell([f], 90, 110) == 1
    assert diskmap.unmovable_in_cell([f], 0, 90) == 0


# --- instância única -----------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="usa flock")
def test_second_instance_is_refused_on_file_lock():
    a, b = single.SingleInstance("t-%d" % os.getpid()), single.SingleInstance("t-%d" % os.getpid())
    try:
        assert a.acquire() is True
        assert b.acquire() is False  # já aberto
        a.release()
        assert b.acquire() is True  # liberou: pode abrir de novo
    finally:
        a.release()
        b.release()


class FakeK32:
    """kernel32 simulado: mutex/evento nomeados compartilhados entre 'processos'."""
    names = {}

    def __init__(self):
        self.err = 0
        self.signalled = {}

    def CreateMutexW(self, sec, owner, name):
        self.err = 183 if name in FakeK32.names else 0
        return FakeK32.names.setdefault(name, len(FakeK32.names) + 1)

    def CreateEventW(self, sec, manual, init, name):
        return FakeK32.names.setdefault(name, len(FakeK32.names) + 1)

    def SetEvent(self, h):
        FakeK32.names.setdefault("set", set()).add(h)

    def WaitForSingleObject(self, h, ms):
        s = FakeK32.names.get("set", set())
        if h in s:
            s.discard(h)
            return 0
        time.sleep(ms / 1000 / 8)
        return 258

    def CloseHandle(self, h):
        pass


def test_windows_flow_with_fake_kernel32(monkeypatch):
    import ctypes
    FakeK32.names = {}
    fake = FakeK32()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(single, "_load_k32", lambda: fake)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: fake.err, raising=False)
    first, second = single.SingleInstance("x"), single.SingleInstance("x")
    assert first.acquire() is True
    woke = []
    first.listen(lambda: woke.append(1))
    assert second.acquire() is False
    second.signal_existing()
    end = time.time() + 3
    while not woke and time.time() < end:
        time.sleep(0.02)
    first.release()
    assert woke, "a primeira instância deveria ser acordada pela segunda"


# --- clique no mapa -------------------------------------------------------------------------

tk = pytest.importorskip("tkinter")
from unfrag import gui  # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("sem display")
    a = gui.App(root, demo=True)
    root.update()
    a.start("analyze")
    end = time.time() + 15
    while time.time() < end and not a.v_last.get():
        root.update()
        time.sleep(0.02)
    root.update()
    yield a
    try:
        root.destroy()
    except tk.TclError:
        pass


def _click(app, i):
    cols, step, size = app._map_geom
    ev = types.SimpleNamespace(x=(i % cols) * step + 3, y=(i // cols) * step + 3)
    app._on_map_click(ev)


def test_click_fragmented_cell_lists_its_files(app):
    i = app.cells.index(diskmap.FRAGMENTED)
    _click(app, i)
    app.root.update()
    assert app.sel_cell == i and "Bloco %d de" % (i + 1) in app.v_block.get()
    assert "Fragmentado" in app.v_block.get()
    rows = app.files_tree.get_children()
    assert rows and app.btn_all.winfo_ismapped()
    lo, hi = diskmap.cell_span(app.engine.backend.total_clusters, len(app.cells), i)
    paths = {app.files_tree.item(r, "values")[2] for r in rows}
    want = {f.path for f, _, _ in diskmap.fragmented_in_cell(app.engine.files.values(), lo, hi)}
    assert paths == want
    assert app.nb.current == 0  # abre a aba dos arquivos


def test_click_free_cell_shows_no_files_and_click_again_clears(app):
    i = app.cells.index(diskmap.FREE)
    _click(app, i)
    assert not app.files_tree.get_children()
    assert "nenhum arquivo fragmentado" in app.v_filesinfo.get()
    _click(app, i)  # mesmo quadrado: limpa
    assert app.sel_cell is None
    assert len(app.files_tree.get_children()) == len(app._frag_files) > 0


def test_click_in_gap_or_outside_does_nothing(app):
    cols, step, size = app._map_geom
    app._on_map_click(types.SimpleNamespace(x=size + 1, y=3))  # entre dois quadrados
    assert app.sel_cell is None


def test_selection_survives_redraw_and_resets_on_new_result(app):
    i = app.cells.index(diskmap.FRAGMENTED)
    _click(app, i)
    lo = app.sel_lo
    app._draw_map()
    assert app.sel_cell is not None and app.sel_lo <= lo
    app._show_result(app.engine.analyze(), None)
    assert app.sel_cell is None
