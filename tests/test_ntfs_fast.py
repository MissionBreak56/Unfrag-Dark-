"""Testes do caminho rápido da análise: parser de retrieval pointers, atalhos de um extent,
pausa do GC e o laço de E/S do backend Windows (com kernel32 simulado, rodando em qualquer SO)."""
import ctypes
import gc
import importlib
import random
import struct
import sys
import threading
import types
from ctypes import wintypes as wt

import pytest

from unfrag.engine import _signature
from unfrag.model import Extent, FileInfo
from unfrag.ntfs import parse_retrieval
from unfrag.scan import gc_paused


def make_raw(extents, start_vcn=None):
    """Monta um RETRIEVAL_POINTERS_BUFFER a partir de [(vcn, lcn, length)]."""
    first = extents[0][0] if start_vcn is None else start_vcn
    raw = struct.pack("<I4xq", len(extents), first)
    for vcn, lcn, ln in extents:
        raw += struct.pack("<qq", vcn + ln, lcn)
    return raw


def reference_parse(raw):
    """Implementação antiga (laço struct a struct), usada como referência."""
    out = []
    count = struct.unpack_from("<I", raw, 0)[0]
    vcn = struct.unpack_from("<q", raw, 8)[0]
    off = 16
    for _ in range(count):
        next_vcn, lcn = struct.unpack_from("<qq", raw, off)
        out.append((vcn, lcn, next_vcn - vcn))
        vcn = next_vcn
        off += 16
    return out, vcn


def random_extents(rnd, k, hole_chance=0.1):
    out, vcn, lcn = [], rnd.randint(0, 3), rnd.randint(0, 10**9)
    for _ in range(k):
        ln = rnd.randint(1, 10**6)
        out.append((vcn, -1 if rnd.random() < hole_chance else lcn, ln))
        vcn += ln
        lcn += ln + rnd.randint(0, 10**4)
    return out


def test_parse_matches_reference_on_random_buffers():
    rnd = random.Random(7)
    for _ in range(500):
        ex = random_extents(rnd, rnd.choice([1, 1, 1, 2, 3, 17, 300]))
        raw = make_raw(ex)
        got, nxt = parse_retrieval(raw)
        want, want_nxt = reference_parse(raw)
        assert [tuple(e) for e in got] == want == ex
        assert nxt == want_nxt
        assert all(isinstance(e, Extent) for e in got)


def test_parse_reads_ctypes_buffer_without_copy_and_honors_length():
    raw = make_raw([(0, 100, 5), (5, 300, 7)])
    buf = ctypes.create_string_buffer(8192)
    ctypes.memmove(buf, raw, len(raw))
    got, nxt = parse_retrieval(buf, len(raw))
    assert got == [Extent(0, 100, 5), Extent(5, 300, 7)] and nxt == 12
    with pytest.raises(ValueError):  # só 1 extent cabe nos bytes válidos
        parse_retrieval(buf, 16 + 16)


def test_parse_empty_and_truncated():
    assert parse_retrieval(struct.pack("<I4xq", 0, 9)) == ([], 9)
    with pytest.raises(ValueError):
        parse_retrieval(b"\x00" * 8)
    with pytest.raises(ValueError):
        parse_retrieval(make_raw([(0, 1, 1), (1, 5, 1)])[:-1])


def test_parse_holes_keep_negative_lcn():
    got, _ = parse_retrieval(make_raw([(0, -1, 4), (4, 50, 2)]))
    assert got[0].is_hole and not got[1].is_hole


def _old_fragments(f):
    count, prev_end = 0, None
    for e in sorted(f.extents, key=lambda e: e.vcn):
        if e.lcn < 0:
            prev_end = None
            continue
        if prev_end is None or e.lcn != prev_end:
            count += 1
        prev_end = e.lcn + e.length
    return count


def _old_signature(f):
    return tuple((e.lcn, e.length) for e in sorted(f.extents, key=lambda e: e.vcn) if e.lcn >= 0)


