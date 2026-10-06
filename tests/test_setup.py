"""Setup assistant: INI handling, limits/encodings, validation of AI answers
(controlled test responses only - no real AI call), export, persistence."""
import base64
import hashlib
import json
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from ac_agent import ac_setup, setup_ai, setup_kpis
from ac_agent.app import create_app
from ac_agent.database import Database
from ac_agent.models import Lap, SessionMeta, Settings

FIXTURES = Path(__file__).parent / "fixtures"
PC, PHONE = ("127.0.0.1", 50000), ("192.168.178.49", 50000)
CAR, TRACK = "Prototype R", "Engineering Circuit"

# Synthetic car data (written for these tests, modelled on the AC format);
# also used by the browser test (scripts/browser_tests.py). base.ini uses CRLF.
SPEC = (FIXTURES / "setup" / "setup.ini").read_text()
BASE = (FIXTURES / "setup" / "base.ini").read_bytes()


def install_car(root, spec=True):
    car_dir = root / "install" / "content" / "cars" / CAR
    (car_dir / "data").mkdir(parents=True, exist_ok=True)
    if spec:
        (car_dir / "data" / "setup.ini").write_bytes(SPEC.encode())
    else:
        (car_dir / "data.acd").write_bytes(b"packed")
    folder = root / "setups" / CAR / TRACK
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "base.ini").write_bytes(BASE)
    return folder / "base.ini"


def states_for(spec_text=SPEC, base=BASE, confirmed=None):
    spec = ac_setup.parse_spec(spec_text.encode()) if spec_text else None
    doc = ac_setup.IniDoc(base)
    obs = {}
    ac_setup.add_observations(obs, doc)
    states = ac_setup.infer(spec, obs, confirmed or {}, names=doc.values())
    return states, ac_setup.describe(states, doc), doc


# ------------------------------------------------------------------ INI


def test_ini_roundtrip_is_byte_identical():
    assert ac_setup.IniDoc(BASE).with_values({}) == BASE
    bom_lf = b"\xef\xbb\xbf[WING_1]\nVALUE=5\n; note\n[X]\nVALUE=1"
    assert ac_setup.IniDoc(bom_lf).with_values({}) == bom_lf
    latin = "[ABOUT]\nDESCRIPTION=Übersteuern\n[WING_1]\nVALUE=5\n".encode("latin-1")
    doc = ac_setup.IniDoc(latin)
    assert doc.with_values({"WING_1": 6}) == latin.replace(b"VALUE=5", b"VALUE=6")
    with pytest.raises(ac_setup.SetupError):
        ac_setup.IniDoc(b"\x00\x01binary")


def test_export_keeps_unknown_entries_and_changes_only_selected_values():
    doc = ac_setup.IniDoc(BASE)
    out = doc.with_values({"DIFF_POWER": 45, "ARB_FRONT": 9})
    before, after = BASE.split(b"\r\n"), out.split(b"\r\n")
    assert len(before) == len(after)
    diff = [(a, b) for a, b in zip(before, after) if a != b]
    assert diff == [(b"VALUE=8", b"VALUE=9"), (b"VALUE=40 ; locking", b"VALUE=45 ; locking")]
    new = ac_setup.IniDoc(out)
    assert new.get("CUSTOM_SCRIPT_ITEM_42", "VALUE") == "1234"
    assert new.get("__EXT_PATCH", "VERSION") == "0.2.0"
    assert new.get("ABOUT", "AUTHOR") == "Tester"
    with pytest.raises(ac_setup.SetupError):
        doc.with_values({"NOT_THERE": 1})
    dup = ac_setup.IniDoc(b"[WING_1]\nVALUE=1\n[WING_1]\nVALUE=2\n")
    with pytest.raises(ac_setup.SetupError, match="mehrfach"):
        dup.with_values({"WING_1": 3})


# --------------------------------------------------- limits and encodings


