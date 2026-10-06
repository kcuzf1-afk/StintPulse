"""Assetto Corsa setup files: lossless INI handling, the car's setup limits
(data/setup.ini) and how a saved VALUE maps to the value shown in the game.

Facts this module relies on (checked on real installations):
- A saved setup (Documents/Assetto Corsa/setups/<car>/<track|generic>/*.ini)
  has one [PARAM] section per item with an integer VALUE=. Unknown sections
  ([CAR], [ABOUT], [__EXT_PATCH] ...) must survive unchanged.
- The limits (MIN/MAX/STEP, NAME, TAB) live in content/cars/<car>/data/setup.ini.
  Most cars only ship the encrypted data.acd; it is NOT decrypted here. Without
  an unpacked or imported setup.ini every parameter stays "unbestätigt".
- How VALUE encodes the in-game value differs per parameter and car: the plain
  value, a click index from MIN, or (camber) tenths of a degree. SHOW_CLICKS is
  only a display hint and does not decide it, so the encoding is derived from
  VALUEs in the user's saved setups and must be unambiguous - or confirmed by
  the user from the in-game display - before a change may be exported.
"""
from __future__ import annotations

import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

MAX_INI_BYTES = 512 * 1024
NAME_PATTERN = re.compile(r"[A-Za-z0-9 _\-().]{1,60}")
# Car and track folder names as Assetto Corsa uses them (no separators).
FOLDER_PATTERN = re.compile(r"[A-Za-z0-9_\-. ]{1,100}")
SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*$")
KEY_VALUE = re.compile(r"^(\s*)([^=;/#\s][^=]*?)(\s*=\s*)(.*?)(\s*)$")
# Sections of data/setup.ini that are no setup items.
NON_PARAMS = {"DISPLAY_METHOD", "GEARS"}


def is_internal(section):
    """Car-script state (CSP) and file metadata: kept byte-identical in every
    export, but never shown, analysed or changed."""
    return section.startswith(("CUSTOM_SCRIPT_ITEM", "__")) or section in ("CAR", "ABOUT")
# Units only where Assetto Corsa defines them for every car.
UNITS = (("PRESSURE_", "psi"), ("CAMBER_", "°"), ("FUEL", "l"), ("FRONT_BIAS", "%"))
ENCODING_LABELS = {
    "value": "VALUE = Wert",
    "tenths": "VALUE = Wert × 10",
    "clicks": "VALUE = Klick ab MIN",
}


class SetupError(ValueError):
    """Invalid setup file or request; message is shown to the user."""


# ---------------------------------------------------------------- INI files


def decode(data: bytes):
    if len(data) > MAX_INI_BYTES:
        raise SetupError("Setup-Datei ist größer als 512 KB")
    if b"\x00" in data:
        raise SetupError("Keine Textdatei (INI erwartet)")
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            text = data.decode(encoding)
            bom = encoding == "utf-8-sig" and data.startswith(b"\xef\xbb\xbf")
            return text, ("utf-8" if encoding == "utf-8-sig" else encoding), bom
        except UnicodeDecodeError:
            continue
    raise SetupError("Unbekannte Zeichenkodierung")


@dataclass
class Entry:
    section: str
    key: str
    value: str
    line: int