def test_fragments_and_signature_match_old_logic():
    rnd = random.Random(3)
    for _ in range(2000):
        ex = [Extent(*t) for t in random_extents(rnd, rnd.choice([1, 1, 2, 3, 6]), 0.2)]
        rnd.shuffle(ex)  # a ordem de chegada não pode importar
        f = FileInfo("x", 1, ex)
        assert f.fragments == _old_fragments(f)
        assert _signature(f) == _old_signature(f)
        assert f.has_holes == any(e.lcn < 0 for e in ex)


def test_gc_paused_restores_previous_state():
    assert gc.isenabled()
    with gc_paused():
        assert not gc.isenabled()
        with gc_paused():  # aninhado
            assert not gc.isenabled()
        assert not gc.isenabled()
    assert gc.isenabled()
    gc.disable()
    try:
        with gc_paused():
            pass
        assert not gc.isenabled()  # quem já tinha desligado continua desligado
    finally:
        gc.enable()


def test_gc_paused_restores_on_exception():
    with pytest.raises(RuntimeError):
        with gc_paused():
            raise RuntimeError("x")
    assert gc.isenabled()


# --- laço de E/S do backend Windows, com kernel32 simulado ---------------------------------

ERROR_HANDLE_EOF, ERROR_MORE_DATA = 38, 234


class FakeK32:
    """Simula FSCTL_GET_RETRIEVAL_POINTERS paginando em `page` extents (ERROR_MORE_DATA)."""

    def __init__(self, extents, page=2):
        self.extents, self.page = extents, page
        self.last_error, self.calls = 0, 0
        self.impl = self._ioctl

        class _Fn:  # função com atributos (argtypes/restype), como a do ctypes
            def __call__(_, *a):
                return self.impl(*a)
        self.DeviceIoControl = _Fn()

    def __getattr__(self, name):  # CreateFileW.argtypes = ... etc.
        o = types.SimpleNamespace()
        setattr(self, name, o)
        return o

    def _ioctl(self, h, code, inref, insize, buf, bufsize, retref, overlapped):
        self.calls += 1
        start_vcn = inref._obj.value
        todo = [e for e in self.extents if e[0] + e[2] > start_vcn]
        if not todo:
            self.last_error = ERROR_HANDLE_EOF
            return 0
        part, more = todo[:self.page], len(todo) > self.page
        raw = make_raw(part, start_vcn=part[0][0] if part[0][0] >= start_vcn else start_vcn)
        ctypes.memmove(buf, raw, len(raw))
        retref._obj.value = len(raw)
        self.last_error = 0 if not more else ERROR_MORE_DATA
        return 0 if more else 1


@pytest.fixture
def win_module(monkeypatch):
    fake = FakeK32([])
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: fake, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: fake.last_error, raising=False)
    sys.modules.pop("unfrag.backends.windows", None)
    mod = importlib.import_module("unfrag.backends.windows")
    yield mod, fake
    sys.modules.pop("unfrag.backends.windows", None)


def _vol(mod):
    v = object.__new__(mod.WindowsVolume)
    v._tls = threading.local()
    return v


@pytest.mark.parametrize("page", [1, 2, 5, 100])
def test_windows_extents_pagination(win_module, page):
    mod, fake = win_module
    rnd = random.Random(page)
    ex = random_extents(rnd, 11, hole_chance=0)
    fake.extents, fake.page, fake.calls = ex, page, 0
    got = _vol(mod)._extents(1)
    assert [tuple(e) for e in got] == ex
    assert fake.calls == -(-11 // page)


def test_windows_extents_eof_and_error(win_module):
    mod, fake = win_module
    fake.extents = []
    assert _vol(mod)._extents(1) == []  # arquivo sem extents (ERROR_HANDLE_EOF)

    def boom(*a):
        fake.last_error = 5
        return 0
    fake.impl = boom
    with pytest.raises(OSError):
        _vol(mod)._extents(1)


def test_windows_extents_reuses_thread_context(win_module):
    mod, fake = win_module
    fake.extents = [(0, 10, 3)]
    v = _vol(mod)
    v._extents(1)
    ctx = v._tls.ctx
    v._extents(1)
    assert v._tls.ctx is ctx