def test_encoding_inference_from_saved_values():
    states, params, _ = states_for()
    status = {n: s.status for n, s in states.items()}
    kinds = {n: s.encoding.kind for n, s in states.items() if s.encoding}
    assert kinds["ARB_FRONT"] == "clicks"  # VALUE 8 can only be a click index
    assert kinds["CAMBER_LF"] == "tenths"  # -25 = -2.5 degrees
    assert kinds["TOE_OUT_LF"] == "clicks"  # despite SHOW_CLICKS=0
    assert kinds["PRESSURE_LF"] == "value" and kinds["FRONT_BIAS"] == "value"
    assert status["WING_1"] == "abgeleitet"  # value and clicks are identical (MIN 0, STEP 1)
    assert status["FUEL"] == "mehrdeutig" and status["WING_2"] == "mehrdeutig"
    assert status["MYSTERY_PARAM"] == "nicht_unterstuetzt"
    assert status["GEAR_2"] == "nicht_unterstuetzt"
    assert "CUSTOM_SCRIPT_ITEM_42" not in states and "CAR" not in states
    by = {p["name"]: p for p in params}
    assert by["ARB_FRONT"]["value"] == 40000 and by["ARB_FRONT"]["step"] == 2500
    assert by["CAMBER_LF"]["value"] == -2.5 and by["CAMBER_LF"]["unit"] == "°"
    assert by["FUEL"]["exportable"] is False
    assert {o["kind"]: o["display"] for o in by["FUEL"]["options"]} == {"value": 30, "clicks": 31}

    # The user confirms what the game shows -> exportable; a contradiction is not.
    states, params, _ = states_for(confirmed={"FUEL": "clicks", "PRESSURE_LF": "clicks"})
    assert states["FUEL"].status == "bestaetigt" and states["FUEL"].exportable
    assert states["PRESSURE_LF"].status == "widerspruechlich"

    # No car data (packed data.acd): nothing is exportable.
    states, params, _ = states_for(spec_text=None)
    assert {s.status for s in states.values()} == {"unbestaetigt"}
    assert not any(p["exportable"] for p in params)


def test_change_check_limits_steps_and_encoding():
    states, params, _ = states_for(confirmed={"FUEL": "value"})
    raw = {p["name"]: p["raw"] for p in params}

    def check(name, value):
        return ac_setup.check_change(states[name], raw[name], value)

    assert check("ARB_FRONT", 42500) == (9, None)
    assert check("ARB_FRONT", 41000)[1].startswith("Wert 41000")  # off the 2500 grid
    assert check("ARB_FRONT", 62500)[1]  # above MAX
    assert check("ARB_FRONT", 40000)[1] == "Keine Änderung gegenüber dem Ausgangssetup"
    assert check("CAMBER_LF", -2.4) == (-24, None)
    assert check("CAMBER_LF", -2.45)[1]  # VALUE must be an integer
    assert check("CAMBER_LF", -5.1)[1]
    assert check("PRESSURE_LF", 26.5)[1]
    assert check("FUEL", 31) == (31, None)
    assert check("WING_2", 8)[1] == "Grenzen/Kodierung unbestätigt - nicht exportierbar"
    assert check("ARB_FRONT", float("nan"))[1] == "Neuer Wert ist keine Zahl"
    assert ac_setup.check_change(None, "1", 2)[1]


def test_spec_requires_min_max_step():
    with pytest.raises(ac_setup.SetupError):
        ac_setup.parse_spec(b"[WING_1]\nNAME=x\n")
    spec = ac_setup.parse_spec(b"[A]\nMIN=0\nMAX=10\nSTEP=1\n[B]\nMIN=5\nMAX=1\nSTEP=1\n[C]\nMIN=0\nMAX=1\nSTEP=0\n")
    assert spec["A"].usable and not spec["B"].usable and not spec["C"].usable


# ----------------------------------------- validation of model answers


def change(param, value, **kw):
    return {
        "parameter": param, "new_value": value, "priority": kw.pop("priority", 1),
        "observation": "Fahrer meldet Untersteuern im Kurveneingang.",
        "possible_cause": "Vorderachse zu steif.", "test": "Eine Stufe weicher, 3 Runden.",
        "reasoning": "Weichere Vorderachse erhöht mechanischen Grip.",
        "expected_effect": "Mehr Grip vorne.", "tradeoffs": "Mehr Wanken.",
        "confidence": "hoch", **kw,
    }


def answer(*changes, **kw):
    return {
        "summary": kw.get("summary", "Test."), "observations": [], "changes": list(changes),
        "driving_tips": kw.get("tips", []), "uncertainties": [],
    }