class IniDoc:
    """Line-preserving INI document: changing one VALUE rewrites only the
    digits of that line; comments, order, unknown entries, line endings and
    the character encoding stay byte-identical."""

    def __init__(self, data: bytes):
        text, self.encoding, self.bom = decode(data)
        self.lines = text.splitlines(keepends=True)
        self.entries: list[Entry] = []
        self.sections: list[str] = []
        section = ""
        for i, raw in enumerate(self.lines):
            line = raw.rstrip("\r\n")
            stripped = line.strip()
            if not stripped or stripped.startswith((";", "//", "#")):
                continue
            m = SECTION.match(line)
            if m:
                section = m.group(1).strip().upper()
                self.sections.append(section)
                continue
            m = KEY_VALUE.match(line)
            if m and section:
                value = strip_comment(m.group(4))
                self.entries.append(Entry(section, m.group(2).strip().upper(), value, i))

    def values(self, key="VALUE"):
        """{SECTION: raw value} of the first occurrence of key per section."""
        out = {}
        for e in self.entries:
            if e.key == key and e.section not in out:
                out[e.section] = e.value
        return out

    def get(self, section, key):
        for e in self.entries:
            if e.section == section and e.key == key:
                return e.value
        return None

    def duplicates(self, key="VALUE"):
        seen, dup = set(), set()
        for e in self.entries:
            if e.key == key:
                (dup if e.section in seen else seen).add(e.section)
        return dup

    def with_values(self, changes: dict[str, int]) -> bytes:
        """New file content with VALUE= replaced for the given sections only."""
        lines = list(self.lines)
        dup = self.duplicates()
        for section, value in changes.items():
            if section in dup:
                raise SetupError(f"{section} kommt mehrfach vor - nicht eindeutig änderbar")
            entry = next(
                (e for e in self.entries if e.section == section and e.key == "VALUE"), None
            )
            if entry is None:
                raise SetupError(f"{section} fehlt im Ausgangssetup")
            raw = lines[entry.line]
            body = raw.rstrip("\r\n")
            ending = raw[len(body):]
            m = KEY_VALUE.match(body)
            old = m.group(4)
            comment = old[len(strip_comment(old)):] if strip_comment(old) != old else ""
            lines[entry.line] = (
                m.group(1) + m.group(2) + m.group(3) + str(int(value)) + comment + m.group(5) + ending
            )
        text = "".join(lines)
        data = text.encode(self.encoding)
        return (b"\xef\xbb\xbf" + data) if self.bom else data


def strip_comment(value):
    for marker in (";", "//"):
        index = value.find(marker)
        if index >= 0:
            value = value[:index]
    return value.strip()


def parse_int(raw):
    if raw is None or not re.fullmatch(r"[+-]?\d{1,12}", raw.strip()):
        return None
    return int(raw)


def parse_number(raw):
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# ------------------------------------------------------------ car limits


@dataclass
class ParamSpec:
    name: str
    label: str
    tab: str
    min: float | None
    max: float | None
    step: float | None
    show_clicks: int | None
    ratios: bool = False
    problems: list[str] = field(default_factory=list)

    @property
    def unit(self):
        return next((u for prefix, u in UNITS if self.name.startswith(prefix)), "")

    @property
    def usable(self):
        return not self.problems and not self.ratios


def parse_spec(data: bytes):
    """Parameter definitions from a car's data/setup.ini."""
    doc = IniDoc(data)
    by_section: dict[str, dict[str, str]] = {}
    for e in doc.entries:
        by_section.setdefault(e.section, {}).setdefault(e.key, e.value)
    params = {}
    for name, keys in by_section.items():
        if name in NON_PARAMS:
            continue
        lo, hi, step = (parse_number(keys.get(k)) for k in ("MIN", "MAX", "STEP"))
        sc = parse_int(keys.get("SHOW_CLICKS"))
        p = ParamSpec(
            name=name,
            label=keys.get("NAME", "")[:80] or name,
            tab=keys.get("TAB", "")[:40],
            min=lo,
            max=hi,
            step=step,
            show_clicks=sc,
            ratios="RATIOS" in keys,
        )
        if p.ratios:
            p.problems.append("Getriebe-Übersetzungsliste (wird nicht geändert)")
        elif lo is None or hi is None or step is None:
            p.problems.append("MIN/MAX/STEP fehlen")
        elif step <= 0 or hi < lo:
            p.problems.append("MIN/MAX/STEP widersprüchlich")
        params[name] = p
    if not any(p.usable for p in params.values()):
        raise SetupError("Keine Setup-Parameter mit MIN/MAX/STEP gefunden (ist das die setup.ini des Fahrzeugs?)")
    return params


# ----------------------------------------------------------- encodings


def _close(a, b):
    return abs(a - b) <= 1e-6 * max(1.0, abs(a), abs(b))


