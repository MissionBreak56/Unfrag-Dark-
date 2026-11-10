"""Backend Windows lendo a MFT, com kernel32 simulado (roda em qualquer sistema)."""
import ctypes
import importlib
import struct
import sys
import threading
import types

import pytest

from test_mft import CS, REC, build_fake_volume, record, std_info, file_name, data_nonres  # noqa: E402


class FakeK32:
    def __init__(self, vol, start_lcn=3, valid=32 * REC):
        self.vol, self.pos, self.start_lcn, self.valid = vol, 0, start_lcn, valid
        self.reads = 0
        outer = self

        class _Fn:
            def __init__(self, impl):
                self.impl = impl

            def __call__(self, *a):
                return self.impl(*a)

        self.SetFilePointerEx = _Fn(self._seek)
        self.ReadFile = _Fn(self._read)
        self.DeviceIoControl = _Fn(self._ioctl)
        self.last_error = 0

    def __getattr__(self, name):
        o = types.SimpleNamespace()
        setattr(self, name, o)
        return o

    def _seek(self, h, off, ptr, method):
        self.pos = off
        return 1

    def _read(self, h, buf, n, retref, ov):
        self.reads += 1
        data = bytes(self.vol[self.pos:self.pos + n])
        ctypes.memmove(buf, data, len(data))
        retref._obj.value = len(data)
        return 1

    def _ioctl(self, h, code, inref, insize, buf, bufsize, retref, ov):
        if code == 0x00090064:
            raw = bytearray(128)
            struct.pack_into("<IIII", raw, 40, 512, CS, REC, REC // CS)
            struct.pack_into("<qq", raw, 56, self.valid, self.start_lcn)
            ctypes.memmove(buf, bytes(raw), 128)
            retref._obj.value = 96
            return 1
        self.last_error = 1
        return 0


@pytest.fixture
def win(monkeypatch):
    vol, runs, total = build_fake_volume()
    fake = FakeK32(vol)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: fake, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: fake.last_error, raising=False)
    sys.modules.pop("unfrag.backends.windows", None)
    mod = importlib.import_module("unfrag.backends.windows")

    def make():
        v = object.__new__(mod.WindowsVolume)
        v.letter, v.cluster_size, v.total_clusters, v.threads = "C", CS, 40, 1
        v._vol, v._tls = 1234, threading.local()
        v.trivial_files = v.trivial_bytes = 0
        return v

    monkeypatch.setattr(mod, "_open", lambda *a, **k: (_ for _ in ()).throw(OSError(5, "negado")))
    yield mod, fake, make
    sys.modules.pop("unfrag.backends.windows", None)


def test_iter_files_uses_mft(win):
    mod, fake, make = win
    v = make()
    v.use_mft = True
    got = {f.path: f for f in v.iter_files(None, True)}
    assert v.mft_used and v.mft_error is None
    video = got["C:\\Users\\Ana\\video.mp4"]
    assert [tuple(e) for e in video.extents] == [(0, 1000, 50), (50, 3000, 70), (120, 3070, 5)]
    assert video.size == 125 * CS and video.skip is None
    assert got["C:\\pagefile.sys"].skip == "arquivo de sistema"
    assert got["C:\\Users\\Ana\\compactado.dat"].skip == "compactado pelo NTFS"
    assert "C:\\Users\\pequeno.txt" not in got and v.trivial_files >= 1  # cabe em 1 cluster: só conta
    # a MFT não teve certeza (cobre 2 de 99 clusters): consultou o arquivo da forma clássica (aqui, inacessível)
    assert got["C:\\Users\\Ana\\buraco.bin"].extents == [] and "inacessível" in got["C:\\Users\\Ana\\buraco.bin"].skip
    assert fake.reads >= 2


def test_progress_callback_and_cancel(win):
    mod, fake, make = win
    v = make()
    v.use_mft = True
    calls = []
    v.progress = lambda d, t: calls.append((d, t))
    list(v.iter_files(None, True))
    assert calls and calls[-1][0] == calls[-1][1] == 32

    class Stop(Exception):
        pass

    def boom(d, t):
        raise Stop()
    v2 = make()
    v2.use_mft = True
    v2.progress = boom
    with pytest.raises(Stop):  # cancelar durante a leitura da MFT interrompe a análise
        list(v2.iter_files(None, True))


def test_falls_back_to_classic_when_mft_unusable(win, monkeypatch):
    mod, fake, make = win
    fake.vol = bytearray(fake.vol)
    fake.vol[3 * CS:3 * CS + 4] = b"XXXX"  # registro 0 ilegível
    v = make()
    v.use_mft = True
    monkeypatch.setattr(v, "_walk", lambda root, recurse: iter(()), raising=False)
    assert list(v.iter_files(None, True)) == []
    assert not v.mft_used and "Unsupported" in v.mft_error


def test_mft_only_for_whole_volume(win, monkeypatch):
    mod, fake, make = win
    v = make()
    v.use_mft = True
    monkeypatch.setattr(v, "_walk", lambda root, recurse: iter(()), raising=False)
    monkeypatch.setattr(v, "_check_root", lambda r: r, raising=False)  # caminhos de mentira (teste fora do Windows)
    list(v.iter_files("C:\\Users", True))   # pasta: método clássico
    assert not v.mft_used and fake.reads == 0
    list(v.iter_files("C:\\", False))       # sem recursão: método clássico
    assert not v.mft_used and fake.reads == 0
    v.use_mft = False
    list(v.iter_files(None, True))
    assert not v.mft_used and fake.reads == 0  # desligado: nunca lê a MFT