def test_faulty_and_overreaching_answers_are_caught_deterministically():
    states, params, _ = states_for()
    ctx = {"used_laps": 5, "has_feedback": True}
    with pytest.raises(setup_ai.ProviderError) as e:
        setup_ai.validate_result("not json {", states, params, ctx)
    assert e.value.code == "invalid_response"
    with pytest.raises(setup_ai.ProviderError):
        setup_ai.validate_result(json.dumps({"summary": "x"}), states, params, ctx)
    bad = answer(change("ARB_FRONT", 37500))
    bad["changes"][0]["extra"] = 1
    with pytest.raises(setup_ai.ProviderError):
        setup_ai.validate_result(json.dumps(bad), states, params, ctx)

    raw = answer(
        change("ARB_FRONT", 37500, priority=1),
        change("ARB_FRONT", 35000, priority=2),  # duplicate
        change("FANTASY_WING", 3, priority=3),  # invented
        change("FRONT_BIAS", 75, priority=4),  # outside MAX
        change("DIFF_POWER", 42, priority=5),  # off step
        change("ARB_REAR", 32500, priority=6, expected_effect="Bringt 0,3 s pro Runde."),
        change("CAMBER_LF", -2.8, priority=7),
        change("WING_2", 8, priority=8),  # ambiguous -> kept but not exportable
        change("PRESSURE_LF", 25, priority=9),  # fourth valid one -> limit
        summary="Garantiert schneller. Mehr Grip vorne.",
    )
    result = setup_ai.validate_result(json.dumps(raw), states, params, ctx)
    names = [c["parameter"] for c in result["changes"]]
    assert names == ["ARB_FRONT", "CAMBER_LF", "WING_2"]
    reasons = {d["parameter"]: d["reason"] for d in result["dropped"] if d["parameter"] != "ARB_FRONT"}
    assert "doppelt" in next(d["reason"] for d in result["dropped"] if d["parameter"] == "ARB_FRONT")
    assert "erfunden" in reasons["FANTASY_WING"]
    assert "nicht einstellbar" in reasons["FRONT_BIAS"]
    assert "nicht einstellbar" in reasons["DIFF_POWER"]
    assert "unvollständig" in reasons["ARB_REAR"]  # only a time promise left
    assert "Mehr als 3" in reasons["PRESSURE_LF"]
    arb = result["changes"][0]
    assert arb["old_value"] == 40000 and arb["new_value"] == 37500 and arb["new_raw"] == "7"
    assert arb["ini"] == "[ARB_FRONT] VALUE=8 → VALUE=7" and arb["exportable"]
    assert result["changes"][1]["new_raw"] == "-28"
    assert result["changes"][2]["exportable"] is False and result["changes"][2]["export_block"]
    assert "Garantiert" not in result["summary"] and "Mehr Grip vorne." in result["summary"]
    assert len(result["changes"]) <= setup_ai.MAX_CHANGES

    few = setup_ai.validate_result(json.dumps(answer(change("ARB_FRONT", 37500))), states, params, {"used_laps": 2, "has_feedback": True})
    assert few["changes"][0]["confidence"] == "niedrig" and few["changes"][0]["confidence_limited"]


def test_rule_based_fallback_avoids_contradictions_and_unconfirmed_parameters():
    states, params, _ = states_for()
    fb = setup_kpis.Feedback(items=[{"issue": "understeer", "phase": "entry", "severity": 3}, {"issue": "oversteer", "phase": "exit"}])
    kpi = {"used": 5, "kpis": {"events_per_lap": {}}}
    raw = setup_ai.rule_based(params, fb, [], kpi)
    # ARB_REAR is wanted stiffer (entry) and softer (exit): left alone. The rear
    # wing is ambiguous (not confirmed): never proposed. One step per complaint.
    assert [c["parameter"] for c in raw["changes"]] == ["ARB_FRONT"]
    assert any("gegensätzliche" in u for u in raw["uncertainties"])
    result = setup_ai.validate_result(raw, states, params, {"used_laps": 5, "has_feedback": True})
    assert result["changes"][0]["new_value"] == 37500 and result["changes"][0]["expected_effect"].startswith("Mehr Grip")
    _, packed, _ = states_for(spec_text=None)
    raw = setup_ai.rule_based(packed, fb, [], kpi)
    assert raw["changes"] == [] and "gepackte Fahrzeugdaten" in raw["uncertainties"][-1]
    assert setup_ai.rule_based(params, setup_kpis.Feedback(), [], kpi)["changes"] == []