@dataclass(frozen=True)
class Encoding:
    kind: str  # value | tenths | clicks
    lo: float
    hi: float
    step: float

    def stored_range(self):
        if self.kind == "clicks":
            return 0.0, math.floor((self.hi - self.lo) / self.step + 1e-9)
        scale = 10 if self.kind == "tenths" else 1
        return self.lo * scale, self.hi * scale

    def accepts(self, stored: int):
        lo, hi = self.stored_range()
        if stored < lo - 1e-6 or stored > hi + 1e-6:
            return False
        if self.kind == "clicks":
            return True
        k = (stored - lo) / self.step
        return _close(k, round(k))

    def display(self, stored: int) -> float:
        if self.kind == "clicks":
            return round(self.lo + stored * self.step, 6)
        if self.kind == "tenths":
            return round(stored / 10, 6)
        return float(stored)

    def stored(self, display: float):
        """Integer VALUE for an in-game value, or None if not representable."""
        if self.kind == "clicks":
            raw = (display - self.lo) / self.step
        elif self.kind == "tenths":
            raw = display * 10
        else:
            raw = display
        if not _close(raw, round(raw)):
            return None
        value = int(round(raw))
        return value if self.accepts(value) else None

    def grid(self):
        """Display step between two valid VALUEs (for the user and the model)."""
        if self.kind == "clicks":
            return self.step
        step = self.step if _close(self.step, round(self.step)) else None
        if step is None:  # fractional STEP: only integer VALUEs exist
            step = math.ceil(self.step)
        return step / 10 if self.kind == "tenths" else step

    def same_as(self, other: "Encoding"):
        """Two encodings that map every VALUE to the same in-game value."""
        if self.kind == other.kind:
            return True
        lo, hi = self.stored_range()
        olo, ohi = other.stored_range()
        if not (_close(lo, olo) and _close(hi, ohi)) or hi - lo > 5000:
            return False
        values = [v for v in range(int(math.ceil(lo)), int(math.floor(hi)) + 1)]
        return all(
            self.accepts(v) == other.accepts(v)
            and (not self.accepts(v) or _close(self.display(v), other.display(v)))
            for v in values
        )


def candidates(p: ParamSpec):
    if not p.usable:
        return []
    kinds = ["value", "clicks"] + (["tenths"] if p.name.startswith("CAMBER") else [])
    out = []
    for kind in kinds:
        enc = Encoding(kind, p.min, p.max, p.step)
        if any(enc.same_as(o) for o in out):
            continue
        out.append(enc)
    return out


@dataclass
class ParamState:
    spec: ParamSpec | None
    name: str
    status: str  # abgeleitet | bestaetigt | mehrdeutig | widerspruechlich | unbestaetigt | nicht_unterstuetzt
    reason: str
    encoding: Encoding | None = None
    options: list[Encoding] = field(default_factory=list)
    observed: int = 0

    @property
    def exportable(self):
        return self.status in ("abgeleitet", "bestaetigt") and self.encoding is not None


def infer(spec: dict[str, ParamSpec] | None, observations: dict[str, set[int]], confirmed: dict[str, str], names=()):
    """Encoding state for every parameter of the spec and every VALUE section
    found in setups (names). Deterministic; never guesses between options."""
    states = {}
    for name in sorted(set(spec or {}) | set(names) | set(observations)):
        if is_internal(name):
            continue
        p = (spec or {}).get(name)
        seen = observations.get(name, set())
        if p is None:
            reason = (
                "Keine Fahrzeugdaten (setup.ini) - Grenzen und Kodierung unbekannt"
                if spec is None
                else "Nicht in der setup.ini des Fahrzeugs beschrieben"
            )
            states[name] = ParamState(None, name, "unbestaetigt" if spec is None else "nicht_unterstuetzt", reason, observed=len(seen))
            continue
        if not p.usable:
            states[name] = ParamState(p, name, "nicht_unterstuetzt", "; ".join(p.problems), observed=len(seen))
            continue
        options = candidates(p)
        fitting = [e for e in options if all(e.accepts(v) for v in seen)]
        user = confirmed.get(name)
        if not seen:
            state = ParamState(p, name, "unbestaetigt", "Kein gespeicherter Wert für diesen Parameter - Kodierung nicht ableitbar")
        elif not fitting:
            state = ParamState(p, name, "widerspruechlich", "Gespeicherte Werte passen zu keiner bekannten Kodierung")
        elif user and any(e.kind == user for e in fitting):
            enc = next(e for e in fitting if e.kind == user)
            state = ParamState(p, name, "bestaetigt", "Kodierung von dir anhand der Anzeige im Spiel bestätigt", enc)
        elif user:
            state = ParamState(p, name, "widerspruechlich", "Deine Bestätigung passt nicht zu den gespeicherten Werten")
        elif len(fitting) == 1:
            state = ParamState(p, name, "abgeleitet", f"Aus {len(seen)} gespeicherten Wert(en) eindeutig abgeleitet", fitting[0])
        else:
            state = ParamState(p, name, "mehrdeutig", "Gespeicherte Werte passen zu mehreren Kodierungen - bitte mit der Anzeige im Spiel bestätigen")
        state.options = fitting
        state.observed = len(seen)
        states[name] = state
    return states


