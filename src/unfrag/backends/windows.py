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
from ctypes import wintypes as wt
from typing import Iterator, List, Optional

if sys.platform != "win32":  # pragma: no cover
    raise ImportError("backend Windows só funciona no Windows")

from ..model import DriveInfo, Extent, FileInfo
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
    path = os.path.abspath(path)
    return path if path.startswith("\\\\?\\") else "\\\\?\\" + path


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


class WindowsVolume(VolumeBackend):
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
            starting, bitmap_size = struct.unpack_from("<qq", buf.raw, 0)
            nbytes = min(ret.value - 16, (bitmap_size + 7) // 8)
            out += buf.raw[16:16 + nbytes]
            start = starting + nbytes * 8
            if ok or nbytes <= 0:
                break
        return bytes(out)

    # --- arquivos ----------------------------------------------------------
    def _extents(self, h: int) -> List[Extent]:
        extents: List[Extent] = []
        size = 64 * 1024
        buf = ctypes.create_string_buffer(size)
        start_vcn = 0
        while True:
            inbuf = ctypes.c_int64(start_vcn)
            ret = wt.DWORD()
            ok = k32.DeviceIoControl(h, FSCTL_GET_RETRIEVAL_POINTERS, ctypes.byref(inbuf), 8,
                                     buf, size, ctypes.byref(ret), None)
            err = ctypes.get_last_error()
            if not ok and err not in (ERROR_MORE_DATA,):
                if err == ERROR_HANDLE_EOF:
                    return extents
                raise OSError(err, "FSCTL_GET_RETRIEVAL_POINTERS falhou (erro %d)" % err)
            count = struct.unpack_from("<I", buf.raw, 0)[0]
            vcn = struct.unpack_from("<q", buf.raw, 8)[0]
            off = 16
            for _ in range(count):
                next_vcn, lcn = struct.unpack_from("<qq", buf.raw, off)
                extents.append(Extent(vcn, lcn, next_vcn - vcn))
                vcn = next_vcn
                off += 16
            if ok:
                return extents
            start_vcn = vcn

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
        name = os.path.basename(path).lower()
        skip = None
        if name in SYSTEM_NAMES:
            skip = "arquivo de sistema"
        elif attrs & ATTR_REPARSE:
            skip = "ponto de reanálise"
        elif attrs & ATTR_ENCRYPTED:
            skip = "criptografado (EFS)"
        elif attrs & ATTR_SPARSE:
            skip = "arquivo esparso"
        elif attrs & ATTR_COMPRESSED:
            skip = "compactado pelo NTFS"
        elif attrs & ATTR_OFFLINE:
            skip = "offline"
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

    def iter_files(self, root: Optional[str], recurse: bool) -> Iterator[FileInfo]:
        root = root or ("%s:\\" % self.letter)
        if os.path.isfile(root):
            st = os.stat(root)
            info = self._scan(root, st.st_size, getattr(st, "st_file_attributes", 0))
            if info:
                yield info
            return
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
                        if st.st_size == 0:
                            continue
                        info = self._scan(e.path, st.st_size, attrs)
                    except OSError:
                        continue
                    if info:
                        yield info

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