# ------------------------------------------------- provider (fake client)


class FakeMessages:
    def __init__(self, outcome):
        self.outcome, self.calls = outcome, []

    def create(self, **request):
        self.calls.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FakeClient:
    def __init__(self, outcome):
        self.beta = type("Beta", (), {})()
        self.beta.messages = FakeMessages(outcome)


def response(text, stop="end_turn"):
    block = type("Block", (), {"type": "text", "text": text})()
    usage = type("Usage", (), {"input_tokens": 10, "output_tokens": 20})()
    return type("Msg", (), {"content": [block] if text is not None else [], "stop_reason": stop, "model": "claude-opus-5-5", "usage": usage})()


def test_anthropic_provider_request_and_error_mapping():
    import anthropic

    client = FakeClient(response('{"ok": 1}'))
    provider = setup_ai.AnthropicProvider("sk-test", "claude-opus-5-5", client=client)
    text, meta = provider.complete("system", "user", setup_ai.RESPONSE_SCHEMA)
    assert text == '{"ok": 1}' and meta["model"] == "claude-opus-5-5"
    call = client.beta.messages.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in call and call["messages"][0]["content"] == "user"

    other = FakeClient(response("{}"))
    setup_ai.AnthropicProvider("k", "claude-sonnet-5-5", client=other).complete("s", "u", {})
    assert "fallbacks" not in other.beta.messages.calls[0]

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    cases = [
        (anthropic.AuthenticationError("bad", response=httpx2.Response(401, request=request), body=None), "auth"),
        (anthropic.RateLimitError("slow", response=httpx2.Response(429, request=request), body=None), "rate_limit"),
        (anthropic.APIConnectionError(request=request), "connection"),
        (anthropic.APITimeoutError(request=request), "timeout"),
        (anthropic.InternalServerError("x", response=httpx2.Response(500, request=request), body=None), "server"),
        (response(None, stop="refusal"), "refusal"),
        (response('{"a"', stop="max_tokens"), "max_tokens"),
        (response(None), "invalid_response"),
    ]
    for outcome, code in cases:
        with pytest.raises(setup_ai.ProviderError) as e:
            setup_ai.AnthropicProvider("k", client=FakeClient(outcome)).complete("s", "u", {})
        assert e.value.code == code, code
    with pytest.raises(setup_ai.ProviderError) as e:
        setup_ai.AnthropicProvider(None).complete("s", "u", {})
    assert e.value.code == "no_key"


# ------------------------------------------------------------ telemetry


def test_missing_telemetry_is_reported_not_invented(recorded):
    meta, laps = recorded
    stripped = []
    for lap in laps:
        copy = lap.model_copy(deep=True)
        for s in copy.samples:
            for k in list(s.channels):
                if k.startswith(("slip_raw", "inner", "outer", "middle", "ride")):
                    s.channels[k] = None
        stripped.append(copy)
    result = setup_kpis.analyse(stripped, {laps[0].session_id: meta.model_dump()}, meta.track_length, Settings())
    available = {a["key"]: a["available"] for a in result["availability"]}
    assert available["wheel_slip"] is False and available["tyre_temp"] is False and available["speed"]
    assert all(v["slip_balance"] is None for v in result["kpis"]["phases"].values())
    fb = setup_kpis.Feedback(items=[{"issue": "understeer", "phase": "mid"}, {"issue": "kerbs", "phase": "general"}])
    ev = setup_kpis.evidence(fb, result)
    assert ev[0]["verdict"] == "nicht messbar" and ev[1]["verdict"] == "nicht messbar"
    assert any("Demo" in w for w in result["warnings"])
    with pytest.raises(ValueError):
        setup_kpis.analyse([], {}, meta.track_length, Settings())