# ------------------------------------------------------- AC folders


def known_documents():
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

            # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
            guid = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0:
                path = Path(out.value)
                ctypes.windll.ole32.CoTaskMemFree(out)
                return path
        except (OSError, AttributeError):
            pass
    return Path.home() / "Documents"


def steam_libraries():
    roots = []
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                roots.append(Path(winreg.QueryValueEx(key, "SteamPath")[0]))
        except OSError:
            pass
        roots.append(Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam")
    libraries = []
    for root in roots:
        libraries.append(root)
        vdf = root / "steamapps" / "libraryfolders.vdf"
        try:
            for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(errors="replace")):
                libraries.append(Path(m.group(1).replace("\\\\", "\\")))
        except OSError:
            pass
    return list(dict.fromkeys(libraries))


def find_ac_dir(configured=""):
    """AC installation folder (read only). Env override first (tests)."""
    override = os.environ.get("AC_AGENT_AC_DIR") or configured
    if override:
        path = Path(override)
        return path if (path / "content" / "cars").is_dir() else None
    for lib in steam_libraries():
        path = lib / "steamapps" / "common" / "assettocorsa"
        if (path / "content" / "cars").is_dir():
            return path
    return None


def find_setups_dir(configured=""):
    override = os.environ.get("AC_AGENT_SETUPS_DIR") or configured
    if override:
        return Path(override)
    return known_documents() / "Assetto Corsa" / "setups"


def check_folder_name(name, what):
    if not name or not FOLDER_PATTERN.fullmatch(name) or name != name.strip(" .") or name.startswith("."):
        raise SetupError(f"Ungültiger {what}-Name")
    return name


def car_spec_file(ac_dir, car):
    check_folder_name(car, "Fahrzeug")
    if not ac_dir:
        return None
    path = Path(ac_dir) / "content" / "cars" / car / "data" / "setup.ini"
    return path if path.is_file() else None


def car_packed(ac_dir, car):
    return bool(ac_dir) and (Path(ac_dir) / "content" / "cars" / car / "data.acd").is_file()


def list_setups(setups_dir, car, track):
    """Saved setups of the car for this track and the generic folder."""
    check_folder_name(car, "Fahrzeug")
    out = []
    base = Path(setups_dir) / car
    for folder in dict.fromkeys([check_folder_name(track, "Strecken"), "generic"]):
        directory = base / folder
        try:
            files = sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() == ".ini")
        except OSError:
            continue
        for p in files:
            stat = p.stat()
            out.append({"folder": folder, "name": p.name, "modified": stat.st_mtime, "bytes": stat.st_size})
    return out


def read_saved_setup(setups_dir, car, track, folder, name):
    """Content of a listed setup; only names from the listing are accepted."""
    if folder not in (track, "generic"):
        raise SetupError("Unbekannter Setup-Ordner")
    match = next(
        (s for s in list_setups(setups_dir, car, track) if s["folder"] == folder and s["name"] == name),
        None,
    )
    if not match:
        raise SetupError("Setup-Datei nicht gefunden")
    path = Path(setups_dir) / car / folder / name
    return path, path.read_bytes()


def observed_values(setups_dir, car):
    """All integer VALUEs per parameter from every saved setup of the car."""
    out: dict[str, set[int]] = {}
    files = 0
    base = Path(setups_dir) / car
    try:
        paths = [p for p in base.rglob("*.ini") if p.is_file()][:400]
    except OSError:
        paths = []
    for path in paths:
        try:
            doc = IniDoc(path.read_bytes())
        except (OSError, SetupError):
            continue
        files += 1
        add_observations(out, doc)
    return out, files


def add_observations(out, doc: IniDoc):
    for section, raw in doc.values().items():
        value = parse_int(raw)
        if value is not None:
            out.setdefault(section, set()).add(value)


