"""In-process fake of the three original-AC pages for platform-independent tests.

Only used through ACSource(page_opener=..., process_probe=...); it never touches
Windows named objects, so it can never write into real AC shared memory.
"""

import ctypes as C
import struct

from ac_agent.shared_memory import (
    ERROR_ACCESS_DENIED,
    ERROR_FILE_NOT_FOUND,
    MappingError,
    Graphics,
    Physics,
    Static,
)
from conftest import FIXTURES

FIXTURE_FILES = {
    "acpmf_physics": "physics.bin",
    "acpmf_graphics": "graphics.bin",
    "acpmf_static": "static.bin",
}


class FakePage:
    def __init__(self, game, name, size):
        self.game, self.name, self.size = game, name, size
        # Like a real view: keeps the mapping object alive after the game quits.
        self.buffer = game.pages[name]
        self.read_only = True
        self.mapped_bytes = 4096
        self.closed = False

    def read(self, count=None):
        return bytes(self.buffer[: self.size if count is None else count])

    def close(self):
        self.closed = True
        self.game.open_pages.discard(self)


class FakeGame:
    def __init__(self):
        self.pages = {}
        self.denied = set()
        self.process_status = "found"
        self.launcher = []
        self.open_pages = set()
        self.opens = 0

    def start(self, with_static=True):
        for name, file in FIXTURE_FILES.items():
            data = (FIXTURES / file).read_bytes()
            if name == "acpmf_static" and not with_static:
                data = bytes(C.sizeof(Static))
            if name in self.pages:
                self.pages[name][:] = data  # the game fills the existing page
            else:
                self.pages[name] = bytearray(data)
        return self

    def quit(self):
        self.pages = {}  # names vanish; open views keep their last contents
        self.process_status = "not_found"

    def set_packets(self, physics, graphics):
        struct.pack_into("<i", self.pages["acpmf_physics"], 0, physics)
        struct.pack_into("<i", self.pages["acpmf_graphics"], 0, graphics)

    def set_status(self, status):
        struct.pack_into("<i", self.pages["acpmf_graphics"], Graphics.status.offset, status)

    def set_static_int(self, field, value):
        struct.pack_into("<i", self.pages["acpmf_static"], getattr(Static, field).offset, value)

    def opener(self, name, size):
        if name in self.denied:
            raise MappingError(name, ERROR_ACCESS_DENIED, "Access is denied.")
        if name not in self.pages:
            raise MappingError(name, ERROR_FILE_NOT_FOUND, "The system cannot find the file specified.")
        self.opens += 1
        page = FakePage(self, name, size)
        self.open_pages.add(page)
        return page

    def probe(self):
        return {
            "status": self.process_status,
            "game": ["acs.exe"] if self.process_status == "found" else [],
            "launcher": list(self.launcher),
            "error": "AccessDenied: test" if self.process_status == "unavailable" else None,
        }


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def make_source(game, clock=None):
    from ac_agent.shared_memory import ACSource

    return ACSource(page_opener=game.opener, process_probe=game.probe, clock=clock or Clock())


assert C.sizeof(Physics) == 580
