"""Setup recommendations: exchangeable providers (Claude via the official
Anthropic SDK, or local rules), backend-only key storage and the
deterministic validation every proposal must pass before it is shown.

The model never decides what is valid: parameter existence, limits, steps,
VALUE encoding, the three-change limit and the ban on lap-time promises are
enforced here, outside the model, for AI and rule-based results alike.
"""
from __future__ import annotations

import base64
import json
import logging
import math
import os
import re
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import ac_setup
from .setup_kpis import GOALS, ISSUES, PHASES

log = logging.getLogger(__name__)

DISCLOSURE_VERSION = 1
MAX_CHANGES = 3
CONFIDENCE = ("niedrig", "mittel", "hoch")
DEFAULT_MODEL = "claude-opus-5-5"
PROVIDERS = {
    "anthropic": {
        "label": "Anthropic (Claude)",
        "endpoint": "api.anthropic.com",
        "privacy": "https://www.anthropic.com/legal/privacy",
    }
}


class ProviderError(Exception):
    """AI call failed; code is stable for the UI, message is user-facing."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------ key storage


class SecretStore:
    """API key on disk, never in settings, responses or logs. On Windows it
    is encrypted with DPAPI for the current Windows user."""

    ENTROPY = b"StintPulse-ai-key-v1"

    def __init__(self, data_root):
        self.path = Path(data_root) / "secrets" / "anthropic.key"

    def status(self):
        stored = self.path.is_file()
        readable = stored and self._read() is not None
        env = bool(os.environ.get("ANTHROPIC_API_KEY"))
        return {
            "set": readable or env,
            "source": "stored" if readable else "env" if env else None,
            "unreadable": stored and not readable,
            "protection": "dpapi" if sys.platform == "win32" else "file",
        }

    def get(self):
        return self._read() or os.environ.get("ANTHROPIC_API_KEY") or None

    def set(self, key: str):
        key = (key or "").strip()
        if not re.fullmatch(r"[\x21-\x7e]{20,300}", key):
            raise ValueError("Ungültiger API-Schlüssel (20-300 sichtbare ASCII-Zeichen)")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = self._protect(key.encode())
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(data)
        if sys.platform != "win32":
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def clear(self):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _read(self):
        try:
            data = self.path.read_bytes()
        except OSError:
            return None
        try:
            return self._unprotect(data).decode()
        except (OSError, ValueError, UnicodeDecodeError):
            return None  # e.g. portable folder moved to another Windows user

    def _protect(self, raw):
        if sys.platform != "win32":
            return base64.b64encode(raw)
        return _dpapi(raw, self.ENTROPY, protect=True)

    def _unprotect(self, data):
        if sys.platform != "win32":
            return base64.b64decode(data, validate=True)
        return _dpapi(data, self.ENTROPY, protect=False)


def _dpapi(data, entropy, protect):
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b):
        buf = ctypes.create_string_buffer(b, len(b))
        return Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    src, _a = blob(data)
    ent, _b = blob(entropy)
    out = Blob()
    crypt = ctypes.windll.crypt32
    fn = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    # 0x1 = CRYPTPROTECT_UI_FORBIDDEN; the description is only set when encrypting.
    ok = fn(ctypes.byref(src), "StintPulse" if protect else None, ctypes.byref(ent), None, None, 0x1, ctypes.byref(out))
    if not ok:
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


# ------------------------------------------------------------- providers

SYSTEM_PROMPT = """Du bist Renningenieur für Fahrzeugsetups im Simulator Assetto Corsa (Original, nicht ACC).
Du bekommst: Ziel, Rückmeldung des Fahrers, zusammengefasste Telemetrie-Kennwerte repräsentativer Runden
(Ausreißer, Out-/Inlaps und Unfälle sind bereits entfernt), einen Abgleich Messung/Rückmeldung und die
Setup-Parameter des Fahrzeugs mit aktuellem Wert, Grenzen und Schrittweite.

Regeln:
- Schlage höchstens 3 Änderungen vor, nach Priorität geordnet (priority 1 = zuerst testen). Weniger oder
  keine Änderung ist richtig, wenn die Daten nicht reichen; sage das dann in summary und uncertainties.