def test_outlaps_incomplete_and_slow_laps_are_excluded_with_reason(recorded):
    meta, laps = recorded
    base = laps[1]
    variants = []
    for i in range(5):
        lap = base.model_copy(deep=True)
        lap.id, lap.number = f"lap-{i}", i + 1
        variants.append(lap)
    variants[0].samples[0].in_pit = True
    variants[1].complete = False
    variants[2].duration_ms = int(base.duration_ms * 1.2)
    result = setup_kpis.analyse(variants, {base.session_id: meta.model_dump()}, meta.track_length, Settings())
    reasons = {l["id"]: l["reasons"] for l in result["laps"]}
    assert "Boxengasse" in reasons["lap-0"][0]
    assert "unvollständig" in reasons["lap-1"]
    assert any("Ausreißer" in r for r in reasons["lap-2"])
    assert result["used"] == 2 and result["excluded"] == 3
    assert any("Nur 2" in w for w in result["warnings"])

    # Track-limit laps can be included on request, but stay visibly marked.
    variants[3].valid, variants[3].reasons = False, ["off_track_inferred"]
    strict = setup_kpis.analyse(variants, {base.session_id: meta.model_dump()}, meta.track_length, Settings())
    assert next(l for l in strict["laps"] if l["id"] == "lap-3")["used"] is False
    loose = setup_kpis.analyse(variants, {base.session_id: meta.model_dump()}, meta.track_length, Settings(), include_invalid=True)
    row = next(l for l in loose["laps"] if l["id"] == "lap-3")
    assert row["used"] and "auf Wunsch einbezogen" in row["notes"]
    assert next(l for l in loose["laps"] if l["id"] == "lap-0")["used"] is False  # pit lap stays out


# ------------------------------------------------------- API end to end


class ScriptedProvider:
    """Controlled test responses - no network, no real AI call."""

    id = "anthropic"
    label = "Test"

    def __init__(self, outcomes):
        self.outcomes, self.requests = list(outcomes), []

    def __call__(self, settings, key):
        self.key = key
        return self

    def complete(self, system, user, schema):
        self.requests.append(json.loads(user))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome, {"model": "test-model"}


def seed(app, recorded):
    meta, laps = recorded
    db = app.state.db
    sid = db.new_session(meta)
    for i, lap in enumerate(laps + [laps[1].model_copy(deep=True)]):
        lap = lap.model_copy(deep=True)
        lap.id, lap.session_id, lap.number = f"{sid[:8]}-{i}", sid, i + 1
        db.save_lap(lap)
    return sid, [l["id"] for l in db.list_laps(sid)]


def b64(data):
    return base64.b64encode(data).decode()


