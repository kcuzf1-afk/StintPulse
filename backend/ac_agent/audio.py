"""Sound for onboard recordings, captured by the PC app itself (Windows).

The OBS Virtual Camera carries no audio, so the sound comes straight from
Windows (WASAPI loopback, read-only, no driver needed):

* ``game``   process loopback: only what Assetto Corsa (acs.exe and its child
             processes) plays - no Discord, music or system sounds.
             Windows 10 version 2004 or newer.
* ``system`` loopback of the default playback device: everything audible.

Samples are written continuously to a 16-bit PCM WAV file next to the video.
Every WASAPI packet carries a QPC timestamp, converted to the server wall
clock (``time.time()``) - the clock of telemetry and video. The file timeline
is kept on that clock: while the game is silent WASAPI delivers nothing, such
gaps become silence, and clock drift is corrected in steps of at most 20 ms.
Sample n therefore belongs to ``started_at + n / rate`` on the server clock.
"""

from __future__ import annotations

import ctypes
import logging
import math
import os
import struct
import sys
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

RATE = 48000
CHANNELS = 2
SAMPLE_BYTES = 2
HEADER = 44
MAX_DRIFT_S = 0.02
WAV_LIMIT = 0xFFFFFFFF - 64  # RIFF sizes are 32 bit (about 6 h at 48 kHz stereo)
GAME_PROCESSES = ("acs.exe", "acs_x86.exe")


class AudioError(Exception):
    """Capture could not be started or failed."""


class AudioUnavailable(AudioError):
    """Nothing to capture (e.g. Assetto Corsa is not running)."""


# ---------- WAV file on the server clock ----------


def wav_header(data_bytes, rate=RATE, channels=CHANNELS):
    block = channels * SAMPLE_BYTES
    return (
        b"RIFF"
        + struct.pack("<I", 36 + data_bytes)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * block, block, 16)
        + b"data"
        + struct.pack("<I", data_bytes)
    )


def wav_info(path):
    """(rate, channels, frames) from a WAV written by WavWriter."""
    with open(path, "rb") as f:
        head = f.read(HEADER)
    if len(head) < HEADER or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise AudioError("Not a WAV file")
    channels, rate = struct.unpack("<HI", head[22:28])
    data = struct.unpack("<I", head[40:44])[0]
    return rate, channels, data // (channels * SAMPLE_BYTES)


def repair_wav(path):
    """Fix the sizes of a WAV that was cut off (crash, power loss)."""
    path = Path(path)
    rate, channels, _ = wav_info(path)
    block = channels * SAMPLE_BYTES
    data = max(0, path.stat().st_size - HEADER)
    data -= data % block
    data = min(data, WAV_LIMIT - HEADER - (WAV_LIMIT - HEADER) % block)
    with open(path, "r+b") as f:
        f.write(wav_header(data, rate, channels))
        f.truncate(HEADER + data)
    return rate, channels, data // block


class WavWriter:
    def __init__(self, path, rate=RATE, channels=CHANNELS):
        self.path = Path(path)
        self.rate, self.channels = rate, channels
        self.block = channels * SAMPLE_BYTES
        self.frames = 0
        self.full = False
        self.file = open(self.path, "wb")
        self.file.write(wav_header(0, rate, channels))

    @property
    def bytes(self):
        return HEADER + self.frames * self.block

    def write(self, pcm: bytes):
        if self.full or not pcm:
            return
        room = WAV_LIMIT - self.bytes
        if len(pcm) > room:
            pcm = pcm[: room - room % self.block]
            self.full = True
        self.file.write(pcm)
        self.frames += len(pcm) // self.block

    def silence(self, frames):
        while frames > 0 and not self.full:
            n = min(frames, self.rate)
            self.write(bytes(n * self.block))
            frames -= n

    def close(self):
        if self.file.closed:
            return
        self.file.flush()
        self.file.seek(0)
        self.file.write(wav_header(self.frames * self.block, self.rate, self.channels))
        self.file.flush()
        os.fsync(self.file.fileno())
        self.file.close()


