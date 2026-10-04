"""Plain-language next steps for the connection problem actually detected.

Only derived from measured diagnostics; never generic advice. Administrator
rights are suggested only when Windows actually reported ERROR_ACCESS_DENIED.
"""


def hints(report):
    out = []

    def add(level, de, en):
        out.append({"level": level, "de": de, "en": en})

    status = report["status"]
    game = report.get("game") or {}
    process = game.get("process") or {}
    pages = game.get("pages") or []
    if status == "capture_failed":
        add(
            "error",
            "Der Erfassungs-Thread ist ausgefallen. „Verbindung erneut prüfen“ startet ihn neu; "
            "die Ursache steht unter „Erfassung“.",
            "The capture thread stopped. 'Re-check connection' restarts it; the cause is "
            "shown under 'Capture'.",
        )
    if status == "demo":
        add(
            "info",
            "Demo aktiv: Alle Werte sind synthetisch. „AC verbinden“ schaltet auf das echte Spiel um.",
            "Demo active: all values are synthetic. 'Connect AC' switches to the real game.",
        )
        return out
    if not report["system"]["windows"]:
        add(
            "error",
            "Echte AC-Telemetrie gibt es nur unter Windows auf demselben PC wie das Spiel.",
            "Real AC telemetry requires Windows on the same PC as the game.",
        )
        return out
    if status == "access_denied":
        add(
            "error",
            "Windows verweigert den Zugriff auf den AC-Speicher (Fehler 5). Typische Ursache: "
            "Assetto Corsa bzw. Content Manager läuft als Administrator, diese App nicht. Beide "
            "mit denselben Rechten starten – bevorzugt beide ohne Administratorrechte, sonst "
            "diese App ebenfalls „Als Administrator ausführen“.",
            "Windows denied access to AC shared memory (error 5). Usually AC/Content Manager "
            "runs as administrator and this app does not. Start both with the same rights.",
        )
    elif status == "waiting_game":
        if process.get("status") != "found" and process.get("launcher"):
            add(
                "info",
                "Nur der Launcher läuft ("
                + ", ".join(process["launcher"])
                + "). Ein Launcher ist keine Fahrsession: Fahrt starten (Drive/Fahren) – erst "
                "dann startet acs.exe und stellt Telemetrie bereit.",
                "Only the launcher is running. Start a drive; only then acs.exe provides telemetry.",
            )
        else:
            add(
                "info",
                "Assetto Corsa ist nicht in einer Fahrsession. Spiel über Steam oder Content "
                "Manager starten und auf die Strecke fahren.",
                "Assetto Corsa is not in a driving session. Start it via Steam or Content "
                "Manager and go on track.",
            )
    elif status == "waiting_session":
        add(
            "info",
            "acs.exe läuft, aber es ist noch keine Fahrsession aktiv (Laden/Menü). Warten, bis "
            "du im Auto sitzt.",
            "acs.exe is running but no driving session is active yet (loading/menu).",
        )
    elif status == "not_initialized":
        add(
            "info",
            "Speicherbereiche gefunden, aber noch nicht befüllt. Die Session lädt vermutlich noch.",
            "Shared memory found but not filled yet. The session is probably still loading.",
        )
    elif status == "stale":
        add(
            "warning",
            "Das Spiel liefert keine neuen Daten (Packet-IDs unverändert). Spiel minimiert, "
            "hängt oder beendet? Nach einem Neustart verbindet die App automatisch.",
            "The game delivers no new data (packet IDs unchanged). The app reconnects "
            "automatically after a restart.",
        )
    elif status == "decode_error":
        add(
            "error",
            "Die Daten konnten nicht dekodiert werden: "
            + str(game.get("last_decode_error"))
            + ". Läuft wirklich das originale Assetto Corsa (nicht ACC)? Bitte Diagnose kopieren "
            "und melden.",
            "Data could not be decoded. Is this the original Assetto Corsa (not ACC)? Please "
            "copy the diagnostics and report it.",
        )
    elif status == "memory_error":
        add(
            "error",
            "Speicherbereich vorhanden, aber nicht nutzbar: "
            + str(game.get("message"))
            + ". Zu kleine Seiten deuten auf eine sehr alte AC-Version oder ein anderes Spiel hin.",
            "Shared memory present but unusable. Pages that are too small indicate a very old "
            "AC version or another game.",
        )
    if process.get("status") == "unavailable":
        add(
            "info",
            "Die Prozessprüfung ist nicht möglich ("
            + str(process.get("error"))
            + "). Die Speicherbereiche werden trotzdem direkt gelesen.",
            "Process check unavailable. Shared memory is still read directly.",
        )
    if (
        process.get("status") == "found"
        and pages
        and not any(p.get("present") for p in pages)
        and status in ("waiting_session", "waiting_game")
    ):
        add(
            "info",
            "acs.exe gefunden, aber noch keine Speicherseite. Lädt die Session noch? Falls es "
            "so bleibt: App und Spiel müssen im selben Windows-Benutzerkonto laufen.",
            "acs.exe found but no shared memory yet. If this persists, app and game must run "
            "in the same Windows user session.",
        )
    if game.get("unknown_fields"):
        add(
            "warning",
            "Einzelne Spielwerte waren unplausibel und werden als unbekannt geführt: "
            + "; ".join(game["unknown_fields"]),
            "Some game values were implausible and are marked unknown.",
        )
    if report["websocket"]["clients"] == 0:
        add(
            "info",
            "Kein Browser per WebSocket verbunden – das Dashboard empfängt keine Live-Daten.",
            "No browser connected via WebSocket; the dashboard receives no live data.",
        )
    return out