def test_full_setup_flow_with_controlled_ai_answer(tmp_path, recorded, isolated_ac_folders, caplog):
    base_path = install_car(isolated_ac_folders)
    base_hash = hashlib.sha256(base_path.read_bytes()).hexdigest()
    good = json.dumps(answer(change("ARB_FRONT", 37500), change("FUEL", 35, priority=2)))
    provider = ScriptedProvider([good, setup_ai.ProviderError("connection", "Keine Verbindung")])
    app = create_app(tmp_path / "data", source="demo", start_engine=False, setup_provider=provider)
    with TestClient(app, client=PC) as pc, TestClient(app, client=PHONE) as phone:
        sid, lap_ids = seed(app, recorded)
        status = pc.get("/api/setup/status").json()
        assert status["ai"]["state"] == "off" and not status["ai"]["ready"]

        car = pc.get("/api/setup/car", params={"car": CAR, "track": TRACK}).json()
        assert car["spec"]["kind"] == "game" and car["saved_setups"][0]["name"] == "base.ini"
        r = pc.post("/api/setup/base", json={"car": CAR, "track": TRACK, "folder": TRACK, "name": "base.ini"})
        assert r.status_code == 200, r.text
        base = r.json()
        assert base["preserved"]["internal"] == 1
        bid = base["version"]["id"]
        request = {
            "car": CAR, "track": TRACK, "base_version_id": bid, "lap_ids": lap_ids, "goal": "race",
            "feedback": {"items": [{"issue": "understeer", "phase": "entry", "severity": 3}], "text": "schiebt"},
        }
        # AI not configured: honest refusal, rule-based fallback is labelled.
        r = pc.post("/api/setup/analyze", json={**request, "mode": "ai"})
        assert r.status_code == 409 and "Kein KI-Anbieter" in r.json()["detail"]
        rules = pc.post("/api/setup/analyze", json={**request, "mode": "rules"}).json()
        assert rules["provider"] == "rules" and rules["status"] == "rules" and rules["sent"] is None
        assert rules["result"]["summary"].startswith("Regelbasierte Ersatzanalyse (keine KI)")
        assert [c["parameter"] for c in rules["result"]["changes"]][:1] == ["ARB_FRONT"]

        # Key, provider and consent only on the PC; the key never comes back.
        assert phone.patch("/api/settings", json={"ai_provider": "anthropic"}).status_code == 403
        assert phone.put("/api/setup/ai-key", json={"key": "sk-ant-" + "x" * 30}).status_code == 403
        pc.patch("/api/settings", json={"ai_provider": "anthropic"})
        assert pc.get("/api/setup/status").json()["ai"]["state"] == "no_key"
        r = pc.post("/api/setup/analyze", json={**request, "mode": "ai"})
        assert r.status_code == 409 and "Schlüssel" in r.json()["detail"]
        secret = "sk-ant-" + "s3cr3t" * 6
        r = pc.put("/api/setup/ai-key", json={"key": secret})
        assert r.json()["key"]["set"] and secret not in r.text
        assert pc.get("/api/setup/status").json()["ai"]["state"] == "consent"
        pc.patch("/api/settings", json={"ai_consent": setup_ai.DISCLOSURE_VERSION})
        assert pc.get("/api/setup/status").json()["ai"]["ready"]
        assert secret not in json.dumps(pc.get("/api/settings").json())

        # Preview = exactly what would be sent; no ids, no raw samples.
        preview = pc.post("/api/setup/preview", json=request).json()
        sent_text = json.dumps(preview["request"])
        assert sid not in sent_text and lap_ids[0] not in sent_text and "samples" not in sent_text
        assert preview["provider"]["endpoint"] == "api.anthropic.com"

        a = pc.post("/api/setup/analyze", json={**request, "mode": "ai"}).json()
        assert a["status"] == "ok" and provider.key == secret
        assert provider.requests[0] == preview["request"]
        names = {c["parameter"]: c for c in a["result"]["changes"]}
        assert names["ARB_FRONT"]["exportable"] and not names["FUEL"]["exportable"]

        failed = pc.post("/api/setup/analyze", json={**request, "mode": "ai"}).json()
        assert failed["status"] == "error" and failed["error"] == "Keine Verbindung"
        assert failed["result"] == {"error_code": "connection"}

        # Export: blocked for unconfirmed parameters, then the valid one.
        r = pc.post("/api/setup/export", json={"analysis_id": a["id"], "parameters": ["FUEL"]})
        assert r.status_code == 409
        r = pc.post("/api/setup/export", json={"analysis_id": a["id"], "parameters": ["ARB_FRONT"], "name": "test export"})
        assert r.status_code == 200, r.text
        exported = r.json()
        assert exported["before_after"] == [
            {"parameter": "ARB_FRONT", "label": "ARB Front", "unit": "", "old_value": 40000.0, "new_value": 37500.0, "old_raw": "8", "new_raw": "7"}
        ]
        vid = exported["version"]["id"]
        file = pc.get(f"/api/setup/versions/{vid}/file")
        assert file.content == BASE.replace(b"[ARB_FRONT]\r\nVALUE=8", b"[ARB_FRONT]\r\nVALUE=7")

        # Saving into the (test) AC folder: only on the PC, never overwriting.
        assert phone.post(f"/api/setup/versions/{vid}/save", json={"folder": "track"}).status_code == 403
        r = pc.post(f"/api/setup/versions/{vid}/save", json={"folder": "track", "name": "base"})
        assert r.status_code == 409  # would overwrite the base setup
        r = pc.post(f"/api/setup/versions/{vid}/save", json={"folder": "track"})
        assert r.status_code == 200 and r.json()["name"] == "test export.ini"
        assert pc.post(f"/api/setup/versions/{vid}/save", json={"folder": "track"}).status_code == 409
        assert hashlib.sha256(base_path.read_bytes()).hexdigest() == base_hash
        assert (base_path.parent / "test export.ini").read_bytes() == file.content

        # Manual assignment and comparison.
        assert pc.put(f"/api/setup/assignments/{sid}", json={"version_id": vid, "note": "ruhiger"}).json()["ok"]
        cmp = pc.get("/api/setup/compare", params={"car": CAR, "track": TRACK, "a": bid, "b": vid}).json()
        assert cmp["b"]["manual"] and cmp["b"]["kpis"]["used"] >= 1 and cmp["a"]["kpis"] is None
        assert any("manuell" in c for c in cmp["caveats"])
    assert secret not in caplog.text

    # Persistence: versions, analyses and assignments survive a restart.
    app = create_app(tmp_path / "data", source="demo", start_engine=False)
    with TestClient(app, client=PC) as pc:
        versions = pc.get("/api/setup/versions", params={"car": CAR, "track": TRACK}).json()
        assert {v["kind"] for v in versions} == {"base", "export"}
        assert next(v for v in versions if v["id"] == vid)["sessions"] == 1
        assert len(pc.get("/api/setup/analyses", params={"car": CAR, "track": TRACK}).json()) == 3
        assert pc.get("/api/setup/assignments", params={"car": CAR, "track": TRACK}).json()[0]["manual"]
        assert pc.get("/api/setup/status").json()["ai"]["key"]["source"] == "stored"
        pc.delete("/api/setup/ai-key")
        assert pc.get("/api/setup/status").json()["ai"]["state"] == "no_key"
        backup = pc.get("/api/backup").content
    # Backup -> restore into a fresh database keeps the setup history.
    (tmp_path / "b.sqlite").write_bytes(backup)
    fresh = Database(tmp_path / "fresh" / "t.sqlite")
    ids = fresh.restore_sqlite(tmp_path / "b.sqlite")
    rows = fresh.conn.execute("SELECT kind FROM setup_versions").fetchall()
    assert sorted(r[0] for r in rows) == ["base", "export"]
    assigned = fresh.conn.execute("SELECT session_id FROM setup_assignments").fetchall()
    assert [r[0] for r in assigned] == ids
    fresh.close()