- Verwende ausschließlich Parameter aus der Liste, mit exakt dem angegebenen "name". Erfinde keine Parameter.
- Für Parameter mit "exportable": true muss new_value innerhalb von min..max liegen und ein Vielfaches der
  Schrittweite "step" ab "min" sein, in der angegebenen Einheit (Anzeige-Wert, nicht der Dateiwert).
  Parameter mit "exportable": false haben unbestätigte Grenzen; nur vorschlagen, wenn klar besser als
  alles andere, new_value dann als Dateiwert ("raw").
- Trenne in jeder Änderung: observation (was gemessen/berichtet wurde), possible_cause (mögliche Ursache),
  test (konkreter Test: was ändern, wie viele Runden, worauf achten).
- Telemetrie-Kennwerte sind Indizien. Behaupte Unter- oder Übersteuern nie allein aus Lenkwinkel und
  Geschwindigkeit. Verknüpfe Messung und Rückmeldung und benenne Unsicherheiten.
- Keine Zeitgewinne in Sekunden/Zehnteln, keine Garantie, dass eine Änderung schneller macht.
- Nenne Nachteile (tradeoffs) jeder Änderung und eine Konfidenz: niedrig, mittel oder hoch.
- Wenn eher die Fahrtechnik als das Setup die Ursache ist, gib einen Hinweis in driving_tips.
- Antworte auf Deutsch, knapp und konkret."""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "observations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "basis": {"type": "string", "enum": ["messung", "rueckmeldung", "beides"]},
                    "certainty": {"type": "string", "enum": list(CONFIDENCE)},
                },
                "required": ["text", "basis", "certainty"],
                "additionalProperties": False,
            },
        },
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "parameter": {"type": "string"},
                    "new_value": {"type": "number"},
                    "priority": {"type": "integer"},
                    "observation": {"type": "string"},
                    "possible_cause": {"type": "string"},
                    "test": {"type": "string"},
                    "reasoning": {"type": "string"},
                    "expected_effect": {"type": "string"},
                    "tradeoffs": {"type": "string"},
                    "confidence": {"type": "string", "enum": list(CONFIDENCE)},
                },
                "required": [
                    "parameter", "new_value", "priority", "observation", "possible_cause", "test",
                    "reasoning", "expected_effect", "tradeoffs", "confidence",
                ],
                "additionalProperties": False,
            },
        },
        "driving_tips": {"type": "array", "items": {"type": "string"}},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "observations", "changes", "driving_tips", "uncertainties"],
    "additionalProperties": False,
}


class AnthropicProvider:
    """Claude via the official SDK, structured JSON output, server-side
    refusal fallback (beta) on the Claude API."""

    id = "anthropic"
    label = PROVIDERS["anthropic"]["label"]

    def __init__(self, api_key, model=DEFAULT_MODEL, client=None, timeout=180.0):
        self.api_key, self.model, self.client, self.timeout = api_key, model, client, timeout

    def complete(self, system, user, schema):
        try:
            import anthropic
        except ImportError:
            raise ProviderError("sdk_missing", "Anthropic-SDK ist nicht installiert")
        if not self.api_key and self.client is None:
            raise ProviderError("no_key", "Kein API-Schlüssel hinterlegt")
        client = self.client or anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout, max_retries=2)
        request = {
            "model": self.model,
            "max_tokens": 16000,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        if self.model.startswith(("claude-opus-5", "claude-fable-5")):
            # A declined request is re-run on Anthropic's recommended fallback model.
            request.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        try:
            response = client.beta.messages.create(**request)
        except anthropic.AuthenticationError:
            raise ProviderError("auth", "API-Schlüssel wurde abgelehnt (ungültig oder widerrufen)")
        except anthropic.PermissionDeniedError:
            raise ProviderError("permission", "Kein Zugriff auf dieses Modell mit diesem Schlüssel")
        except anthropic.NotFoundError:
            raise ProviderError("not_found", f"Modell '{self.model}' nicht gefunden")
        except anthropic.RateLimitError:
            raise ProviderError("rate_limit", "Anfragelimit erreicht - später erneut versuchen")
        except anthropic.BadRequestError as exc:
            raise ProviderError("bad_request", "Anfrage abgelehnt: " + _api_message(exc))
        except anthropic.APITimeoutError:
            raise ProviderError("timeout", "Zeitüberschreitung bei der KI-Anfrage")
        except anthropic.APIConnectionError:
            raise ProviderError("connection", "Keine Verbindung zum KI-Anbieter (Internet/Firewall?)")
        except anthropic.APIStatusError as exc:
            raise ProviderError("server", f"KI-Anbieter meldet Fehler {exc.status_code}")
        if response.stop_reason == "refusal":
            raise ProviderError("refusal", "Das Modell hat die Anfrage abgelehnt")
        if response.stop_reason == "max_tokens":
            raise ProviderError("max_tokens", "Antwort wurde abgeschnitten (zu lang)")
        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), None)
        if not text:
            raise ProviderError("invalid_response", "Antwort enthielt keinen Text")
        usage = getattr(response, "usage", None)
        return text, {
            "model": getattr(response, "model", self.model),
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
        }


def _api_message(exc):
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        message = (body.get("error") or {}).get("message")
        if isinstance(message, str):
            return message[:200]
    return "ungültige Anfrage"


# --------------------------------------------------------- the request


def parameters_for_model(params):
    """Setup values for the model: names, current values and limits only."""
    out = []
    for p in params:
        if not p["in_setup"]:
            continue
        item = {"name": p["name"], "label": p["label"], "tab": p["tab"], "exportable": p["exportable"]}
        if p["exportable"]:
            item.update(
                value=p.get("value"), unit=p["unit"], min=p.get("min"), max=p.get("max"), step=p.get("step")
            )
        else:
            item.update(raw=p["raw"], note="Grenzen/Kodierung unbestätigt: " + p["reason"])
        out.append(item)
    return out


def build_request(car, track, goal, goal_text, feedback, kpi_result, evidence, params):
    """Exactly what is transmitted: no driver name, no session/lap ids, no raw
    telemetry, no video or audio."""
    unavailable = [a["label"] for a in kpi_result["availability"] if not a["available"]]
    return {
        "car": car,
        "track": track,
        "goal": GOALS[goal] + (f": {goal_text}" if goal_text else ""),
        "driver_feedback": {
            "items": [
                {"issue": ISSUES[i.issue], "phase": PHASES[i.phase], "corners": i.corners, "severity": i.severity}
                for i in feedback.items
            ],
            "text": feedback.text,
        },
        "measurement_vs_feedback": [
            {"feedback": e["label"], "verdict": e["verdict"], "details": e["details"]} for e in evidence
        ],
        "telemetry": {
            "representative_laps": kpi_result["used"],
            "excluded_laps": [
                {"reasons": l["reasons"]} for l in kpi_result["laps"] if not l["used"]
            ],
            "included_laps_with_notes": [
                {"notes": l["notes"]} for l in kpi_result["laps"] if l["used"] and l.get("notes")
            ],
            "warnings": kpi_result["warnings"],
            "notes": kpi_result["notes"],
            "unavailable_channels": unavailable,
            "kpis": kpi_result["kpis"],
            "conditions": {k: v for k, v in kpi_result["conditions"].items() if k != "sessions"},
        },
        "setup_parameters": parameters_for_model(params),
    }


# ----------------------------------------------------------- validation


class _Obs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    basis: Literal["messung", "rueckmeldung", "beides"]
    certainty: Literal["niedrig", "mittel", "hoch"]


class _Change(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parameter: str
    new_value: float
    priority: int
    observation: str
    possible_cause: str
    test: str
    reasoning: str
    expected_effect: str
    tradeoffs: str
    confidence: Literal["niedrig", "mittel", "hoch"]


class _Result(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str
    observations: list[_Obs]
    changes: list[_Change]
    driving_tips: list[str]
    uncertainties: list[str]


TIME_UNIT = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:s\b|sek\w*|sec\w*|ms\b|zehntel\w*|hundertstel\w*|tenths?\b)|\b(?:zehntel|hundertstel|tenths?)\b",
    re.I,
)
GAIN_WORD = re.compile(
    r"schneller|gewinn|faster|gain|quicker|spar|bringt|verbesser|rundenzeit|lap ?time", re.I
)
# Promises only ("garantiert schneller"), not disclaimers ("keine Garantie").
GUARANTEE = re.compile(
    r"\bgarantier\w*|(?<!\bno )\bguarantee[sd]?\b|\bdefinitiv schneller|\bsicher schneller", re.I
)
REMOVED = "[Zeitversprechen entfernt - nicht belegbar]"


def scrub(text, limit=700):
    """Remove sentences that promise a lap-time gain or guarantee speed."""
    text = " ".join((text or "").split())[:limit]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept, removed = [], False
    for s in sentences:
        if (TIME_UNIT.search(s) and GAIN_WORD.search(s)) or GUARANTEE.search(s):
            removed = True
            if not kept or kept[-1] != REMOVED:
                kept.append(REMOVED)
        else:
            kept.append(s)
    return " ".join(kept).strip(), removed


def validate_result(raw, states, params, context):
    """Check a model/rule result deterministically. Returns the display result;
    raises ProviderError('invalid_response') if the structure is unusable."""
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except ValueError:
            raise ProviderError("invalid_response", "Antwort ist kein gültiges JSON")
    else:
        data = raw
    try:
        result = _Result.model_validate(data)
    except ValidationError as exc:
        first = exc.errors(include_input=False)[0]
        where = ".".join(map(str, first["loc"]))
        raise ProviderError("invalid_response", f"Antwort passt nicht zum Schema ({where}: {first['msg']})")

    by_name = {p["name"]: p for p in params}
    notes = []
    accepted, dropped = [], []
    cap = "hoch"
    if context.get("used_laps", 0) < 3:
        cap = "niedrig"
    elif not context.get("has_feedback"):
        cap = "mittel"
    seen = set()
    for change in sorted(result.changes, key=lambda c: c.priority):
        name = change.parameter.strip().upper()
        label = by_name.get(name, {}).get("label", name)
        reason = None
        texts = {}
        for field in ("observation", "possible_cause", "test", "reasoning", "expected_effect", "tradeoffs"):
            texts[field], hit = scrub(getattr(change, field))
            if hit:
                notes.append(f"{label}: Zeitversprechen aus '{field}' entfernt")
        state = states.get(name)
        p = by_name.get(name)
        stored, error = (None, "Parameter existiert für dieses Fahrzeug nicht")
        if name in seen:
            reason = "Parameter doppelt vorgeschlagen"
        elif state is None or p is None:
            reason = "Parameter existiert für dieses Fahrzeug nicht (erfunden?)"
        elif not p["in_setup"]:
            reason = "Parameter fehlt im Ausgangssetup"
        elif any(not texts[f].strip() or texts[f] == REMOVED for f in ("observation", "possible_cause", "reasoning", "expected_effect", "tradeoffs", "test")):
            reason = "Begründung unvollständig"
        else:
            stored, error = ac_setup.check_change(state, p["raw"], change.new_value)
            if error and p["exportable"]:
                reason = error
        if reason is None and len(accepted) >= MAX_CHANGES:
            reason = f"Mehr als {MAX_CHANGES} Änderungen - nur die {MAX_CHANGES} wichtigsten werden gezeigt"
        if reason:
            dropped.append({"parameter": name, "label": label, "new_value": change.new_value, "reason": reason})
            continue
        seen.add(name)
        confidence = change.confidence
        limited = CONFIDENCE.index(confidence) > CONFIDENCE.index(cap)
        if limited:
            confidence = cap
        exportable = stored is not None
        if not exportable:
            raw_value = ac_setup.parse_int(p["raw"])
            new_raw = change.new_value
            if raw_value is not None and math.isfinite(new_raw) and abs(new_raw - raw_value) < 1e-9:
                dropped.append({"parameter": name, "label": label, "new_value": change.new_value, "reason": "Keine Änderung gegenüber dem Ausgangssetup"})
                continue
        enc = state.encoding if exportable else None
        accepted.append(
            {
                "parameter": name,
                "label": label,
                "tab": p["tab"],
                "unit": p["unit"] if exportable else "",
                "old_value": p.get("value") if exportable else ac_setup.parse_int(p["raw"]),
                "new_value": enc.display(stored) if exportable else change.new_value,
                "old_raw": p["raw"],
                "new_raw": str(stored) if exportable else None,
                "ini": f"[{name}] VALUE={p['raw']} → VALUE={stored}" if exportable else f"[{name}] VALUE={p['raw']} (Zielwert unbestätigt)",
                "encoding": enc.kind if enc else None,
                "exportable": exportable,
                "export_block": None if exportable else (error or "Grenzen/Kodierung unbestätigt"),
                "priority": len(accepted) + 1,
                "confidence": confidence,
                "confidence_limited": limited,
                **texts,
            }
        )
    def clean_list(items, limit=8):
        out = []
        for item in items[:limit]:
            text, hit = scrub(item, 400)
            if text and text != REMOVED:
                out.append(text)
            if hit:
                notes.append("Zeitversprechen aus Hinweisen entfernt")
        return out

    summary, hit = scrub(result.summary, 1200)
    if hit:
        notes.append("Zeitversprechen aus der Zusammenfassung entfernt")
    observations = []
    for o in result.observations[:8]:
        text, hit = scrub(o.text, 400)
        if text and text != REMOVED:
            observations.append({"text": text, "basis": o.basis, "certainty": o.certainty})
    if cap != "hoch":
        notes.append(
            "Konfidenz auf 'niedrig' begrenzt: weniger als 3 repräsentative Runden"
            if cap == "niedrig"
            else "Konfidenz auf 'mittel' begrenzt: keine Rückmeldung zum Fahrverhalten"
        )
    return {
        "summary": summary,
        "observations": observations,
        "changes": accepted,
        "dropped": dropped,
        "driving_tips": clean_list(result.driving_tips),
        "uncertainties": clean_list(result.uncertainties),
        "validation_notes": list(dict.fromkeys(notes)),
    }


# ------------------------------------------------------ rule-based fallback

# (issue, phase) -> list of (role, direction, effect, tradeoff)
RULES = {
    ("understeer", "entry"): [
        ("arb_front", -1, "mehr Grip vorne beim Einlenken", "weniger Stütze vorne, mehr Wanken"),
        ("arb_rear", +1, "Heck dreht williger ein", "Heck kann beim Einlenken leichter ausbrechen"),
    ],
    ("understeer", "mid"): [
        ("arb_front", -1, "mehr mechanischer Grip vorne in der Kurvenmitte", "trägere Reaktion bei Richtungswechseln"),
        ("wing_front", +1, "mehr Abtrieb vorne in schnellen Kurven", "mehr Luftwiderstand, Balance verschiebt sich nach vorne"),
        ("arb_rear", +1, "mehr Rotation in der Kurvenmitte", "weniger Traktion am Ausgang"),
    ],
    ("understeer", "exit"): [
        ("arb_front", -1, "mehr Grip vorne beim Herausbeschleunigen", "weniger Stütze vorne"),
        ("diff_power", -1, "Fahrzeug dreht unter Last leichter", "mehr Durchdrehen des kurveninneren Rads möglich"),
    ],
    ("understeer", "highspeed"): [
        ("wing_front", +1, "mehr Abtrieb vorne bei hohem Tempo", "mehr Luftwiderstand, Heck bei hohem Tempo leichter"),
    ],
    ("understeer", "lowspeed"): [
        ("arb_front", -1, "mehr mechanischer Grip vorne in langsamen Kurven", "weniger Stütze vorne"),
    ],
    ("oversteer", "entry"): [
        ("diff_coast", +1, "stabileres Heck beim Bremsen und Einlenken", "Fahrzeug dreht beim Einlenken weniger"),
        ("front_bias", +1, "Bremskraft weiter vorne beruhigt das Heck", "Vorderräder blockieren eher"),
    ],
    ("oversteer", "mid"): [
        ("arb_rear", -1, "mehr mechanischer Grip hinten", "Fahrzeug kann träger einlenken"),
        ("wing_rear", +1, "mehr Abtrieb hinten in schnellen Kurven", "mehr Luftwiderstand, weniger Endgeschwindigkeit"),
    ],
    ("oversteer", "exit"): [
        ("arb_rear", -1, "mehr Traktion beim Herausbeschleunigen", "trägere Rotation in der Kurvenmitte"),
        ("wing_rear", +1, "mehr Abtrieb hinten", "mehr Luftwiderstand"),
    ],
    ("oversteer", "highspeed"): [
        ("wing_rear", +1, "stabileres Heck bei hohem Tempo", "mehr Luftwiderstand, weniger Endgeschwindigkeit"),
    ],
    ("oversteer", "lowspeed"): [
        ("arb_rear", -1, "mehr mechanischer Grip hinten in langsamen Kurven", "trägeres Einlenken"),
    ],
    ("wheelspin", "exit"): [
        ("arb_rear", -1, "mehr Traktion hinten", "trägere Rotation"),
        ("diff_power", +1, "weniger Durchdrehen des kurveninneren Rads", "Fahrzeug schiebt unter Last eher über die Vorderräder"),
    ],
    ("lockup", "front"): [
        ("front_bias", -1, "Vorderräder blockieren seltener", "Heck wird beim Bremsen unruhiger"),
        ("brake_power", -1, "weniger Bremsmoment, seltener Blockieren", "längere Bremswege bei vollem Pedal"),
    ],
    ("lockup", "rear"): [
        ("front_bias", +1, "Hinterräder blockieren seltener", "Vorderräder blockieren eher"),
    ],
    ("unstable_braking", "braking"): [
        ("front_bias", +1, "ruhigeres Heck beim Bremsen", "Vorderräder blockieren eher"),
        ("diff_coast", +1, "stabileres Heck im Schiebebetrieb", "weniger Rotation beim Einlenken"),
    ],
}
ROLE_PATTERNS = {
    "arb_front": re.compile(r"^ARB_(FRONT|F)$"),
    "arb_rear": re.compile(r"^ARB_(REAR|R)$"),
    "front_bias": re.compile(r"^FRONT_BIAS$"),
    "brake_power": re.compile(r"^BRAKE_POWER_MULT$"),
    "diff_power": re.compile(r"^DIFF_POWER$"),
    "diff_coast": re.compile(r"^DIFF_COAST$"),
}


def find_role(role, params):
    usable = [p for p in params if p["exportable"] and p.get("value") is not None and p.get("step")]
    if role.startswith("wing_"):
        # Wing numbering differs per car: only an unambiguous front/rear label counts.
        wings = [(p, p["label"].lower()) for p in usable if p["name"].startswith("WING")]
        if role == "wing_front":
            matches = [p for p, label in wings if "front" in label and "rear" not in label]
        else:
            matches = [p for p, label in wings if "rear" in label]
        return matches[0] if len(matches) == 1 else None
    pattern = ROLE_PATTERNS[role]
    return next((p for p in usable if pattern.match(p["name"])), None)


def sentence(text):
    return text[:1].upper() + text[1:]


def rule_based(params, feedback, evidence_items, kpi_result):
    """Deterministic, conservative suggestions (one step each) from the
    feedback, ordered by severity and measured support. Never AI."""
    events = kpi_result.get("kpis", {}).get("events_per_lap", {})
    by_item = {e["item"]: e for e in evidence_items}
    order = sorted(
        range(len(feedback.items)),
        key=lambda i: (-feedback.items[i].severity, 0 if by_item.get(i, {}).get("verdict") == "stützt" else 1),
    )
    changes, used, tips, uncertainties = [], set(), [], []

    def rule_phase(item):
        phase = item.phase
        if item.issue == "lockup":
            return "rear" if events.get("wheel_lock_rear", 0) > events.get("wheel_lock_front", 0) else "front"
        if item.issue == "wheelspin":
            return "exit"
        if item.issue == "unstable_braking":
            return "braking"
        if item.issue == "oversteer" and phase == "braking":
            return "entry"
        if item.issue in ("understeer", "oversteer") and phase in ("general", "straight", "braking"):
            return "mid"
        return phase

    # A parameter that one complaint wants up and another wants down is left
    # alone: the rules cannot weigh the two against each other.
    wanted: dict[str, set[int]] = {}
    for item in feedback.items:
        for role, direction, *_ in RULES.get((item.issue, rule_phase(item)), []):
            wanted.setdefault(role, set()).add(direction)
    conflicts = {role for role, dirs in wanted.items() if len(dirs) > 1}
    for role in sorted(conflicts):
        p = find_role(role, params)
        if p:
            uncertainties.append(
                f"{p['label']}: deine Rückmeldungen verlangen gegensätzliche Änderungen - bewusst nicht verändert."
            )

    for i in order:
        item = feedback.items[i]
        ev = by_item.get(i, {"verdict": "nicht messbar", "details": []})
        phase = rule_phase(item)
        if item.issue == "lockup":
            tips.append("Blockieren: Bremsdruck nach dem ersten Anbremsen gleichmäßiger abbauen, Bremspunkt prüfen.")
        if item.issue == "wheelspin":
            tips.append("Traktion: Gas am Kurvenausgang progressiver aufziehen, erst bei geöffnetem Lenkrad voll.")
        # One step per complaint: the first rule whose parameter is confirmed.
        for role, direction, effect, tradeoff in RULES.get((item.issue, phase), []):
            if len(changes) >= MAX_CHANGES:
                break
            p = find_role(role, params)
            if role in conflicts or not p or p["name"] in used:
                continue
            new = round(p["value"] + direction * p["step"], 6)
            if p.get("min") is not None and not (p["min"] - 1e-9 <= new <= p["max"] + 1e-9):
                continue
            used.add(p["name"])
            supported = ev["verdict"] == "stützt"
            changes.append(
                {
                    "parameter": p["name"],
                    "new_value": new,
                    "priority": len(changes) + 1,
                    "observation": f"Rückmeldung: {ISSUES[item.issue]} ({PHASES[item.phase]}"
                    + (f", {item.corners}" if item.corners else "")
                    + f"). Messung: {ev['verdict']}"
                    + (f" - {ev['details'][0]}" if ev["details"] else "")
                    + ".",
                    "possible_cause": "Mögliche Ursache laut Standard-Setup-Regel; mit den verfügbaren Daten nicht eindeutig belegt.",
                    "test": f"Nur diese Änderung um einen Schritt ({p['step']:g}{(' ' + p['unit']) if p['unit'] else ''}) testen, 3-5 gleichmäßige Runden fahren und Rückmeldung vergleichen.",
                    "reasoning": f"Regel: {ISSUES[item.issue]} ({PHASES[item.phase]}) → {p['label']} {'erhöhen' if direction > 0 else 'verringern'}.",
                    "expected_effect": sentence(effect) + " (Tendenz, keine Garantie).",
                    "tradeoffs": sentence(tradeoff) + ".",
                    "confidence": "mittel" if supported and kpi_result.get("used", 0) >= 3 else "niedrig",
                }
            )
            break
        if item.issue in ("bottoming", "kerbs", "tyre_temps", "instability", "other"):
            uncertainties.append(
                f"{ISSUES[item.issue]}: dafür gibt es keine sichere Standardregel für alle Fahrzeuge - bitte gezielt testen."
            )
    if not feedback.items:
        uncertainties.append("Ohne Rückmeldung zum Fahrverhalten macht die Regel-Analyse keine Änderungsvorschläge.")
    elif not any(p["exportable"] for p in params):
        uncertainties.append(
            "Für dieses Fahrzeug ist kein Parameter mit Grenzen und Kodierung bestätigt (z. B. gepackte Fahrzeugdaten) - "
            "die Regel-Analyse schlägt deshalb keine Werte vor. Mit der setup.ini des Fahrzeugs werden Empfehlungen und Export möglich."
        )
    elif not changes:
        uncertainties.append("Für die Rückmeldung wurde kein passender, bestätigter Parameter gefunden.")
    return {
        "summary": "Regelbasierte Ersatzanalyse (keine KI): konservative Einzelschritte aus deiner Rückmeldung, abgeglichen mit den Messwerten.",
        "observations": [
            {"text": f"{e['label']}: Messung {e['verdict']}" + (f" ({'; '.join(e['details'])})" if e["details"] else ""), "basis": "beides", "certainty": "niedrig"}
            for e in evidence_items
        ],
        "changes": changes,
        "driving_tips": list(dict.fromkeys(tips)),
        "uncertainties": uncertainties,
    }
