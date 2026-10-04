"""Index a MediaRecorder WebM so browsers can seek in long recordings.

Chrome's MediaRecorder writes WebM as a live stream: the Segment and every
Cluster have "unknown" size and there is neither a Duration nor a Cues index.
Short files play, but seeking in an hour-long file is slow or impossible.
``finalize`` copies every frame unchanged (no re-encoding) into a file with
known element sizes, a Duration and one cue point per video keyframe.
A file cut off mid-write (browser closed, power loss) keeps every complete
frame; only the incomplete tail is dropped.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field

EBML_HEADER = 0x1A45DFA3
SEGMENT = 0x18538067
SEEK_HEAD = 0x114D9B74
SEEK = 0x4DBB
SEEK_ID = 0x53AB
SEEK_POSITION = 0x53AC
INFO = 0x1549A966
TIMECODE_SCALE = 0x2AD7B1
DURATION = 0x4489
TRACKS = 0x1654AE6B
TRACK_ENTRY = 0xAE
TRACK_NUMBER = 0xD7
TRACK_TYPE = 0x83
VIDEO = 0xE0
PIXEL_WIDTH = 0xB0
PIXEL_HEIGHT = 0xBA
CODEC_ID = 0x86
CLUSTER = 0x1F43B675
TIMECODE = 0xE7
SIMPLE_BLOCK = 0xA3
BLOCK_GROUP = 0xA0
BLOCK = 0xA1
REFERENCE_BLOCK = 0xFB
CUES = 0x1C53BB6B
CUE_POINT = 0xBB
CUE_TIME = 0xB3
CUE_TRACK_POSITIONS = 0xB7
CUE_TRACK = 0xF7
CUE_CLUSTER_POSITION = 0xF1
CUE_RELATIVE_POSITION = 0xF0
VOID = 0xEC
CRC32 = 0xBF
TOP_LEVEL = {
    SEEK_HEAD,
    INFO,
    TRACKS,
    CLUSTER,
    CUES,
    0x1254C367,  # Tags
    0x1043A770,  # Chapters
    0x1941A469,  # Attachments
}
COPY_BLOCK = 1024 * 1024
MIN_CUE_GAP_S = 0.2


class WebmError(ValueError):
    """The file is not a usable WebM recording."""


@dataclass
class Frame:
    time: int  # absolute timecode (TimecodeScale units)
    key: bool
    track: int
    offset: int  # element offset relative to the cluster data start


@dataclass
class Cluster:
    data_start: int
    data_len: int
    timecode: int
    frames: list[Frame] = field(default_factory=list)


@dataclass
class Layout:
    header: bytes
    info_children: bytes
    tracks: bytes
    scale: int
    video_track: int | None
    codec: str
    width: int | None
    height: int | None
    clusters: list[Cluster]
    truncated: bool
    indexed: bool  # Duration and Cues were present already


# ---------- reading ----------


def _vint(f, keep_marker=False):
    first = f.read(1)
    if not first:
        raise EOFError
    b0 = first[0]
    length, mask = 1, 0x80
    while length <= 8 and not b0 & mask:
        length += 1
        mask >>= 1
    if length > 8:
        raise WebmError("Invalid EBML number")
    rest = f.read(length - 1)
    if len(rest) < length - 1:
        raise EOFError
    raw = int.from_bytes(first + rest, "big")
    if keep_marker:
        if length > 4:
            raise WebmError("Invalid EBML element ID")
        return raw, length, False
    value = raw & ((1 << (7 * length)) - 1)
    return value, length, value == (1 << (7 * length)) - 1


def _header(f):
    """(element id, data size or None if unknown, header length)."""
    eid, n1, _ = _vint(f, keep_marker=True)
    size, n2, unknown = _vint(f)
    return eid, None if unknown else size, n1 + n2


def _children(data: bytes):
    """Iterate (id, payload, raw element bytes) of a fully read master element."""
    import io

    f = io.BytesIO(data)
    while f.tell() < len(data):
        start = f.tell()
        try:
            eid, size, hl = _header(f)
        except EOFError:
            raise WebmError("Truncated header element")
        if size is None or start + hl + size > len(data):
            raise WebmError("Invalid element size in header")
        payload = f.read(size)
        yield eid, payload, data[start : start + hl + size]


def _uint(payload: bytes) -> int:
    return int.from_bytes(payload, "big") if payload else 0


def _block_head(f, size):
    head = f.read(min(size, 12))
    import io

    try:
        track, n, _ = _vint(io.BytesIO(head))
    except EOFError:
        raise WebmError("Truncated block")
    if len(head) < n + 3:
        raise WebmError("Truncated block")
    rel = struct.unpack(">h", head[n : n + 2])[0]
    return track, rel, head[n + 2]


def _scan_cluster(f, data_start, known_end, seg_end):
    limit = min(known_end if known_end is not None else seg_end, seg_end)
    pos, timecode, frames, cut = data_start, None, [], False
    while pos < limit:
        f.seek(pos)
        try:
            eid, size, hl = _header(f)
        except (EOFError, WebmError):
            cut = True
            break
        if known_end is None and eid in TOP_LEVEL:
            break  # the next cluster (or index) begins
        if size is None or pos + hl + size > limit:
            cut = True  # element was not written completely
            break
        body = pos + hl
        if eid == TIMECODE:
            timecode = _uint(f.read(size))
        elif eid == SIMPLE_BLOCK:
            track, rel, flags = _block_head(f, size)
            frames.append((rel, bool(flags & 0x80), track, pos - data_start))
        elif eid == BLOCK_GROUP:
            group = f.read(size)
            block, key = None, True
            for cid, payload, _ in _children(group):
                if cid == BLOCK:
                    import io

                    block = _block_head(io.BytesIO(payload), len(payload))
                elif cid == REFERENCE_BLOCK:
                    key = False
            if block:
                frames.append((block[1], key, block[0], pos - data_start))
        pos = body + size
    if known_end is not None and not cut:
        pos = known_end
    if timecode is None or not frames:
        return None, pos, cut
    return (
        Cluster(
            data_start,
            pos - data_start,
            timecode,
            [Frame(timecode + rel, key, track, off) for rel, key, track, off in frames],
        ),
        pos,
        cut,
    )


def scan(path) -> Layout:
    total = os.path.getsize(path)
    with open(path, "rb") as f:
        try:
            eid, size, hl = _header(f)
        except EOFError:
            raise WebmError("Empty file")
        if eid != EBML_HEADER or size is None:
            raise WebmError("Not a WebM/Matroska file")
        f.seek(0)
        header = f.read(hl + size)
        try:
            eid, seg_size, hl = _header(f)
        except EOFError:
            raise WebmError("No Segment")
        if eid != SEGMENT:
            raise WebmError("No Segment")
        seg_start = f.tell()
        seg_end = total if seg_size is None else min(total, seg_start + seg_size)
        info = tracks = None
        clusters, truncated, has_cues = [], False, False
        pos = seg_start
        while pos < seg_end:
            f.seek(pos)
            try:
                eid, size, hl = _header(f)
            except (EOFError, WebmError):
                truncated = True
                break
            data = pos + hl
            if eid == CLUSTER:
                cluster, end, cut = _scan_cluster(
                    f, data, None if size is None else data + size, seg_end
                )
                if cluster:
                    clusters.append(cluster)
                if cut:
                    truncated = True
                    break
                pos = end
                continue
            if size is None:
                raise WebmError(f"Unsupported unknown-size element {eid:#x}")
            if data + size > seg_end:
                truncated = True
                break
            if eid == INFO:
                f.seek(data)
                info = f.read(size)
            elif eid == TRACKS:
                f.seek(pos)
                tracks = f.read(hl + size)
            elif eid == CUES:
                has_cues = True
            pos = data + size
    if info is None or tracks is None:
        raise WebmError("Missing Info or Tracks")
    scale, children, has_duration = 1_000_000, [], False
    for cid, payload, raw in _children(info):
        if cid == TIMECODE_SCALE:
            scale = _uint(payload) or 1_000_000
        if cid == DURATION:
            has_duration = True
        if cid not in (DURATION, CRC32, VOID):
            children.append(raw)
    video_track, codec, width, height = None, "", None, None
    for cid, entries, _ in _children(tracks):
        if cid != TRACKS:
            continue
        for eid2, entry, _ in _children(entries):
            if eid2 != TRACK_ENTRY:
                continue
            number, kind, entry_codec, w, h = None, None, "", None, None
            for k, v, _ in _children(entry):
                if k == TRACK_NUMBER:
                    number = _uint(v)
                elif k == TRACK_TYPE:
                    kind = _uint(v)
                elif k == CODEC_ID:
                    entry_codec = v.decode("ascii", "replace")
                elif k == VIDEO:
                    for vk, vv, _ in _children(v):
                        if vk == PIXEL_WIDTH:
                            w = _uint(vv)
                        elif vk == PIXEL_HEIGHT:
                            h = _uint(vv)
            if kind == 1 and video_track is None:
                video_track, codec, width, height = number, entry_codec, w, h
    return Layout(
        header,
        b"".join(children),
        tracks,
        scale,
        video_track,
        codec,
        width,
        height,
        clusters,
        truncated,
        has_cues and has_duration,
    )


# ---------- writing ----------


def _id(eid: int) -> bytes:
    return eid.to_bytes((eid.bit_length() + 7) // 8, "big")


def _size(n: int, width: int | None = None) -> bytes:
    if width is None:
        width = 1
        while n >= (1 << (7 * width)) - 1:
            width += 1
    if n >= (1 << (7 * width)) - 1:
        raise WebmError("Element too large")
    return ((1 << (7 * width)) | n).to_bytes(width, "big")


def _el(eid: int, payload: bytes) -> bytes:
    return _id(eid) + _size(len(payload)) + payload


def _uint_el(eid: int, value: int, width: int | None = None) -> bytes:
    width = width or max(1, (value.bit_length() + 7) // 8)
    return _el(eid, value.to_bytes(width, "big"))


def _seek_head(info_pos, tracks_pos, cues_pos) -> bytes:
    seeks = b"".join(
        _el(SEEK, _el(SEEK_ID, _id(target)) + _uint_el(SEEK_POSITION, pos, 8))
        for target, pos in ((INFO, info_pos), (TRACKS, tracks_pos), (CUES, cues_pos))
    )
    return _el(SEEK_HEAD, seeks)


def _frame_step(times: list[int]) -> int:
    diffs = sorted(b - a for a, b in zip(times, times[1:]) if b > a)
    return diffs[len(diffs) // 2] if diffs else 0


def _summary(layout: Layout, duration_tc: int, cues: int) -> dict:
    frames = [fr for c in layout.clusters for fr in c.frames]
    return {
        "duration_ms": round(duration_tc * layout.scale / 1e6),
        "frames": len(frames),
        "keyframes": sum(fr.key for fr in frames),
        "clusters": len(layout.clusters),
        "cues": cues,
        "codec": layout.codec,
        "width": layout.width,
        "height": layout.height,
        "truncated": layout.truncated,
    }


def duration_tc(layout: Layout) -> int:
    times = sorted(fr.time for c in layout.clusters for fr in c.frames)
    if not times:
        return 0
    video = sorted(
        fr.time
        for c in layout.clusters
        for fr in c.frames
        if layout.video_track is None or fr.track == layout.video_track
    )
    return times[-1] + _frame_step(video or times)


def inspect(path) -> dict:
    """Summary of a recording without rewriting it."""
    layout = scan(path)
    if not layout.clusters:
        raise WebmError("No video frames")
    return _summary(layout, duration_tc(layout), 0)


def finalize(src, dst) -> dict:
    """Write an indexed copy of ``src`` to ``dst``; frames are copied unchanged."""
    layout = scan(src)
    if not layout.clusters:
        raise WebmError("No video frames")
    total_tc = duration_tc(layout)
    info = _el(INFO, layout.info_children + _el(DURATION, struct.pack(">d", float(total_tc))))
    head_len = len(_seek_head(0, 0, 0))
    info_pos = head_len
    tracks_pos = info_pos + len(info)
    pos = tracks_pos + len(layout.tracks)
    cluster_pos = []
    for c in layout.clusters:
        cluster_pos.append(pos)
        pos += len(_id(CLUSTER)) + 8 + c.data_len
    cues_pos = pos
    min_gap = MIN_CUE_GAP_S * 1e9 / layout.scale
    points, last = [], None
    for c, cpos in zip(layout.clusters, cluster_pos):
        for fr in c.frames:
            if not fr.key or (layout.video_track is not None and fr.track != layout.video_track):
                continue
            if last is not None and fr.time - last < min_gap:
                continue
            last = fr.time
            points.append(
                _el(
                    CUE_POINT,
                    _uint_el(CUE_TIME, fr.time)
                    + _el(
                        CUE_TRACK_POSITIONS,
                        _uint_el(CUE_TRACK, fr.track)
                        + _uint_el(CUE_CLUSTER_POSITION, cpos)
                        + _uint_el(CUE_RELATIVE_POSITION, fr.offset),
                    ),
                )
            )
    cues = _el(CUES, b"".join(points))
    seg_size = cues_pos + len(cues)
    with open(src, "rb") as fin, open(dst, "wb") as out:
        out.write(layout.header)
        out.write(_id(SEGMENT) + _size(seg_size, 8))
        out.write(_seek_head(info_pos, tracks_pos, cues_pos))
        out.write(info)
        out.write(layout.tracks)
        for c in layout.clusters:
            out.write(_id(CLUSTER) + _size(c.data_len, 8))
            fin.seek(c.data_start)
            left = c.data_len
            while left:
                block = fin.read(min(COPY_BLOCK, left))
                if not block:
                    raise WebmError("Source changed while indexing")
                out.write(block)
                left -= len(block)
        out.write(cues)
        out.flush()
        os.fsync(out.fileno())
    return _summary(layout, total_tc, len(points))


def verify(path, expected: dict) -> dict:
    """Re-read an indexed file: sizes known, index present, frames complete."""
    layout = scan(path)
    got = _summary(layout, duration_tc(layout), 0)
    if not layout.indexed or layout.truncated:
        raise WebmError("Indexed file is incomplete")
    if (got["frames"], got["clusters"]) != (expected["frames"], expected["clusters"]):
        raise WebmError("Indexed file lost frames")
    return got