class Timeline:
    """Keeps sample n of the file at ``started_at + n / rate``."""

    def __init__(self, writer: WavWriter):
        self.writer = writer
        self.started_at = None
        self.dropped = 0
        self.padded = 0

    def begin(self, wall):
        if self.started_at is None:
            self.started_at = wall

    def position(self):
        return self.started_at + self.writer.frames / self.writer.rate

    def add(self, wall, pcm: bytes):
        """``wall`` = server time of the first frame of this packet."""
        block = self.writer.block
        if self.started_at is None:
            self.started_at = wall
        else:
            drift = wall - self.position()
            if drift > MAX_DRIFT_S:  # nothing was played meanwhile
                frames = round(drift * self.writer.rate)
                self.writer.silence(frames)
                self.padded += frames
            elif drift < -MAX_DRIFT_S:  # overlap: device clock ran ahead
                drop = min(len(pcm) // block, round(-drift * self.writer.rate))
                pcm = pcm[drop * block :]
                self.dropped += drop
        self.writer.write(pcm)

    def pad_until(self, wall):
        if self.started_at is not None and wall > self.position():
            self.writer.silence(round((wall - self.position()) * self.writer.rate))


class _Capture:
    """Common thread/stop logic; subclasses implement ``_run``."""

    mode = "test"

    def __init__(self, path):
        self.path = Path(path)
        self.writer: WavWriter | None = None
        self.timeline: Timeline | None = None
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.state = "starting"
        self.error = ""
        self.thread = threading.Thread(target=self._guarded, daemon=True, name="ac-audio")

    def start(self, timeout=5.0):
        self.thread.start()
        if not self.ready.wait(timeout):
            self.stop_event.set()
            raise AudioError("Audio capture did not start")
        if self.state == "failed":
            raise AudioError(self.error or "Audio capture failed")
        return self

    def _guarded(self):
        try:
            self._run()
        except Exception as exc:  # reported, never raised into the app
            self.state = "failed"
            self.error = str(exc)[:200]
            log.warning("Audio capture stopped: %s", self.error)
        finally:
            self.ready.set()

    def _open_writer(self, rate, channels):
        self.writer = WavWriter(self.path, rate, channels)
        self.timeline = Timeline(self.writer)

    @property
    def bytes(self):
        return self.writer.bytes if self.writer else 0

    def status(self):
        return {"status": "failed" if self.state == "failed" else "recording", "mode": self.mode, "error": self.error or None}

    def stop(self, until=None):
        """Stop, pad silence up to ``until`` (server time) and close the file."""
        self.stop_event.set()
        self.thread.join(5)
        if not self.writer:
            return {"status": "failed", "mode": self.mode, "error": self.error or "no audio"}
        if until is not None:
            self.timeline.pad_until(until)
        self.writer.close()
        return {
            "status": "failed" if self.state == "failed" and not self.writer.frames else "ready",
            "mode": self.mode,
            "error": self.error or None,
            "started_at": self.timeline.started_at,
            "sample_rate": self.writer.rate,
            "channels": self.writer.channels,
            "frames": self.writer.frames,
            "duration_ms": round(self.writer.frames / self.writer.rate * 1000),
            "bytes": self.writer.bytes,
            "truncated": self.writer.full,
        }


class SyntheticCapture(_Capture):
    """Test-only sound (AC_AGENT_TEST_AUDIO): a quiet tone on the server clock."""

    mode = "test"

    def _run(self):
        self._open_writer(RATE, CHANNELS)
        self.timeline.begin(time.time())
        self.state = "recording"
        self.ready.set()
        import numpy as np

        phase = 0
        while not self.stop_event.wait(0.02):
            now = time.time()
            frames = max(0, round((now - self.timeline.position()) * RATE))
            if not frames:
                continue
            t = (phase + np.arange(frames)) / RATE
            mono = (3000 * np.sin(2 * np.pi * 440 * t)).astype("<i2")
            phase += frames
            self.timeline.add(now - frames / RATE, np.repeat(mono, CHANNELS).tobytes())


def find_game_pid():
    try:
        import psutil
    except ImportError:
        return None
    for proc in psutil.process_iter(["name", "pid"]):
        if (proc.info.get("name") or "").lower() in GAME_PROCESSES:
            return proc.info["pid"]
    return None


def open_capture(path, mode):
    """Capture object for the configured sound source (not started yet)."""
    if os.environ.get("AC_AGENT_TEST_AUDIO"):
        return SyntheticCapture(path)
    if sys.platform != "win32":
        raise AudioUnavailable("Sound recording needs Windows")
    if mode == "game":
        # AC_AGENT_AUDIO_PID: automated tests only (a test process as "game").
        pid = int(os.environ.get("AC_AGENT_AUDIO_PID") or 0) or find_game_pid()
        if not pid:
            raise AudioUnavailable("Assetto Corsa (acs.exe) is not running")
        return WasapiCapture(path, "game", pid)
    return WasapiCapture(path, "system")


# ---------- WASAPI (Windows only, ctypes, read-only loopback) ----------

AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR = 0x4
AUDCLNT_S_BUFFER_EMPTY = 0x08890001
CLSCTX_ALL = 0x17
COINIT_MULTITHREADED = 0x0
E_NOINTERFACE = -2147467262
VT_BLOB = 65
PROCESS_LOOPBACK = 1
INCLUDE_TARGET_PROCESS_TREE = 0
BUFFER_HNS = 2_000_000  # 200 ms
WAVE_FORMAT_PCM = 1
WAVE_FORMAT_IEEE_FLOAT = 3
WAVE_FORMAT_EXTENSIBLE = 0xFFFE
IEEE_FLOAT_SUBTYPE = uuid.UUID("00000003-0000-0010-8000-00aa00389b71").bytes_le

IID_IUnknown = "00000000-0000-0000-C000-000000000046"
IID_IAgileObject = "94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90"
IID_ICompletionHandler = "41D949AB-9862-444A-80F6-C261334DA5EB"
IID_IAudioClient = "1CB9AD4C-DBFA-4C32-B178-C2F568A703B2"
IID_IAudioCaptureClient = "C8ADBD64-E71E-48A0-A4DE-185C395CD317"
CLSID_MMDeviceEnumerator = "BCDE0395-E52F-467C-8E3D-C4579291692E"
IID_IMMDeviceEnumerator = "A95664D2-9614-4F35-A746-DE8DB63617E6"


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text):
    g = GUID()
    ctypes.memmove(ctypes.byref(g), uuid.UUID(text).bytes_le, 16)
    return g