def safe_file_name(name):
    name = (name or "").strip()
    if name.lower().endswith(".ini"):
        name = name[:-4].strip()
    if not NAME_PATTERN.fullmatch(name) or name.endswith((".", " ")) or name.upper() in (
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))
    ):
        raise SetupError("Dateiname: 1-60 Zeichen aus Buchstaben, Ziffern, Leerzeichen, - _ . ( )")
    return name + ".ini"


def clean_name(name, fallback="setup"):
    """Lenient variant for names we did not choose (uploads, defaults)."""
    stem = Path(str(name or "")).name
    if stem.lower().endswith(".ini"):
        stem = stem[:-4]
    stem = re.sub(r"[^A-Za-z0-9 _\-().]", "_", stem).strip(" .")[:60] or fallback
    try:
        return safe_file_name(stem)
    except SetupError:
        return fallback + ".ini"


def save_new_setup(setups_dir, car, folder, name, data: bytes, protected=()):
    """Write a NEW file into the AC setups folder; never overwrites anything."""
    check_folder_name(car, "Fahrzeug")
    check_folder_name(folder, "Ordner")
    directory = Path(setups_dir) / car / folder
    target = directory / safe_file_name(name)
    if any(Path(p).resolve() == target.resolve() for p in protected if p):
        raise SetupError("Das Ausgangssetup wird nie überschrieben - bitte anderen Namen wählen")
    directory.mkdir(parents=True, exist_ok=True)
    try:
        with open(target, "xb") as f:  # exclusive: fails if the file exists
            f.write(data)
    except FileExistsError:
        raise SetupError(f"{target.name} existiert bereits - bitte anderen Namen wählen")
    return target


# ------------------------------------------------------ setup snapshot


def describe(states: dict[str, ParamState], doc: IniDoc | None):
    """Parameter list for the UI and the analysis (values in game units where known)."""
    values = doc.values() if doc else {}
    out = []
    for name, st in states.items():
        raw = values.get(name)
        stored = parse_int(raw)
        item = {
            "name": name,
            "label": st.spec.label if st.spec else name,
            "tab": st.spec.tab if st.spec else "",
            "unit": st.spec.unit if st.spec else "",
            "status": st.status,
            "reason": st.reason,
            "exportable": st.exportable and stored is not None,
            "in_setup": raw is not None,
            "raw": raw,
            "stored": stored,
            "encoding": st.encoding.kind if st.encoding else None,
            "options": [
                {
                    "kind": e.kind,
                    "label": ENCODING_LABELS[e.kind],
                    "display": e.display(stored) if stored is not None and e.accepts(stored) else None,
                }
                for e in st.options
            ],
            "observed": st.observed,
        }
        if st.spec and st.spec.usable:
            item.update({"min": st.spec.min, "max": st.spec.max, "spec_step": st.spec.step})
        if st.encoding:
            item["step"] = st.encoding.grid()
            item["value"] = st.encoding.display(stored) if stored is not None and st.encoding.accepts(stored) else None
            if stored is not None and not st.encoding.accepts(stored):
                item["exportable"] = False
                item["reason"] = "Wert im Ausgangssetup liegt außerhalb der Grenzen"
            if st.encoding.kind == "clicks" and stored is not None:
                item["click"] = stored
        out.append(item)
    return out


def check_change(state: ParamState, current_raw, new_display):
    """Deterministic check of one proposed change. Returns (stored, error)."""
    if state is None:
        return None, "Parameter existiert für dieses Fahrzeug nicht"
    if not isinstance(new_display, (int, float)) or isinstance(new_display, bool) or not math.isfinite(new_display):
        return None, "Neuer Wert ist keine Zahl"
    current = parse_int(current_raw)
    if current is None:
        return None, "Parameter fehlt im Ausgangssetup"
    if not state.exportable:
        return None, "Grenzen/Kodierung unbestätigt - nicht exportierbar"
    enc = state.encoding
    if not enc.accepts(current):
        return None, "Ausgangswert liegt außerhalb der Grenzen"
    stored = enc.stored(float(new_display))
    if stored is None:
        spec = state.spec
        return None, f"Wert {new_display} ist nicht einstellbar (Bereich {spec.min:g}…{spec.max:g}, Schritt {enc.grid():g})"
    if stored == current:
        return None, "Keine Änderung gegenüber dem Ausgangssetup"
    return stored, None
