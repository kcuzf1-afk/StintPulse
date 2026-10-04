import ctypes as C
import struct
import sys
import uuid
import pytest
from ac_agent.shared_memory import (
    Physics,
    Graphics,
    Static,
    decode,
    decode_frame,
    ACSource,
    DecodeError,
    SessionTimer,
)
from ac_agent.models import Settings
from conftest import FIXTURES


def test_abi_offsets_fixed_utf16():
    assert [C.sizeof(s) for s in (Physics, Graphics, Static)] == [580, 296, 684]
    assert Physics.brakeBias.offset == 564
    assert Graphics.iCurrentTime.offset == 140
    assert Graphics.normalizedCarPosition.offset == 248
    assert Static.trackConfiguration.offset == 524
    assert Static.ersMaxJ.offset == 592


def test_decode_binary_fixture():
    p, g, s = [
        decode(cls, (FIXTURES / (name + ".bin")).read_bytes())
        for cls, name in (
            (Physics, "physics"),
            (Graphics, "graphics"),
            (Static, "static"),
        )
    ]
    meta, sample = decode_frame(p, g, s, Settings())
    assert meta.driver == "Jörg Müller"
    assert meta.layout == "long_configuration_24chars"
    assert meta.track_length == 4200
    assert sample.channels["speed"] == pytest.approx(183.4)
    assert sample.channels["gear"] == 4 and sample.channels["rpm"] == 8470
    assert sample.lap_ms == 23782 and sample.sector_index == 1
    assert sample.lap_pos == pytest.approx(0.29)
    assert sample.coords == (40, 0, 300)
    assert [w.corner for w in sample.tyres] == ["FL", "FR", "RL", "RR"]
    assert sample.tyres[0].pressure == pytest.approx(26.1)
    assert sample.channels["engine_temp"] is None
    assert sample.channels["abs_active"] is None
    assert sample.channels["drs"] is None
    assert sample.tyres[0].surface is None
    assert sample.channels["raw_physics_speedKmh"] == sample.channels["speed"]
    assert sample.channels["raw_graphics_currentSectorIndex"] == 1
    assert meta.raw_static["trackConfiguration"] == meta.layout


def test_reject_truncated_and_corrupt_pages():
    with pytest.raises(ValueError):
        decode(Physics, b"\0" * 20)
    corrupt = Graphics()
    corrupt.status = 42
    with pytest.raises(DecodeError):
        decode_frame(Physics(), corrupt, Static(), Settings())


def test_non_windows_does_not_create_pages():
    if sys.platform == "win32":
        pytest.skip("non-Windows test")
    src = ACSource()
    assert src.read(Settings()) is None and not src.maps
    assert src.state == "unsupported_platform"


def test_timer_units_verified_from_clock_rate():
    timer = SessionTimer()
    assert timer.read(0, 1000, 600000, 2) is None
    assert timer.read(0, 2500, 598500, 2) == 598500 and timer.unit == "ms"
    timer = SessionTimer()
    assert timer.read(0, 1000, 600, 2) is None
    assert timer.read(0, 2500, 598.5, 2) == 598500 and timer.unit == "s"
    timer = SessionTimer()
    assert timer.read(0, 1000, -1, 2) is None
    assert timer.read(0, 1000, 600, 3) is None
    timer.read(0, 1000, 600, 2)
    assert timer.read(0, 3000, 600, 2) is None


def _exists(name):
    k = C.WinDLL("kernel32", use_last_error=True)
    k.OpenFileMappingW.argtypes = [C.c_uint32, C.c_int, C.c_wchar_p]
    k.OpenFileMappingW.restype = C.c_void_p
    k.CloseHandle.argtypes = [C.c_void_p]
    handle = k.OpenFileMappingW(4, False, name)
    if handle:
        k.CloseHandle(handle)
    return bool(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named mmap integration")
def test_windows_named_mapping_reader_is_read_only_and_never_creates_pages():
    """Uses unique test mapping names only; never the real acpmf_* names."""
    import mmap

    prefix = "ac-agent-test-" + uuid.uuid4().hex
    names = [
        (prefix + "-" + key, cls)
        for key, cls in (("physics", Physics), ("graphics", Graphics), ("static", Static))
    ]
    assert not any(n.startswith("acpmf") for n, _ in names)
    probe = lambda: {"status": "unavailable", "game": [], "launcher": [], "error": "test"}
    source = ACSource(process_probe=probe)
    source.names = tuple(names)
    source._reset_page_info()
    # 1) Missing pages: no data and no mapping is created by the reader.
    assert source.read(Settings()) is None
    assert source.state == "waiting_game"
    assert not any(_exists(n) for n, _ in names)
    pages = []
    try:
        for (mapping, cls), key in zip(names, ("physics", "graphics", "static")):
            page = mmap.mmap(-1, C.sizeof(cls), tagname=mapping)
            page[:] = (FIXTURES / (key + ".bin")).read_bytes()
            pages.append(page)
        source.next_connect_at = 0
        # 2) Present pages, process check unavailable: data still flows.
        meta, sample = source.read(Settings())
        assert meta.source == "ac" and sample.channels["speed"] == pytest.approx(183.4)
        diag = source.diagnostics()
        for info in diag["pages"]:
            assert info["present"] and info["readable"] and info["initialized"]
            assert info["read_only"] is True  # view is PAGE_READONLY
            assert info["read_bytes"] == info["expected_bytes"]
            assert info["mapped_bytes"] >= info["expected_bytes"]
        assert source.read(Settings()) is None  # unchanged packets not recorded twice
        pages[0][0:4] = struct.pack("<i", 714)
        assert source.read(Settings())[1].packet_id == 714
    finally:
        source.close()
        for page in pages:
            page.close()
    assert not any(_exists(n) for n, _ in names)