class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("wFormatTag", ctypes.c_uint16),
        ("nChannels", ctypes.c_uint16),
        ("nSamplesPerSec", ctypes.c_uint32),
        ("nAvgBytesPerSec", ctypes.c_uint32),
        ("nBlockAlign", ctypes.c_uint16),
        ("wBitsPerSample", ctypes.c_uint16),
        ("cbSize", ctypes.c_uint16),
    ]


class _LoopbackParams(ctypes.Structure):
    _fields_ = [("TargetProcessId", ctypes.c_uint32), ("ProcessLoopbackMode", ctypes.c_int)]


class _ActivationParams(ctypes.Structure):
    _fields_ = [("ActivationType", ctypes.c_int), ("ProcessLoopbackParams", _LoopbackParams)]


class _Blob(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("pBlobData", ctypes.c_void_p)]


class _PropVariant(ctypes.Structure):
    _fields_ = [
        ("vt", ctypes.c_uint16),
        ("r1", ctypes.c_uint16),
        ("r2", ctypes.c_uint16),
        ("r3", ctypes.c_uint16),
        ("blob", _Blob),
    ]


def _com(ptr, index, name, argtypes, *args):
    """Call method ``index`` of a COM interface pointer; raise on failure."""
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    hr = proto(vtbl[index])(ptr, *args)
    if hr < 0:
        raise AudioError(f"{name} failed (0x{hr & 0xFFFFFFFF:08X})")
    return hr


def _release(ptr):
    if ptr and getattr(ptr, "value", ptr):
        vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])(ptr)


if sys.platform == "win32":
    _QI = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
    _REF = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
    _DONE = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)

    class _HandlerVtbl(ctypes.Structure):
        _fields_ = [("QueryInterface", _QI), ("AddRef", _REF), ("Release", _REF), ("ActivateCompleted", _DONE)]

    class _HandlerObject(ctypes.Structure):
        _fields_ = [("vtbl", ctypes.POINTER(_HandlerVtbl))]

    class _Completion:
        """IActivateAudioInterfaceCompletionHandler (+ IAgileObject) for
        ActivateAudioInterfaceAsync; lives on the stack of the caller."""

        ACCEPT = {
            uuid.UUID(i).bytes_le for i in (IID_IUnknown, IID_IAgileObject, IID_ICompletionHandler)
        }

        def __init__(self):
            self.event = threading.Event()
            self.hr = None
            self.client = ctypes.c_void_p()
            self._fns = (_QI(self._qi), _REF(lambda this: 1), _REF(lambda this: 1), _DONE(self._done))
            self.vtbl = _HandlerVtbl(*self._fns)
            self.obj = _HandlerObject(ctypes.pointer(self.vtbl))

        def _qi(self, this, riid, ppv):
            if ctypes.string_at(riid, 16) in self.ACCEPT:
                ppv[0] = this
                return 0
            ppv[0] = None
            return E_NOINTERFACE

        def _done(self, this, operation):
            try:
                hr, unk = ctypes.c_long(), ctypes.c_void_p()
                _com(
                    operation,
                    3,
                    "GetActivateResult",
                    [ctypes.POINTER(ctypes.c_long), ctypes.POINTER(ctypes.c_void_p)],
                    ctypes.byref(hr),
                    ctypes.byref(unk),
                )
                self.hr, self.client = hr.value, unk
            except Exception:
                self.hr = -1
            finally:
                self.event.set()
            return 0


