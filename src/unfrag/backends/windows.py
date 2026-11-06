"""Backend Windows/NTFS usando as IOCTLs documentadas da API de desfragmentação:

* FSCTL_GET_VOLUME_BITMAP       - clusters livres/ocupados
* FSCTL_GET_RETRIEVAL_POINTERS  - extents de um arquivo
* FSCTL_MOVE_FILE               - mover clusters (o NTFS garante a integridade)
* IOCTL_STORAGE_QUERY_PROPERTY  - detectar SSD (seek penalty)

Requer privilégios de administrador. Só importável no Windows.
"""
from __future__ import annotations

import ctypes
import os
import struct
import sys
import threading
from ctypes import wintypes as wt
from typing import Iterator, List, Optional

if sys.platform != "win32":  # pragma: no cover
    raise ImportError("backend Windows só funciona no Windows")

from ..model import DriveInfo, Extent, FileInfo
from .. import mft
from ..ntfs import parse_retrieval
from ..scan import parallel_scan
from ..system import resolve_threads
from .base import MoveError, VolumeBackend

k32 = ctypes.WinDLL("kernel32", use_last_error=True)

GENERIC_READ = 0x80000000
FILE_READ_ATTRIBUTES = 0x0080
FILE_SHARE_ALL = 0x7
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
INVALID_HANDLE = ctypes.c_void_p(-1).value

FSCTL_GET_VOLUME_BITMAP = 0x0009006F
FSCTL_GET_RETRIEVAL_POINTERS = 0x00090073
FSCTL_GET_NTFS_VOLUME_DATA = 0x00090064
FSCTL_MOVE_FILE = 0x00090074
IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400

ERROR_HANDLE_EOF = 38
ERROR_MORE_DATA = 234

ATTR_SPARSE = 0x200
ATTR_REPARSE = 0x400
ATTR_COMPRESSED = 0x800
ATTR_OFFLINE = 0x1000
ATTR_ENCRYPTED = 0x4000

SYSTEM_NAMES = {"pagefile.sys", "hiberfil.sys", "swapfile.sys"}

k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID, wt.DWORD, wt.DWORD, wt.HANDLE]
k32.CreateFileW.restype = wt.HANDLE
k32.DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, wt.LPVOID, wt.DWORD, wt.LPVOID, wt.DWORD,
                                ctypes.POINTER(wt.DWORD), wt.LPVOID]
k32.DeviceIoControl.restype = wt.BOOL
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.SetFilePointerEx.argtypes = [wt.HANDLE, ctypes.c_int64, ctypes.c_void_p, wt.DWORD]
k32.SetFilePointerEx.restype = wt.BOOL
k32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, ctypes.c_void_p]
k32.ReadFile.restype = wt.BOOL
k32.GetDiskFreeSpaceW.argtypes = [wt.LPCWSTR] + [ctypes.POINTER(wt.DWORD)] * 4
k32.GetDiskFreeSpaceExW.argtypes = [wt.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64),
                                    ctypes.POINTER(ctypes.c_uint64)]
k32.GetVolumeInformationW.argtypes = [wt.LPCWSTR, wt.LPWSTR, wt.DWORD, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_void_p, wt.LPWSTR, wt.DWORD]
k32.GetDriveTypeW.argtypes = [wt.LPCWSTR]


class MOVE_FILE_DATA(ctypes.Structure):
    _fields_ = [("FileHandle", wt.HANDLE),
                ("StartingVcn", ctypes.c_int64),
                ("StartingLcn", ctypes.c_int64),
                ("ClusterCount", wt.DWORD)]


class STORAGE_PROPERTY_QUERY(ctypes.Structure):
    _fields_ = [("PropertyId", wt.DWORD), ("QueryType", wt.DWORD), ("Extra", ctypes.c_ubyte * 1)]


