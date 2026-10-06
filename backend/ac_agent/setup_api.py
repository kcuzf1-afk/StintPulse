"""HTTP API of the setup assistant (Analyse → Setup)."""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import re
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import ValidationError

from . import ac_setup, setup_ai, setup_kpis
from .access import is_loopback
from .setup_store import SetupStore

log = logging.getLogger(__name__)
ID = re.compile(r"[\w-]{1,64}")


def default_provider(settings, key):
    return setup_ai.AnthropicProvider(key, settings.ai_model)


def register(app, db, engine, data_root, provider_factory=None):
    store = SetupStore(db)
    secrets = setup_ai.SecretStore(data_root)
    make_provider = provider_factory or default_provider
    app.state.setup_store, app.state.setup_secrets = store, secrets

    def fail(exc):
        if isinstance(exc, (ac_setup.SetupError, ValueError)):
            raise HTTPException(422, str(exc)[:300])
        raise exc

    async def body(request, limit=2 * 1024**2):
        raw = await request.body()
        if len(raw) > limit:
            raise HTTPException(413, "Anfrage zu groß")
        try:
            data = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "Invalid JSON")
        if not isinstance(data, dict):
            raise HTTPException(422, "JSON-Objekt erwartet")
        return data

    def require_pc(request, what):
        if not is_loopback(request):
            raise HTTPException(403, f"{what} ist nur direkt am PC möglich")

    def text_arg(data, key, limit=100):
        value = data.get(key)
        if not isinstance(value, str) or not value or len(value) > limit:
            raise HTTPException(422, f"'{key}' fehlt oder ist ungültig")
        return value

    def file_bytes(data):
        try:
            raw = base64.b64decode(text_arg(data, "content_b64", 800_000), validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(422, "Datei konnte nicht gelesen werden (base64)")
        if len(raw) > ac_setup.MAX_INI_BYTES:
            raise HTTPException(413, "Datei ist größer als 512 KB")
        return raw

    def folders():
        cfg = engine.settings
        return ac_setup.find_ac_dir(cfg.ac_install_dir), ac_setup.find_setups_dir(cfg.ac_setups_dir)

    def spec_for(car):
        """(params, source) - the car's own unpacked setup.ini first, else an import."""
        ac_dir, _ = folders()
        path = ac_setup.car_spec_file(ac_dir, car)
        if path:
            try:
                return ac_setup.parse_spec(path.read_bytes()), {"kind": "game", "path": str(path)}
            except (OSError, ac_setup.SetupError) as exc:
                return None, {"kind": "error", "error": str(exc)[:200]}
        content, imported_at = store.spec(car)
        if content:
            try:
                return ac_setup.parse_spec(content), {"kind": "import", "imported_at": imported_at}
            except ac_setup.SetupError as exc:
                return None, {"kind": "error", "error": str(exc)[:200]}
        return None, {"kind": "missing", "packed": ac_setup.car_packed(ac_dir, car), "game_found": bool(ac_dir)}

    def context(car, track, base=None):
        ac_setup.check_folder_name(car, "Fahrzeug")
        ac_setup.check_folder_name(track, "Strecken")
        spec, source = spec_for(car)
        _, setups_dir = folders()
        obs, files = ac_setup.observed_values(setups_dir, car)
        doc = None
        if base is not None:
            doc = ac_setup.IniDoc(base["content"])
            ac_setup.add_observations(obs, doc)
        states = ac_setup.infer(spec, obs, store.confirmations(car), names=doc.values() if doc else ())
        params = ac_setup.describe(states, doc)
        if doc is not None:
            params = [p for p in params if p["in_setup"] or p["status"] != "nicht_unterstuetzt"]
        return {
            "states": states,
            "params": params,
            "doc": doc,
            "source": source,
            "observed_files": files,
        }

    def base_version(vid, car=None, track=None):
        if not isinstance(vid, str) or not ID.fullmatch(vid):
            raise HTTPException(422, "Ausgangssetup fehlt")
        v = store.version(vid, content=True)
        if not v or (car and (v["car"], v["track"]) != (car, track)):
            raise HTTPException(404, "Ausgangssetup nicht gefunden")
        return v

    def ai_status(request=None):
        cfg = engine.settings
        key = secrets.status()
        try:
            import anthropic  # noqa: F401

            sdk = True
        except ImportError:
            sdk = False
        ready = cfg.ai_provider != "off" and key["set"] and sdk and cfg.ai_consent >= setup_ai.DISCLOSURE_VERSION
        state = (
            "off" if cfg.ai_provider == "off"
            else "sdk_missing" if not sdk
            else "key_unreadable" if key["unreadable"] and not key["set"]
            else "no_key" if not key["set"]
            else "consent" if cfg.ai_consent < setup_ai.DISCLOSURE_VERSION
            else "ready"
        )
        return {
            "provider": cfg.ai_provider,
            "model": cfg.ai_model,
            "state": state,
            "ready": ready,
            "key": key,
            "sdk": sdk,
            "consent": cfg.ai_consent >= setup_ai.DISCLOSURE_VERSION,
            "disclosure_version": setup_ai.DISCLOSURE_VERSION,
            "providers": setup_ai.PROVIDERS,
            "default_model": setup_ai.DEFAULT_MODEL,
            "pc": bool(request and is_loopback(request)),
        }

    @app.get("/api/setup/status")
    async def setup_status(request: Request):
        ac_dir, setups_dir = folders()
        return {
            "ai": ai_status(request),
            "ac": {
                "install_dir": str(ac_dir) if ac_dir else None,
                "setups_dir": str(setups_dir),
                "setups_found": setups_dir.is_dir(),
            },
            "goals": setup_kpis.GOALS,
            "issues": setup_kpis.ISSUES,
            "phases": setup_kpis.PHASES,
        }

    @app.put("/api/setup/ai-key")
    async def ai_key_set(request: Request):
        require_pc(request, "Der API-Schlüssel")
        data = await body(request, 4096)
        try:
            secrets.set(data.get("key") if isinstance(data.get("key"), str) else "")
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        log.info("AI key stored (value not logged)")
        return ai_status(request)

    @app.delete("/api/setup/ai-key")
    async def ai_key_clear(request: Request):
        require_pc(request, "Der API-Schlüssel")
        secrets.clear()
        return ai_status(request)

    @app.get("/api/setup/car")
    async def car_info(car: str, track: str):
        try:
            ctx = await asyncio.to_thread(context, car, track)
            _, setups_dir = folders()
            saved = await asyncio.to_thread(ac_setup.list_setups, setups_dir, car, track)
        except ac_setup.SetupError as exc:
            fail(exc)
        counts = {}
        for p in ctx["params"]:
            counts[p["status"]] = counts.get(p["status"], 0) + 1
        return {
            "car": car,
            "track": track,
            "spec": ctx["source"],
            "observed_files": ctx["observed_files"],
            "counts": counts,
            "params": ctx["params"],
            "saved_setups": saved,
        }

    @app.post("/api/setup/spec")
    async def spec_import(request: Request):
        data = await body(request)
        car = text_arg(data, "car")
        raw = file_bytes(data)
        try:
            ac_setup.check_folder_name(car, "Fahrzeug")
            params = ac_setup.parse_spec(raw)
        except ac_setup.SetupError as exc:
            fail(exc)
        store.save_spec(car, raw)
        return {"ok": True, "params": sum(1 for p in params.values() if p.usable)}

    @app.delete("/api/setup/spec")
    async def spec_delete(car: str):
        store.delete_spec(car)
        return {"ok": True}

    @app.put("/api/setup/confirm")
    async def confirm(request: Request):
        data = await body(request)
        car, param = text_arg(data, "car"), text_arg(data, "param").upper()
        encoding = data.get("encoding")
        if encoding not in (None, "value", "clicks", "tenths"):
            raise HTTPException(422, "Unbekannte Kodierung")
        try:
            ac_setup.check_folder_name(car, "Fahrzeug")
        except ac_setup.SetupError as exc:
            fail(exc)
        store.confirm(car, param, encoding)
        return {"ok": True}

    @app.post("/api/setup/base")
    async def base_select(request: Request):
        data = await body(request)
        car, track = text_arg(data, "car"), text_arg(data, "track")
        try:
            ac_setup.check_folder_name(car, "Fahrzeug")
            ac_setup.check_folder_name(track, "Strecken")
            if data.get("upload"):
                raw = file_bytes(data)
                name = ac_setup.clean_name(data.get("name"), "import")
                origin = "upload"
            else:
                _, setups_dir = folders()
                folder, name = text_arg(data, "folder"), text_arg(data, "name", 200)
                _, raw = await asyncio.to_thread(ac_setup.read_saved_setup, setups_dir, car, track, folder, name)
                origin = f"{folder}/{name}"
            doc = ac_setup.IniDoc(raw)
            model = doc.get("CAR", "MODEL")
            if model and model.lower() != car.lower():
                raise ac_setup.SetupError(f"Setup gehört zu einem anderen Fahrzeug ({model[:60]})")
            if not doc.values():
                raise ac_setup.SetupError("Keine Setup-Werte (VALUE=) gefunden")
        except (ac_setup.SetupError, OSError) as exc:
            fail(ac_setup.SetupError(str(exc)))
        version = store.add_base(car, track, name, origin, raw)
        ctx = await asyncio.to_thread(context, car, track, {"content": raw})
        dup = sorted(ctx["doc"].duplicates())
        internal = sum(1 for s in ctx["doc"].values() if ac_setup.is_internal(s))
        return {
            "version": version,
            "params": ctx["params"],
            "spec": ctx["source"],
            "preserved": {
                "sections": len(ctx["doc"].sections),
                "internal": internal,
                "duplicates": dup,
            },
        }

    def load_laps(car, track, lap_ids):
        if not isinstance(lap_ids, list) or not 1 <= len(lap_ids) <= setup_kpis.MAX_LAPS:
            raise HTTPException(422, f"1 bis {setup_kpis.MAX_LAPS} Runden auswählen")
        laps, metas = [], {}
        for lap_id in dict.fromkeys(lap_ids):
            if not isinstance(lap_id, str) or not ID.fullmatch(lap_id):
                raise HTTPException(422, "Ungültige Runden-ID")
            lap = db.lap(lap_id)
            if not lap:
                raise HTTPException(404, "Runde nicht gefunden")
            if lap.session_id not in metas:
                metas[lap.session_id] = db.session(lap.session_id)["meta"]
            laps.append(lap)
        layouts = {(m["car"], m["track"], m["layout"]) for m in metas.values()}
        if len(layouts) != 1 or next(iter(layouts))[:2] != (car, track):
            raise HTTPException(422, "Runden müssen zu diesem Fahrzeug und genau einem Streckenlayout gehören")
        length = next(iter(metas.values()))["track_length"]
        return laps, metas, length

    def prepare(data):
        car, track = text_arg(data, "car"), text_arg(data, "track")
        goal = data.get("goal")
        if goal not in setup_kpis.GOALS:
            raise HTTPException(422, "Ziel wählen")
        goal_text = data.get("goal_text") or ""
        if not isinstance(goal_text, str) or len(goal_text) > 300:
            raise HTTPException(422, "Zielbeschreibung: höchstens 300 Zeichen")
        try:
            feedback = setup_kpis.Feedback.model_validate(data.get("feedback") or {})
        except ValidationError:
            raise HTTPException(422, "Rückmeldung ungültig")
        base = base_version(data.get("base_version_id"), car, track)
        laps, metas, length = load_laps(car, track, data.get("lap_ids"))
        try:
            kpi = setup_kpis.analyse(laps, metas, length, engine.settings, data.get("include_invalid") is True)
        except ValueError as exc:
            raise HTTPException(422, str(exc)[:300])
        ctx = context(car, track, base)
        evidence = setup_kpis.evidence(feedback, kpi)
        request_payload = setup_ai.build_request(car, track, goal, goal_text, feedback, kpi, evidence, ctx["params"])
        return {
            "car": car, "track": track, "goal": goal, "goal_text": goal_text, "feedback": feedback,
            "base": base, "laps": laps, "kpi": kpi, "ctx": ctx, "evidence": evidence, "request": request_payload,
        }

    @app.post("/api/setup/preview")
    async def preview(request: Request):
        data = await body(request)
        p = await asyncio.to_thread(prepare, data)
        status = ai_status(request)
        return {
            "kpis": p["kpi"],
            "evidence": p["evidence"],
            "request": p["request"],
            "provider": {
                "id": status["provider"],
                "model": status["model"],
                **setup_ai.PROVIDERS.get(status["provider"], {}),
            },
        }

    @app.post("/api/setup/analyze")
    async def analyze(request: Request):
        data = await body(request)
        mode = data.get("mode")
        if mode not in ("ai", "rules"):
            raise HTTPException(422, "Modus 'ai' oder 'rules'")
        p = await asyncio.to_thread(prepare, data)
        context_info = {"used_laps": p["kpi"]["used"], "has_feedback": bool(p["feedback"].items or p["feedback"].text.strip())}
        common = {
            "car": p["car"], "track": p["track"], "base_version_id": p["base"]["id"],
            "lap_ids": [l.id for l in p["laps"]], "goal": p["goal"],
            "feedback": {**p["feedback"].model_dump(), "goal_text": p["goal_text"], "include_invalid": data.get("include_invalid") is True},
            "kpis": {"kpis": p["kpi"], "evidence": p["evidence"]},
        }
        if p["kpi"]["used"] == 0:
            raise HTTPException(422, "Keine repräsentative Runde - bitte andere Runden wählen")
        if mode == "rules":
            raw = setup_ai.rule_based(p["ctx"]["params"], p["feedback"], p["evidence"], p["kpi"])
            result = setup_ai.validate_result(raw, p["ctx"]["states"], p["ctx"]["params"], context_info)
            return store.add_analysis(**common, provider="rules", model=None, status="rules", sent=None, result=result)
        status = ai_status(request)
        if not status["ready"]:
            messages = {
                "off": "Kein KI-Anbieter eingestellt",
                "sdk_missing": "Anthropic-SDK fehlt in dieser Version",
                "no_key": "Kein API-Schlüssel hinterlegt",
                "key_unreadable": "Gespeicherter API-Schlüssel ist auf diesem PC nicht lesbar - bitte neu eingeben",
                "consent": "Bitte zuerst die Datenübertragung bestätigen",
            }
            raise HTTPException(409, messages.get(status["state"], "KI nicht bereit"))
        provider = make_provider(engine.settings, secrets.get())
        user = json.dumps(p["request"], ensure_ascii=False, separators=(",", ":"))
        sent = {"provider": provider.id, "model": engine.settings.ai_model, "payload": p["request"]}
        try:
            text, meta = await asyncio.to_thread(
                provider.complete, setup_ai.SYSTEM_PROMPT, user, setup_ai.RESPONSE_SCHEMA
            )
            result = setup_ai.validate_result(text, p["ctx"]["states"], p["ctx"]["params"], context_info)
            result["provider_meta"] = meta
        except setup_ai.ProviderError as exc:
            log.warning("Setup AI call failed: %s", exc.code)
            return store.add_analysis(
                **common, provider=provider.id, model=engine.settings.ai_model, status="error", sent=sent,
                result={"error_code": exc.code}, error=str(exc)[:300],
            )
        return store.add_analysis(**common, provider=provider.id, model=meta.get("model") or engine.settings.ai_model, status="ok", sent=sent, result=result)

    @app.get("/api/setup/analyses")
    async def analyses(car: str, track: str):
        return await asyncio.to_thread(store.analyses, car, track)

    @app.get("/api/setup/analyses/{aid}")
    async def analysis_get(aid: str):
        a = store.analysis(aid)
        if not a:
            raise HTTPException(404, "Analyse nicht gefunden")
        return a

    def build_export(data):
        aid = data.get("analysis_id")
        a = store.analysis(aid) if isinstance(aid, str) and ID.fullmatch(aid) else None
        if not a or a["status"] not in ("ok", "rules"):
            raise HTTPException(404, "Analyse mit Empfehlungen nicht gefunden")
        wanted = data.get("parameters")
        if not isinstance(wanted, list) or not wanted or len(wanted) > setup_ai.MAX_CHANGES:
            raise HTTPException(422, "1 bis 3 Änderungen auswählen")
        base = base_version(a["base_version_id"], a["car"], a["track"])
        ctx = context(a["car"], a["track"], base)
        params = {p["name"]: p for p in ctx["params"]}
        changes, before_after = {}, []
        for name in dict.fromkeys(wanted):
            change = next((c for c in a["result"].get("changes", []) if c["parameter"] == name), None)
            if not change:
                raise HTTPException(422, f"{name} ist keine Empfehlung dieser Analyse")
            if not change["exportable"]:
                raise HTTPException(409, f"{change['label']}: nicht exportierbar ({change['export_block']})")
            p = params.get(name)
            stored, error = ac_setup.check_change(ctx["states"].get(name), p["raw"] if p else None, change["new_value"])
            if error or str(stored) != change["new_raw"]:
                raise HTTPException(409, f"{change['label']}: Prüfung fehlgeschlagen ({error or 'Kodierung hat sich geändert'})")
            changes[name] = stored
            before_after.append(
                {
                    "parameter": name, "label": change["label"], "unit": change["unit"],
                    "old_value": change["old_value"], "new_value": change["new_value"],
                    "old_raw": p["raw"], "new_raw": str(stored),
                }
            )
        doc = ctx["doc"]
        content = doc.with_values(changes)
        # Self-check: only the selected VALUE lines differ from the base file.
        new_doc = ac_setup.IniDoc(content)
        changed_lines = {e.line for e in doc.entries if e.key == "VALUE" and e.section in changes}
        if len(new_doc.lines) != len(doc.lines) or any(
            (a_line != b_line) != (i in changed_lines) for i, (a_line, b_line) in enumerate(zip(doc.lines, new_doc.lines))
        ):
            raise HTTPException(500, "Export-Selbstprüfung fehlgeschlagen")
        for name, stored in changes.items():
            if ac_setup.parse_int(new_doc.values()[name]) != stored:
                raise HTTPException(500, "Export-Selbstprüfung fehlgeschlagen")
        try:
            if data.get("name"):
                file_name = ac_setup.safe_file_name(str(data["name"]))
            else:
                file_name = ac_setup.clean_name(Path(base["name"]).stem[:45] + " stintpulse")
        except ac_setup.SetupError as exc:
            fail(exc)
        return a, base, content, file_name, before_after

    @app.post("/api/setup/export")
    async def export(request: Request):
        data = await body(request)
        a, base, content, file_name, before_after = await asyncio.to_thread(build_export, data)
        version = store.add_export(a["car"], a["track"], base["id"], a["id"], file_name, content, before_after)
        return {"version": version, "before_after": before_after, "file_name": file_name}

    @app.get("/api/setup/versions")
    async def versions(car: str, track: str):
        return await asyncio.to_thread(store.versions, car, track)

    @app.get("/api/setup/versions/{vid}/file")
    async def version_file(vid: str):
        v = store.version(vid, content=True) if ID.fullmatch(vid) else None
        if not v:
            raise HTTPException(404, "Version nicht gefunden")
        return Response(
            v["content"],
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{ac_setup.clean_name(v["name"])}"'},
        )

    @app.post("/api/setup/versions/{vid}/save")
    async def version_save(vid: str, request: Request):
        require_pc(request, "Speichern in den AC-Setupordner")
        data = await body(request)
        v = store.version(vid, content=True) if ID.fullmatch(vid) else None
        if not v or v["kind"] != "export":
            raise HTTPException(404, "Exportierte Version nicht gefunden")
        folder = data.get("folder")
        if folder not in ("track", "generic"):
            raise HTTPException(422, "Ordner 'track' oder 'generic'")
        _, setups_dir = folders()
        protected = []
        parent = store.version(v["parent_id"]) if v["parent_id"] else None
        if parent and parent["origin"] != "upload":
            protected.append(Path(setups_dir) / v["car"] / parent["origin"])
        try:
            target = await asyncio.to_thread(
                ac_setup.save_new_setup, setups_dir, v["car"], v["track"] if folder == "track" else "generic",
                data.get("name") or v["name"], v["content"], protected,
            )
        except (ac_setup.SetupError, OSError) as exc:
            raise HTTPException(409 if isinstance(exc, ac_setup.SetupError) else 500, str(exc)[:300])
        store.mark_saved(vid, target)
        return {"path": str(target), "folder": target.parent.name, "name": target.name}

    @app.get("/api/setup/assignments")
    async def assignments(car: str, track: str):
        ids = {v["id"] for v in store.versions(car, track)}
        return store.assignments(ids)

    @app.put("/api/setup/assignments/{sid}")
    async def assign(sid: str, request: Request):
        data = await body(request)
        session = db.session(sid) if ID.fullmatch(sid) else None
        if not session:
            raise HTTPException(404, "Session nicht gefunden")
        vid = data.get("version_id")
        note = data.get("note") or ""
        if not isinstance(note, str) or len(note) > 500:
            raise HTTPException(422, "Notiz: höchstens 500 Zeichen")
        if vid is not None:
            v = store.version(vid) if isinstance(vid, str) and ID.fullmatch(vid) else None
            if not v or (v["car"], v["track"]) != (session["meta"]["car"], session["meta"]["track"]):
                raise HTTPException(422, "Version passt nicht zu Fahrzeug/Strecke der Session")
        store.assign(sid, vid, note)
        return {"ok": True}

    def side(car, track, vid, include_invalid=False):
        v = store.version(vid) if isinstance(vid, str) and ID.fullmatch(vid) else None
        if not v or (v["car"], v["track"]) != (car, track):
            raise HTTPException(404, "Version nicht gefunden")
        links = store.assignments({vid})
        laps, metas = [], {}
        for link in links:
            s = db.session(link["session_id"])
            if not s:
                continue
            metas[s["id"]] = s["meta"]
            laps += [row for row in s["laps"] if row["complete"]]
        out = {"version": v, "sessions": len(metas), "notes": [l["note"] for l in links if l["note"]], "manual": True}
        if not laps:
            return {**out, "kpis": None}
        laps = sorted(laps, key=lambda r: r["created_at"])[-setup_kpis.MAX_LAPS:]
        loaded = [db.lap(r["id"]) for r in laps]
        layouts = {metas[l.session_id]["layout"] for l in loaded}
        if len(layouts) > 1:
            return {**out, "kpis": None, "error": "Zugeordnete Sessions haben verschiedene Layouts"}
        try:
            kpi = setup_kpis.analyse(
                loaded, metas, metas[loaded[0].session_id]["track_length"], engine.settings, include_invalid
            )
        except ValueError as exc:
            return {**out, "kpis": None, "error": str(exc)[:200]}
        if v["analysis_id"]:
            a = store.analysis(v["analysis_id"])
            out["feedback_before"] = a["feedback"] if a else None
        return {**out, "kpis": kpi, "layout": next(iter(layouts))}

    @app.get("/api/setup/compare")
    async def compare(car: str, track: str, a: str, b: str, include_invalid: bool = False):
        left = await asyncio.to_thread(side, car, track, a, include_invalid)
        right = await asyncio.to_thread(side, car, track, b, include_invalid)
        return {"a": left, "b": right, "caveats": caveats(left, right)}

    return store


def caveats(a, b):
    out = ["Zuordnung Session → Setup ist manuell; StintPulse kann das tatsächlich gefahrene Setup nicht auslesen."]
    ka, kb = a.get("kpis"), b.get("kpis")
    if not ka or not kb:
        out.append("Für mindestens eine Version gibt es keine zugeordneten, auswertbaren Runden.")
        return out
    if a.get("layout") != b.get("layout"):
        out.append("Verschiedene Streckenlayouts - nicht vergleichbar.")
    for k, label in ((ka, "A"), (kb, "B")):
        if k["used"] < 3:
            out.append(f"Version {label}: nur {k['used']} repräsentative Runde(n) - Unterschiede können Zufall sein.")
    ca, cb = ka["conditions"], kb["conditions"]
    if ca.get("compounds") != cb.get("compounds"):
        out.append(f"Unterschiedliche Reifenmischung ({', '.join(ca.get('compounds') or ['?'])} / {', '.join(cb.get('compounds') or ['?'])}).")
    for key, label, limit in (("fuel_start_l", "Tankfüllung", 5), ("air_c", "Lufttemperatur", 3), ("road_c", "Streckentemperatur", 3)):
        ra, rb = ca.get(key), cb.get(key)
        if ra and rb and abs((ra[0] + ra[1]) / 2 - (rb[0] + rb[1]) / 2) > limit:
            out.append(f"{label} unterscheidet sich deutlich ({ra} / {rb}) - beeinflusst Rundenzeit und Reifen.")
        elif not ra or not rb:
            out.append(f"{label} nicht für beide Versionen bekannt.")
    out.append("Reifenverschleiß, Streckengummi und Tagesform werden nicht herausgerechnet.")
    return out