def _pcm_format(rate=RATE, channels=CHANNELS):
    block = channels * SAMPLE_BYTES
    return WAVEFORMATEX(WAVE_FORMAT_PCM, channels, rate, rate * block, block, 16, 0)


class WasapiCapture(_Capture):
    def __init__(self, path, mode, pid=None):
        super().__init__(path)
        self.mode = mode
        self.pid = pid

    # -- activation --

    def _process_client(self):
        mmdevapi = ctypes.WinDLL("Mmdevapi.dll")
        activate = mmdevapi.ActivateAudioInterfaceAsync
        activate.argtypes = [
            ctypes.c_wchar_p,
            ctypes.POINTER(GUID),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        activate.restype = ctypes.c_long
        params = _ActivationParams(PROCESS_LOOPBACK, _LoopbackParams(self.pid, INCLUDE_TARGET_PROCESS_TREE))
        variant = _PropVariant()
        variant.vt = VT_BLOB
        variant.blob.cbSize = ctypes.sizeof(params)
        variant.blob.pBlobData = ctypes.cast(ctypes.pointer(params), ctypes.c_void_p)
        handler = _Completion()
        operation = ctypes.c_void_p()
        iid = _guid(IID_IAudioClient)
        hr = activate(
            "VAD\\Process_Loopback",
            ctypes.byref(iid),
            ctypes.addressof(variant),
            ctypes.addressof(handler.obj),
            ctypes.byref(operation),
        )
        if hr < 0:
            raise AudioError(f"Game sound not available (0x{hr & 0xFFFFFFFF:08X}); Windows 10 2004+ required")
        if not handler.event.wait(5):
            raise AudioError("Game sound activation timed out")
        _release(operation)
        if handler.hr is None or handler.hr < 0 or not handler.client.value:
            raise AudioError(f"Game sound activation failed (0x{(handler.hr or 0) & 0xFFFFFFFF:08X})")
        return handler.client

    def _device_client(self):
        ole32 = ctypes.WinDLL("ole32")
        enum = ctypes.c_void_p()
        clsid, iid = _guid(CLSID_MMDeviceEnumerator), _guid(IID_IMMDeviceEnumerator)
        hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_ALL, ctypes.byref(iid), ctypes.byref(enum))
        if hr < 0:
            raise AudioError(f"No audio devices (0x{hr & 0xFFFFFFFF:08X})")
        device, client = ctypes.c_void_p(), ctypes.c_void_p()
        try:
            _com(enum, 4, "GetDefaultAudioEndpoint", [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)], 0, 0, ctypes.byref(device))
            iid_client = _guid(IID_IAudioClient)
            _com(
                device,
                3,
                "Activate",
                [ctypes.POINTER(GUID), ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
                ctypes.byref(iid_client),
                CLSCTX_ALL,
                None,
                ctypes.byref(client),
            )
        finally:
            _release(device)
            _release(enum)
        return client

    def _initialize(self, client, fmt, flags):
        _com(
            client,
            3,
            "Initialize",
            [ctypes.c_int, ctypes.c_uint32, ctypes.c_longlong, ctypes.c_longlong, ctypes.POINTER(WAVEFORMATEX), ctypes.c_void_p],
            0,
            flags,
            BUFFER_HNS,
            0,
            ctypes.byref(fmt),
            None,
        )

    def _open(self):
        """(client, input block size, converter or None, rate, channels, event-driven)."""
        if self.mode == "game":
            client = self._process_client()
            fmt = _pcm_format()
            self._initialize(
                client,
                fmt,
                AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM,
            )
            return client, fmt.nBlockAlign, None, RATE, CHANNELS, True
        client = self._device_client()
        fmt = _pcm_format()
        try:
            self._initialize(
                client,
                fmt,
                AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY,
            )
            return client, fmt.nBlockAlign, None, RATE, CHANNELS, False
        except AudioError:
            _release(client)
        # Fallback: the device's own mix format, converted here.
        client = self._device_client()
        mix = ctypes.c_void_p()
        _com(client, 8, "GetMixFormat", [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(mix))
        try:
            head = WAVEFORMATEX.from_address(mix.value)
            tag, channels, rate, bits, block = head.wFormatTag, head.nChannels, head.nSamplesPerSec, head.wBitsPerSample, head.nBlockAlign
            if tag == WAVE_FORMAT_EXTENSIBLE:
                sub = ctypes.string_at(mix.value + 18 + 6, 16)  # after cbSize: samples, mask, SubFormat
                tag = WAVE_FORMAT_IEEE_FLOAT if sub == IEEE_FLOAT_SUBTYPE else WAVE_FORMAT_PCM
            self._initialize(client, WAVEFORMATEX.from_address(mix.value), AUDCLNT_STREAMFLAGS_LOOPBACK)
        finally:
            ctypes.WinDLL("ole32").CoTaskMemFree(mix)
        out_channels = min(2, channels) or 1
        return client, block, _converter(tag, bits, channels), rate, out_channels, False

    # -- capture loop --

    def _run(self):
        ole32 = ctypes.WinDLL("ole32")
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.CreateEventW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        client = capture = event = None
        try:
            client, in_block, convert, rate, channels, evented = self._open()
            capture = ctypes.c_void_p()
            iid = _guid(IID_IAudioCaptureClient)
            _com(client, 14, "GetService", [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(iid), ctypes.byref(capture))
            if evented:
                event = kernel32.CreateEventW(None, False, False, None)
                _com(client, 13, "SetEventHandle", [ctypes.c_void_p], event)
            self._open_writer(rate, channels)
            _com(client, 10, "Start", [])
            self.timeline.begin(time.time())
            self.state = "recording"
            self.ready.set()
            while not self.stop_event.is_set():
                if event:
                    kernel32.WaitForSingleObject(event, 100)
                else:
                    time.sleep(0.01)
                self._drain(capture, in_block, convert, rate)
            self._drain(capture, in_block, convert, rate)
            _com(client, 11, "Stop", [])
        finally:
            _release(capture)
            _release(client)
            if event:
                kernel32.CloseHandle(event)
            ole32.CoUninitialize()

    def _drain(self, capture, in_block, convert, rate):
        while True:
            size = ctypes.c_uint32()
            _com(capture, 5, "GetNextPacketSize", [ctypes.POINTER(ctypes.c_uint32)], ctypes.byref(size))
            if not size.value:
                return
            data, frames, flags = ctypes.c_void_p(), ctypes.c_uint32(), ctypes.c_uint32()
            device_pos, qpc = ctypes.c_uint64(), ctypes.c_uint64()
            hr = _com(
                capture,
                3,
                "GetBuffer",
                [ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64)],
                ctypes.byref(data),
                ctypes.byref(frames),
                ctypes.byref(flags),
                ctypes.byref(device_pos),
                ctypes.byref(qpc),
            )
            if hr == AUDCLNT_S_BUFFER_EMPTY:
                return
            count = frames.value
            if flags.value & AUDCLNT_BUFFERFLAGS_SILENT or not data.value:
                raw = bytes(count * in_block)
            else:
                raw = ctypes.string_at(data.value, count * in_block)
            _com(capture, 4, "ReleaseBuffer", [ctypes.c_uint32], count)
            # QPC position (100 ns) -> server wall clock; perf_counter is QPC.
            mono = qpc.value / 1e7
            now_mono = time.perf_counter()
            if flags.value & AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR or not qpc.value or abs(now_mono - mono) > 5:
                wall = time.time() - count / rate
            else:
                wall = time.time() - (now_mono - mono)
            pcm = convert(raw) if convert else raw
            try:
                self.timeline.add(wall, pcm)
            except OSError as exc:  # disk full: stop the sound, keep the video
                self.state = "failed"
                self.error = f"Sound could not be written: {exc}"[:200]
                self.stop_event.set()
                return


def _converter(tag, bits, channels):
    """Device mix format -> 16-bit PCM, at most two channels."""
    import numpy as np

    def convert(raw: bytes) -> bytes:
        if tag == WAVE_FORMAT_IEEE_FLOAT and bits == 32:
            x = np.frombuffer(raw, dtype="<f4")
        elif bits == 16:
            x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
        elif bits == 32:
            x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648
        elif bits == 24:
            b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
            v = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
            v = np.where(v & 0x800000, v - 0x1000000, v)
            x = v.astype(np.float32) / 8388608
        else:
            raise AudioError(f"Unsupported device format ({bits} bit)")
        x = x.reshape(-1, channels)[:, :2]
        return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()

    return convert