def _winerr(prefix: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(code, "%s (erro Win32 %d)" % (prefix, code))


def _open(path: str, access: int, flags: int = FILE_FLAG_BACKUP_SEMANTICS) -> int:
    h = k32.CreateFileW(path, access, FILE_SHARE_ALL, None, OPEN_EXISTING, flags, None)
    if h is None or h == INVALID_HANDLE:
        raise _winerr("não foi possível abrir %s" % path)
    return h


def _long(path: str) -> str:
    if path.startswith("\\\\?\\"):
        return path
    if len(path) > 2 and path[1] == ":":  # caminho absoluto vindo do scandir: sem abspath()
        return "\\\\?\\" + path
    return "\\\\?\\" + os.path.abspath(path)


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def on_battery() -> bool:
    class PS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", wt.DWORD), ("BatteryFullLifeTime", wt.DWORD)]
    st = PS()
    if not k32.GetSystemPowerStatus(ctypes.byref(st)):
        return False
    return st.ACLineStatus == 0


def _query_ssd(letter: str) -> Optional[bool]:
    try:
        h = _open("\\\\.\\%s:" % letter, 0, 0)
    except OSError:
        return None
    try:
        q = STORAGE_PROPERTY_QUERY(7, 0)  # StorageDeviceSeekPenaltyProperty, PropertyStandardQuery
        out = ctypes.create_string_buffer(16)
        ret = wt.DWORD()
        if not k32.DeviceIoControl(h, IOCTL_STORAGE_QUERY_PROPERTY, ctypes.byref(q), ctypes.sizeof(q),
                                   out, 16, ctypes.byref(ret), None):
            return None
        incurs = out.raw[8]
        return not bool(incurs)
    finally:
        k32.CloseHandle(h)


def list_drives() -> List[DriveInfo]:
    drives: List[DriveInfo] = []
    mask = k32.GetLogicalDrives()
    for i in range(26):
        if not mask & (1 << i):
            continue
        letter = chr(65 + i)
        root = "%s:\\" % letter
        dtype = k32.GetDriveTypeW(root)
        if dtype not in (2, 3):  # removível, fixo
            continue
        label = ctypes.create_unicode_buffer(261)
        fs = ctypes.create_unicode_buffer(261)
        if not k32.GetVolumeInformationW(root, label, 261, None, None, None, fs, 261):
            continue
        total, free = ctypes.c_uint64(), ctypes.c_uint64()
        k32.GetDiskFreeSpaceExW(root, None, ctypes.byref(total), ctypes.byref(free))
        ssd = _query_ssd(letter) if dtype == 3 else None
        kind = "Removível" if dtype == 2 else ("SSD" if ssd else ("HDD" if ssd is False else "Desconhecido"))
        ok = fs.value.upper() == "NTFS" and dtype == 3
        note = "" if ok else "somente NTFS em discos fixos é suportado"
        drives.append(DriveInfo(letter, label.value, fs.value, kind, total.value, free.value, ok, note))
    return drives


class _ThreadCtx:
    """Objetos ctypes reaproveitados por thread: a análise faz uma DeviceIoControl por arquivo,
    e criar buffer/c_int64/DWORD/byref a cada chamada custa mais que a própria chamada."""

    __slots__ = ("size", "buf", "start", "ret", "start_ref", "ret_ref")

    def __init__(self, size: int):
        self.size = size
        self.buf = ctypes.create_string_buffer(size)
        self.start = ctypes.c_int64(0)
        self.ret = wt.DWORD(0)
        self.start_ref = ctypes.byref(self.start)
        self.ret_ref = ctypes.byref(self.ret)


def skip_reason(name_lower: str, attrs: int) -> Optional[str]:
    if name_lower in SYSTEM_NAMES:
        return "arquivo de sistema"
    if attrs & ATTR_REPARSE:
        return "ponto de reanálise"
    if attrs & ATTR_ENCRYPTED:
        return "criptografado (EFS)"
    if attrs & ATTR_SPARSE:
        return "arquivo esparso"
    if attrs & ATTR_COMPRESSED:
        return "compactado pelo NTFS"
    if attrs & ATTR_OFFLINE:
        return "offline"
    return None


class WindowsVolume(VolumeBackend):
    use_mft = False          # lê a MFT em vez de abrir arquivo por arquivo (só para o volume inteiro)
    progress = None          # callable(feitos, total) usado durante a leitura da MFT
    mft_used = False         # a última análise usou a MFT?
    mft_error: Optional[str] = None  # por que a MFT não foi usada (e o método clássico entrou)

    def __init__(self, letter: str):
        self.letter = letter.rstrip(":\\/").upper()
        root = "%s:\\" % self.letter
        spc, bps, free, total = wt.DWORD(), wt.DWORD(), wt.DWORD(), wt.DWORD()
        if not k32.GetDiskFreeSpaceW(root, ctypes.byref(spc), ctypes.byref(bps),
                                     ctypes.byref(free), ctypes.byref(total)):
            raise _winerr("GetDiskFreeSpace falhou")
        self.cluster_size = spc.value * bps.value
        self.total_clusters = total.value
        self._ssd = _query_ssd(self.letter)
        self._tls = threading.local()
        self._vol = _open("\\\\.\\%s:" % self.letter, GENERIC_READ, 0)
        # GetDiskFreeSpaceW satura em 32 bits; o bitmap dá o número real de clusters
        self._fix_total_clusters()

    def _fix_total_clusters(self) -> None:
        buf = ctypes.create_string_buffer(32)
        start = ctypes.c_int64(0)
        ret = wt.DWORD()
        k32.DeviceIoControl(self._vol, FSCTL_GET_VOLUME_BITMAP, ctypes.byref(start), 8, buf, 32,
                            ctypes.byref(ret), None)
        if ret.value >= 16:
            start_lcn, bitmap_size = struct.unpack_from("<qq", buf.raw, 0)
            self.total_clusters = max(self.total_clusters, start_lcn + bitmap_size)

    @property
    def is_ssd(self) -> Optional[bool]:
        return self._ssd

    def close(self) -> None:
        if getattr(self, "_vol", None):
            k32.CloseHandle(self._vol)
            self._vol = None

    # --- bitmap ------------------------------------------------------------
    def read_bitmap(self) -> bytes:
        out = bytearray()
        start = 0
        size = (4 << 20) + 16
        buf = ctypes.create_string_buffer(size)
        while start < self.total_clusters:
            inbuf = ctypes.c_int64(start)
            ret = wt.DWORD()
            ok = k32.DeviceIoControl(self._vol, FSCTL_GET_VOLUME_BITMAP, ctypes.byref(inbuf), 8,
                                     buf, size, ctypes.byref(ret), None)
            err = ctypes.get_last_error()
            if not ok and err != ERROR_MORE_DATA:
                raise OSError(err, "FSCTL_GET_VOLUME_BITMAP falhou (erro %d)" % err)
            # lê direto do buffer: `buf.raw` copiaria os 4 MiB inteiros a cada acesso
            starting, bitmap_size = struct.unpack_from("<qq", buf, 0)
            nbytes = min(ret.value - 16, (bitmap_size + 7) // 8)
            out += ctypes.string_at(ctypes.addressof(buf) + 16, max(nbytes, 0))
            start = starting + nbytes * 8
            if ok or nbytes <= 0:
                break
        return bytes(out)

    # --- arquivos ----------------------------------------------------------
    def _ctx(self) -> _ThreadCtx:
        ctx = getattr(self._tls, "ctx", None)
        if ctx is None:
            ctx = self._tls.ctx = _ThreadCtx(8 * 1024)  # cabe ~500 extents; se faltar, ERROR_MORE_DATA continua o laço
        return ctx

    def _extents(self, h: int) -> List[Extent]:
        ctx = self._ctx()
        buf, size, start, ret = ctx.buf, ctx.size, ctx.start, ctx.ret
        start_ref, ret_ref = ctx.start_ref, ctx.ret_ref
        ioctl = k32.DeviceIoControl
        start.value = 0
        extents: List[Extent] = []
        while True:
            ok = ioctl(h, FSCTL_GET_RETRIEVAL_POINTERS, start_ref, 8, buf, size, ret_ref, None)
            if ok:
                err = 0
            else:
                err = ctypes.get_last_error()
                if err == ERROR_HANDLE_EOF:
                    return extents
                if err != ERROR_MORE_DATA:
                    raise OSError(err, "FSCTL_GET_RETRIEVAL_POINTERS falhou (erro %d)" % err)
            try:
                part, next_vcn = parse_retrieval(buf, ret.value)  # lê direto do buffer, sem cópia
            except ValueError:
                raise OSError(err, "FSCTL_GET_RETRIEVAL_POINTERS devolveu dados incompletos")
            if extents:
                extents.extend(part)
            else:
                extents = part
            if ok:
                return extents
            start.value = next_vcn

    def get_file(self, path: str) -> Optional[FileInfo]:
        try:
            size = os.stat(path).st_size
            h = _open(_long(path), FILE_READ_ATTRIBUTES)
        except OSError:
            return None
        try:
            return FileInfo(path, size, self._extents(h))
        except OSError:
            return None
        finally:
            k32.CloseHandle(h)

    def _scan(self, path: str, size: int, attrs: int) -> Optional[FileInfo]:
        skip = skip_reason(os.path.basename(path).lower(), attrs)
        try:
            h = _open(_long(path), FILE_READ_ATTRIBUTES)
        except OSError as exc:
            return FileInfo(path, size, [], "inacessível: %s" % exc.strerror) if size else None
        try:
            return FileInfo(path, size, self._extents(h), skip)
        except OSError as exc:
            return FileInfo(path, size, [], "erro: %s" % exc.strerror)
        finally:
            k32.CloseHandle(h)

    def _walk(self, root: str, recurse: bool):
        """Percorre as pastas (rápido: scandir já traz tamanho e atributos).
        Arquivos que cabem em 1 cluster não podem fragmentar e só são contados."""
        cs = self.cluster_size
        skip_attrs = ATTR_REPARSE | ATTR_SPARSE | ATTR_COMPRESSED | ATTR_ENCRYPTED | ATTR_OFFLINE
        stack = [root]
        while stack:
            d = stack.pop()
            try:
                it = os.scandir(d)
            except OSError:
                continue
            with it:
                for e in it:
                    try:
                        st = e.stat(follow_symlinks=False)
                        attrs = getattr(st, "st_file_attributes", 0)
                        if e.is_dir(follow_symlinks=False):
                            if recurse and not attrs & ATTR_REPARSE:
                                stack.append(e.path)
                            continue
                        size = st.st_size
                    except OSError:
                        continue
                    if size == 0:
                        continue
                    if size <= cs and not attrs & skip_attrs and e.name.lower() not in SYSTEM_NAMES:
                        self.trivial_files += 1
                        self.trivial_bytes += size
                        continue
                    yield (e.path, size, attrs)

    def iter_files(self, root: Optional[str], recurse: bool) -> Iterator[FileInfo]:
        self.trivial_files = 0
        self.trivial_bytes = 0
        self.mft_used = False
        self.mft_error = None
        root = root or ("%s:\\" % self.letter)
        if os.path.isfile(root):
            st = os.stat(root)
            info = self._scan(root, st.st_size, getattr(st, "st_file_attributes", 0))
            if info:
                yield info
            return
        whole_volume = recurse and os.path.normpath(root).rstrip("\\").upper() == "%s:" % self.letter
        if self.use_mft and whole_volume:
            try:
                entries = self._mft_entries()
            except (mft.Unsupported, OSError, ValueError, struct.error, MemoryError) as exc:
                self.mft_error = "%s: %s" % (type(exc).__name__, exc)
                entries = None
            if entries is not None:
                self.mft_used = True
                yield from self._from_mft(entries)
                return
        workers = resolve_threads(self.threads)
        yield from parallel_scan(self._walk(root, recurse), lambda t: self._scan(*t), workers=workers)

    # --- MFT -----------------------------------------------------------------
    def _read_volume(self, offset: int, n: int) -> bytes:
        """Lê `n` bytes do volume bruto (deslocamento e tamanho múltiplos do setor)."""
        if not k32.SetFilePointerEx(self._vol, offset, None, 0):
            raise _winerr("SetFilePointerEx falhou")
        buf = getattr(self._tls, "rawbuf", None)
        if buf is None or len(buf) < n:
            buf = self._tls.rawbuf = ctypes.create_string_buffer(max(n, 4 << 20))
        got = wt.DWORD(0)
        if not k32.ReadFile(self._vol, buf, n, ctypes.byref(got), None):
            raise _winerr("ReadFile no volume falhou (deslocamento %d)" % offset)
        return ctypes.string_at(ctypes.addressof(buf), got.value)

    def _ntfs_volume_data(self) -> dict:
        buf = ctypes.create_string_buffer(128)
        ret = wt.DWORD(0)
        if not k32.DeviceIoControl(self._vol, FSCTL_GET_NTFS_VOLUME_DATA, None, 0, buf, 128,
                                   ctypes.byref(ret), None):
            raise _winerr("FSCTL_GET_NTFS_VOLUME_DATA falhou (o volume não é NTFS?)")
        bps, bpc, bpr, _cpr = struct.unpack_from("<IIII", buf, 40)
        valid, start_lcn = struct.unpack_from("<qq", buf, 56)
        return dict(bytes_per_sector=bps, bytes_per_cluster=bpc, bytes_per_record=bpr,
                    mft_valid_length=valid, mft_start_lcn=start_lcn)

    def _mft_entries(self) -> list:
        """Lê a MFT inteira do volume e devolve [(caminho, tamanho, atributos, extents|None)]."""
        vd = self._ntfs_volume_data()
        cs, rec_size = vd["bytes_per_cluster"], vd["bytes_per_record"]
        if cs <= 0 or rec_size < 512 or rec_size % 512 or vd["mft_start_lcn"] <= 0:
            raise mft.Unsupported("dados do volume NTFS inesperados")
        rec0 = bytearray(self._read_volume(vd["mft_start_lcn"] * cs, rec_size))
        runs, size = mft.mft_runs_from_record0(rec0)
        total = min(size, vd["mft_valid_length"] or size) // rec_size
        scanner = mft.MftScanner("%s:\\" % self.letter, cs)
        report = self.progress
        for idx, rec in mft.iter_mft_records(self._read_volume, runs, cs, rec_size, total,
                                             progress=(lambda d, t: report(d, t)) if report else None):
            scanner.feed(idx, rec)
        return list(scanner.results())

    def _from_mft(self, entries: list) -> Iterator[FileInfo]:
        cs = self.cluster_size
        skip_attrs = ATTR_REPARSE | ATTR_SPARSE | ATTR_COMPRESSED | ATTR_ENCRYPTED | ATTR_OFFLINE
        fallback = []
        for path, size, attrs, extents in entries:
            if size == 0:
                continue
            name = path.rsplit("\\", 1)[-1].lower()
            if size <= cs and not attrs & skip_attrs and name not in SYSTEM_NAMES:
                self.trivial_files += 1
                self.trivial_bytes += size
                continue
            if extents is None:  # a MFT não deu certeza: pergunta ao Windows, como no método clássico
                fallback.append((path, size, attrs))
                continue
            yield FileInfo(path, size, extents, skip_reason(name, attrs))
        if fallback:
            yield from parallel_scan(iter(fallback), lambda t: self._scan(*t), workers=resolve_threads(self.threads))

    # --- mover -------------------------------------------------------------
    def move_clusters(self, path: str, vcn: int, lcn: int, count: int) -> None:
        try:
            h = _open(_long(path), FILE_READ_ATTRIBUTES)
        except OSError as exc:
            raise MoveError(str(exc))
        try:
            data = MOVE_FILE_DATA(h, vcn, lcn, count)
            ret = wt.DWORD()
            if not k32.DeviceIoControl(self._vol, FSCTL_MOVE_FILE, ctypes.byref(data),
                                       ctypes.sizeof(data), None, 0, ctypes.byref(ret), None):
                err = ctypes.get_last_error()
                raise MoveError("FSCTL_MOVE_FILE falhou (erro Win32 %d)" % err)
        finally:
            k32.CloseHandle(h)
