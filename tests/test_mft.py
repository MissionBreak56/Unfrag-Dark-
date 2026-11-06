"""Testes do leitor de MFT com registros NTFS sintéticos (montados byte a byte, com fixups)."""
import random
import struct
import time

import pytest

from unfrag import mft
from unfrag.model import Extent

REC = 1024
CS = 4096


def _align8(n):
    return (n + 7) & ~7


def attr_resident(atype, content, name=b"", attr_id=0):
    off = _align8(24 + len(name))
    body = struct.pack("<IIBBHHH", atype, 0, 0, len(name) // 2, 24 if name else 0, 0, attr_id)
    body += struct.pack("<IHBB", len(content), off, 0, 0)
    body = body.ljust(24, b"\0") + name
    body = body.ljust(off, b"\0") + content
    total = _align8(len(body))
    body = body.ljust(total, b"\0")
    return struct.pack("<II", atype, total) + body[8:]


def attr_nonres(atype, runs, start_vcn, real, name=b"", flags=0, attr_id=0):
    runs_off = _align8(64 + len(name))
    head = struct.pack("<IIBBHHH", atype, 0, 1, len(name) // 2, 64 if name else 0, flags, attr_id)
    last_vcn = start_vcn
    head += struct.pack("<qqHHI", start_vcn, last_vcn, runs_off, 0, 0)
    head += struct.pack("<QQQ", real, real, real)
    body = head.ljust(64, b"\0") + name
    body = body.ljust(runs_off, b"\0") + runs
    total = _align8(len(body))
    body = body.ljust(total, b"\0")
    return struct.pack("<II", atype, total) + body[8:]


def std_info(win_attrs=0x20):
    return attr_resident(0x10, struct.pack("<QQQQI", 0, 0, 0, 0, win_attrs).ljust(72, b"\0"))


def file_name(parent, name, ns=1):
    raw = name.encode("utf-16-le")
    c = struct.pack("<Q", parent | (1 << 48)) + b"\0" * 32 + struct.pack("<QQIIBB", 0, 0, 0, 0, len(name), ns) + raw
    return attr_resident(0x30, c)


def data_resident(content):
    return attr_resident(0x80, content)


def data_nonres(extents, size, start_vcn=0, flags=0):
    return attr_nonres(0x80, mft.encode_runs(extents), start_vcn, size, flags=flags)


def record(attrs, flags=1, base=0, corrupt_fixup=False):
    usa_count = REC // 512 + 1
    hdr_len = 0x30 + 2 * usa_count
    attrs_off = _align8(hdr_len)
    body = b"".join(attrs) + struct.pack("<I", 0xFFFFFFFF)
    used = attrs_off + len(body)
    used = _align8(used)
    rec = bytearray(REC)
    rec[0:4] = b"FILE"
    struct.pack_into("<HHQHHHHIIQH", rec, 4, 0x30, usa_count, 0, 1, 1, attrs_off, flags, used, REC, base, 0)
    rec[attrs_off:attrs_off + len(body)] = body
    usn = b"\x07\x00"
    rec[0x30:0x32] = usn
    for i in range(usa_count - 1):
        end = (i + 1) * 512
        rec[0x32 + 2 * i:0x34 + 2 * i] = rec[end - 2:end]  # guarda o original
        rec[end - 2:end] = usn if not (corrupt_fixup and i == 1) else b"\xff\xff"
    return bytes(rec)


def test_runs_roundtrip_random():
    rnd = random.Random(1)
    for _ in range(300):
        runs, lcn = [], rnd.randint(0, 10**7)
        for _ in range(rnd.randint(1, 40)):
            if rnd.random() < 0.15:
                runs.append((-1, rnd.randint(1, 10**4)))
            else:
                lcn = max(0, lcn + rnd.randint(-10**6, 10**6))
                runs.append((lcn, rnd.randint(1, 10**6)))
        raw = mft.encode_runs(runs)
        got = mft.decode_runs(raw, 0, len(raw))
        vcn = 0
        for (lcn_, ln), e in zip(runs, got):
            assert (e.vcn, e.lcn, e.length) == (vcn, lcn_, ln)
            vcn += ln
        assert len(got) == len(runs)


def test_decode_runs_rejects_garbage():
    with pytest.raises(ValueError):
        mft.decode_runs(b"\x41\x01", 0, 2)  # promete bytes que não existem
    with pytest.raises(ValueError):
        mft.decode_runs(b"\x11\x05\x80\x00", 0, 4)  # lcn negativo


def test_fixups_ok_and_corrupt():
    rec = bytearray(record([std_info(), file_name(5, "a.txt"), data_resident(b"hi")]))
    assert mft.apply_fixups(rec) and rec[510:512] != b"\x07\x00"
    bad = bytearray(record([std_info()], corrupt_fixup=True))
    assert not mft.apply_fixups(bad)
    assert mft.parse_record(bytearray(bad)) is None


def test_parse_record_fields():
    rec = bytearray(record([std_info(0x820), file_name(5, "Foto.JPG"), data_nonres([(100, 3), (500, 2)], 20000)]))
    flags, base, names, std, pieces, has_list = mft.parse_record(rec)
    assert flags == 1 and base == 0 and not has_list
    assert names == [(5, "Foto.JPG", 1)] and std == 0x820
    assert pieces == [(0, True, 20000, [Extent(0, 100, 3), Extent(3, 500, 2)])]


def test_parse_ignores_deleted_and_alternate_streams():
    assert mft.parse_record(bytearray(record([std_info()], flags=0))) is None
    named = attr_nonres(0x80, mft.encode_runs([(9, 1)]), 0, 10, name="x".encode("utf-16-le"))
    _f, _b, _n, _s, pieces, _l = mft.parse_record(bytearray(record([std_info(), file_name(5, "f"), named])))
    assert pieces == []  # fluxo alternativo não conta


def build_fake_volume():
    """Monta um volume com MFT em 2 trechos e vários casos. Devolve (bytes do volume, runs da MFT, n registros)."""
    recs = {}
    recs[0] = record([std_info(), file_name(5, "$MFT"), data_nonres([(3, 2), (9, 2), (15, 2), (22, 2)], 32 * REC)])
    for i in range(1, 16):
        recs[i] = record([std_info(), file_name(5, "$meta%d" % i)])
    recs[5] = record([std_info(), file_name(5, "."), ], flags=3)
    recs[16] = record([std_info(), file_name(5, "Users")], flags=3)
    recs[17] = record([std_info(), file_name(16, "Ana")], flags=3)
    recs[18] = record([std_info(), file_name(17, "video.mp4"), data_nonres([(1000, 50), (3000, 70), (3070, 5)], 125 * CS)])
    recs[19] = record([std_info(), file_name(17, "PROGRA~1.TXT", 2), file_name(17, "Arquivo longo.txt", 1),
                       data_nonres([(7000, 2)], 8000)])
    recs[20] = record([std_info(), file_name(5, "pagefile.sys"), data_nonres([(9000, 100)], 100 * CS)])
    recs[21] = record([std_info(), file_name(16, "pequeno.txt"), data_resident(b"x" * 100)])
    recs[22] = record([std_info(), file_name(5, "apagado.bin"), data_nonres([(1, 1)], 10)], flags=0)  # deletado
    recs[23] = record([std_info(), file_name(17, "link-a.bin"), file_name(16, "link-b.bin"), data_nonres([(8000, 4)], 4 * CS)])
    # arquivo muito fragmentado: registro-base com o 1º pedaço + registro de extensão com o resto
    recs[24] = record([std_info(), file_name(17, "grande.vhdx"), data_nonres([(100, 2), (200, 2)], 8 * CS)])
    recs[25] = record([attr_nonres(0x80, mft.encode_runs([(300, 2), (400, 2)]), 4, 0)], base=24)
    recs[26] = record([std_info(), file_name(5, "System Volume Information")], flags=3)
    recs[27] = record([std_info(), file_name(26, "tracking.log"), data_nonres([(60, 9)], 9 * CS)])
    recs[28] = record([std_info(), file_name(11, "$Quota"), data_nonres([(70, 9)], 9 * CS)])  # dentro de $Extend
    recs[11] = record([std_info(), file_name(5, "$Extend")], flags=3)
    recs[29] = record([std_info(), file_name(17, "buraco.bin"), data_nonres([(500, 2)], 99 * CS)])  # vcn 0..1 ok, resto falta?
    recs[30] = record([std_info(0x800), file_name(17, "compactado.dat"), data_nonres([(5000, 3)], 3 * CS)])
    recs[31] = record([std_info(), file_name(40, "orfao.bin"), data_nonres([(5100, 3)], 3 * CS)])  # pai inexistente
    total = 32
    blob = b"".join(recs.get(i, b"\0" * REC) for i in range(total))
    # espalha a MFT em 2 trechos de 2 clusters (4 registros por cluster -> 16 registros por 4 clusters)
    # aqui: 32 registros = 8 clusters -> 4 trechos
    clusters = [blob[i * CS:(i + 1) * CS] for i in range(len(blob) // CS)]
    vol = bytearray(CS * 40)
    locs = [3, 9, 15, 22]
    runs, vcn = [], 0
    for c, lcn in zip(range(0, len(clusters), 2), locs):
        vol[lcn * CS:lcn * CS + 2 * CS] = clusters[c] + clusters[c + 1]
        runs.append(Extent(vcn, lcn, 2))
        vcn += 2
    return bytes(vol), runs, total


def scan_volume(vol, runs, total, chunk):
    def read(off, n):
        return vol[off:off + n]
    sc = mft.MftScanner("C:\\", CS)
    for idx, rec in mft.iter_mft_records(read, runs, CS, REC, total, chunk_bytes=chunk):
        sc.feed(idx, rec)
    return sc


@pytest.mark.parametrize("chunk", [REC * 3, CS, 1 << 20])  # inclui chunk que corta o registro no meio do cluster
def test_full_scan(chunk):
    vol, runs, total = build_fake_volume()
    sc = scan_volume(vol, runs, total, chunk)
    got = {p: (size, attrs, ex) for p, size, attrs, ex in sc.results()}
    base = "C:\\Users\\Ana\\"
    ex = got[base + "video.mp4"][2]
    assert ex == [Extent(0, 1000, 50), Extent(50, 3000, 70), Extent(120, 3070, 5)]
    assert got[base + "video.mp4"][0] == 125 * CS
    assert base + "Arquivo longo.txt" in got and base + "PROGRA~1.TXT" not in got  # nome 8.3 ignorado
    assert got["C:\\pagefile.sys"][2] == [Extent(0, 9000, 100)]  # o filtro de nomes de sistema é do backend
    assert got["C:\\Users\\pequeno.txt"] == (100, 0x20, [])  # residente: sem extents
    assert "C:\\apagado.bin" not in got  # registro deletado
    assert got[base + "link-a.bin"][2] == got["C:\\Users\\link-b.bin"][2] == [Extent(0, 8000, 4)]  # 2 links
    assert got[base + "grande.vhdx"][2] == [Extent(0, 100, 2), Extent(2, 200, 2), Extent(4, 300, 2), Extent(6, 400, 2)]
    assert not any("System Volume Information" in p or "$Quota" in p for p in got)
    assert got[base + "compactado.dat"][1] == 0x800  # atributos repassados (o backend decide pular)
    assert got[base + "buraco.bin"][2] is None  # cobre 2 de 99 clusters: incompleto
    assert "C:\\Users\\Ana\\orfao.bin" not in got and not any("orfao" in p for p in got)  # cadeia quebrada
    assert not any("$meta" in p or "$MFT" in p for p in got)  # metadados fora


def test_extension_before_base_and_missing_piece():
    sc = mft.MftScanner("D:\\", CS)
    sc.feed(5, bytearray(record([std_info(), file_name(5, ".")], flags=3)))
    sc.feed(40, bytearray(record([attr_nonres(0x80, mft.encode_runs([(300, 2)]), 2, 0)], base=39)))  # extensão primeiro
    sc.feed(39, bytearray(record([std_info(), file_name(5, "f.bin"), data_nonres([(100, 2)], 4 * CS)])))
    sc.feed(41, bytearray(record([std_info(), file_name(5, "g.bin"), data_nonres([(100, 2)], 4 * CS)])))
    got = {p: ex for p, _s, _a, ex in sc.results()}
    assert got["D:\\f.bin"] == [Extent(0, 100, 2), Extent(2, 300, 2)]
    # g.bin perdeu o 2º pedaço (cobre só 2 dos 4 clusters): não confiamos, o backend confere o arquivo
    assert got["D:\\g.bin"] is None


def test_gap_with_attribute_list_falls_back():
    sc = mft.MftScanner("D:\\", CS)
    sc.feed(5, bytearray(record([std_info(), file_name(5, ".")], flags=3)))
    lst = attr_resident(0x20, b"\0" * 32)
    sc.feed(50, bytearray(record([std_info(), file_name(5, "h.bin"), lst, data_nonres([(100, 2)], 4 * CS)])))
    got = {p: ex for p, _s, _a, ex in sc.results()}
    assert got["D:\\h.bin"] is None  # faltou pedaço: o backend consulta o arquivo da forma clássica


def test_mft_runs_from_record0():
    vol, runs, total = build_fake_volume()
    rec0 = bytearray(vol[3 * CS:3 * CS + REC])
    ex, size = mft.mft_runs_from_record0(rec0)
    assert ex == [Extent(0, 3, 2), Extent(2, 9, 2), Extent(4, 15, 2), Extent(6, 22, 2)] and size == 32 * REC
    with pytest.raises(mft.Unsupported):
        mft.mft_runs_from_record0(bytearray(record([std_info()], flags=0)))
    with pytest.raises(mft.Unsupported):
        mft.mft_runs_from_record0(bytearray(record([std_info(), attr_resident(0x20, b"\0" * 32),
                                                    data_nonres([(1, 2)], REC)])))


def test_short_read_raises():
    vol, runs, total = build_fake_volume()
    with pytest.raises(mft.Unsupported):
        list(mft.iter_mft_records(lambda off, n: vol[off:off + n - 1], runs, CS, REC, total))
    sparse = [Extent(0, -1, 2)]
    with pytest.raises(mft.Unsupported):
        list(mft.iter_mft_records(lambda off, n: b"", sparse, CS, REC, 8))


def test_parse_speed_reasonable():
    rec = record([std_info(), file_name(5, "arquivo-de-teste.dat"), data_nonres([(1000, 50), (3000, 70)], 120 * CS)])
    n = 20000
    t = time.perf_counter()
    for _ in range(n):
        assert mft.parse_record(bytearray(rec)) is not None
    per = (time.perf_counter() - t) / n
    assert per < 200e-6, "parse_record muito lento: %.1f us" % (per * 1e6)