def test_packed_car_needs_imported_spec(tmp_path, recorded, isolated_ac_folders):
    install_car(isolated_ac_folders, spec=False)
    app = create_app(tmp_path / "data", source="demo", start_engine=False)
    with TestClient(app, client=PC) as pc:
        car = pc.get("/api/setup/car", params={"car": CAR, "track": TRACK}).json()
        assert car["spec"] == {"kind": "missing", "packed": True, "game_found": True}
        assert set(car["counts"]) == {"unbestaetigt"}
        r = pc.post("/api/setup/spec", json={"car": CAR, "content_b64": b64(b"[X]\nNAME=only\n")})
        assert r.status_code == 422
        assert pc.post("/api/setup/spec", json={"car": CAR, "content_b64": b64(SPEC.encode())}).json()["ok"]
        car = pc.get("/api/setup/car", params={"car": CAR, "track": TRACK}).json()
        assert car["spec"]["kind"] == "import" and car["counts"]["abgeleitet"] >= 5
        assert pc.put("/api/setup/confirm", json={"car": CAR, "param": "FUEL", "encoding": "value"}).json()["ok"]
        car = pc.get("/api/setup/car", params={"car": CAR, "track": TRACK}).json()
        assert next(p for p in car["params"] if p["name"] == "FUEL")["status"] == "bestaetigt"
        # A setup of another car is refused; path tricks are refused.
        other = BASE.replace(b"MODEL=Prototype R", b"MODEL=other_car")
        r = pc.post("/api/setup/base", json={"car": CAR, "track": TRACK, "upload": True, "name": "x.ini", "content_b64": b64(other)})
        assert r.status_code == 422 and "anderen Fahrzeug" in r.json()["detail"]
        r = pc.post("/api/setup/base", json={"car": CAR, "track": TRACK, "folder": "..", "name": "base.ini"})
        assert r.status_code == 422
        r = pc.get("/api/setup/car", params={"car": "../x", "track": TRACK})
        assert r.status_code == 422
