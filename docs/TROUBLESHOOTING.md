# Fehlerbehebung

## Start und Installation

**`py -3.12` nicht gefunden:** Python 3.12 x64 mit Launcher installieren. Python, Node und npm im Terminal mit `py -3.12 --version`, `node --version`, `npm --version` prüfen. Das Projekt wurde mit Node 24 getestet. PowerShell anschließend neu öffnen.

**PowerShell blockiert Skripte:** Im aktuellen Terminal `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` setzen. Danach `scripts/install.ps1` beziehungsweise `scripts/start.ps1` ausführen. Es wird keine dauerhafte systemweite Policy-Änderung benötigt.

**Dashboard fehlt / HTTP 503:** `cd frontend; npm ci; npm run build` ausführen und Server neu starten. Der Produktionsbuild muss in `frontend/dist` liegen. Bei einer EXE muss der ganze `onedir`-Ordner einschließlich `_internal` kopiert werden.

**Port belegt:** Anderen Port in den Einstellungen setzen und neu starten oder `python -m ac_agent --demo --port 8766` verwenden. Vite-Proxy und Tablet-Adresse gegebenenfalls ebenfalls ändern.

**Server läuft, Browser öffnet sich nicht:** `http://localhost:8765` manuell öffnen. `--no-browser` oder die Einstellung **Dashboard beim Start öffnen** kann das automatische Öffnen abschalten.

## Keine echten Daten / Spiel wird nicht erkannt

### Behobene Ursache (Oktober 2026)

Bis einschließlich des ausgelieferten Pakets öffnete der Reader die Graphics-Seite unter dem Namen `acpmf_graphic`. Das originale Assetto Corsa erzeugt sie als **`acpmf_graphics`** (Plural; belegt in Kunos’ Beispiel über mdjarvs `AssettoCorsa.cs` und in Rombiks `sim_info.py`, siehe [DATA_SOURCES.md](DATA_SOURCES.md)). `OpenFileMappingW` lieferte deshalb immer Windows-Fehler 2 („nicht gefunden“), der gesamte Verbindungsaufbau brach ab und die App meldete dauerhaft „Waiting for AC: enter a driving session“ – auch während einer laufenden Fahrt. Nachgewiesen auf Windows 11 mit laufendem AC 1.16.4 (Status `AC_LIVE`): alter Code 0 Samples in 4 s, `acpmf_graphic` fehlt, `acpmf_graphics` vorhanden; korrigierter Code ≈ 58 Hz.

Weitere behobene Schwachstellen im selben Pfad:

- Die Prozessprüfung (`acs.exe`) blockierte vorhandene Speicherseiten. Sie ist jetzt nur noch Diagnose.
- Ein einzelner unplausibler Metadatenwert (z. B. `sectorCount = 0` einer Mod-Strecke) erzeugte bei **jedem** Frame einen Fehler, schloss die Verbindung und verhinderte jeden Empfang. Solche Werte werden jetzt als unbekannt markiert (`unknown_fields`); Messwerte bleiben erhalten.
- Unerwartete Ausnahmen konnten den Erfassungs-Thread unbemerkt beenden. Fehler werden jetzt gezählt, protokolliert und angezeigt; ein ausgefallener Thread erscheint als „Erfassungs-Thread ausgefallen“ und wird über „Verbindung erneut prüfen“ neu gestartet.
- Nach dem Wechsel von Demo zu AC blieben Demo-Fahrer, -Strecke und -Messwerte in der Live-Ansicht stehen. Die Live-Ansicht wird jetzt geleert; Demo-Sessions bleiben im Archiv.
- Eine zuvor im Dashboard gewählte Demo wurde beim nächsten Start der EXE aus den gespeicherten Einstellungen übernommen. Jeder Start verwendet jetzt das echte Spiel, außer `--demo` wird angegeben.
- „ENGINE ONLINE“ beschrieb nur die Browser-Backend-Verbindung. Backend- und Spielstatus werden jetzt getrennt angezeigt.

### Schritt für Schritt prüfen

1. App starten (`scripts\start.ps1` oder EXE), Dashboard öffnen, **Einstellungen → Erweitert / Diagnose** wählen (oder den Hinweis „Diagnose“ im Live-Dashboard).
2. Assetto Corsa über Steam oder Content Manager starten und **auf die Strecke fahren** (im Auto sitzen).
3. In der Diagnose müssen erscheinen: Spielprozess *gefunden*, alle drei Speicherbereiche *vorhanden / lesbar / initialisiert / nur lesend*, erwartete = gelesene Bytes (580 / 296 / 684), steigende Packet-IDs, Spielstatus `AC_LIVE`, Empfangsrate um 50–60 Hz.
4. Ohne Browser: `.\.venv\Scripts\python.exe scripts\check_ac_live.py --seconds 8` während der Fahrt. `"ok": true` mit Auto/Strecke ist der Nachweis einer echten Verbindung. Ein Demo- oder Fixture-Test ist kein Nachweis.

### Statusanzeigen und was zu tun ist

| Anzeige | Bedeutung | Handlung |
|---|---|---|
| DEMO AKTIV | synthetische Daten, keine Spielverbindung | „AC verbinden“ |
| WARTE AUF SPIEL | `acs.exe` nicht gefunden und keine Speicherseiten | Fahrt starten; ein laufender Launcher/Content Manager allein ist keine Fahrsession |
| SPIEL GEFUNDEN, WARTE AUF FAHRSESSION | `acs.exe` läuft, Seiten fehlen noch oder Status `AC_OFF` | warten, bis die Session geladen ist |
| SPEICHER GEFUNDEN, NICHT INITIALISIERT | Seiten vorhanden, Static-Seite leer (`carModel`/`track`) | Session lädt noch |
| TELEMETRIE VERBUNDEN | echte Samples mit `AC_LIVE` | – |
| SPIEL PAUSIERT | `AC_PAUSE`; keine Runden werden aufgezeichnet | weiterfahren |
| REPLAY | `AC_REPLAY`; wird nicht als Live-Runde gespeichert | – |
| DATEN VERALTET | Packet-IDs seit ≥ 2 s unverändert | Spiel minimiert/hängt/beendet? Nach Spielende wird automatisch neu verbunden |
| ZUGRIFF VERWEIGERT | Windows-Fehler 5 beim Öffnen | AC/Content Manager und App mit **denselben** Rechten starten (bevorzugt beide ohne Administrator). Nur in diesem Fall ist „Als Administrator ausführen“ für die App eine Lösung |
| DEKODIERUNGSFEHLER | Seite passt nicht zum Original-AC-Layout (z. B. unbekannter Status) | Diagnose kopieren und melden; ACC wird nicht unterstützt |
| SPEICHERFEHLER | Seite vorhanden, aber kleiner als das Original-Layout | sehr alte AC-Version oder anderes Spiel |
| ERFASSUNG AUSGEFALLEN | Erfassungs-Thread beendet | „Verbindung erneut prüfen“; Ursache steht unter „Erfassung“ |
| BACKEND GETRENNT | Browser erreicht den lokalen Server nicht | Server läuft? Port/Firewall prüfen; sagt nichts über das Spiel aus |

### Diagnose-Endpunkt und Protokoll

- `GET /api/diagnostics` – System/Python-Architektur, gewählte Quelle, Thread-Zustand, Prozessprüfung (`found` / `not_found` / `unavailable`), je Speicherbereich vorhanden/lesbar/initialisiert/nur lesend/erwartete und gelesene Bytes/Windows-Fehlercode, Packet-IDs, Alter der letzten Aktualisierung, Spielstatus, letzte Dekodierung, letzte Fehlermeldung, Empfangsrate, WebSocket-Clients und Handlungshinweise. Im LAN ist der Bearer-Token nötig; der Bericht enthält weder Token noch Speicherinhalte.
- `POST /api/diagnostics/recheck` – schließt die Handles, prüft sofort neu und startet einen ausgefallenen Erfassungs-Thread neu (Schaltfläche „Verbindung erneut prüfen“).
- Protokolldatei: `<Datenordner>\logs\stintpulse.log` – bei der portablen EXE `Daten\logs` neben der EXE, sonst `%LOCALAPPDATA%\StintPulse\logs` (rotierend, 3 × 1 MB). Enthält Zustandswechsel, konkrete Dekodierungsfehler (gedrosselt) und Thread-Fehler, keine Token und keine Speicherinhalte.

Die App öffnet ausschließlich bestehende Seiten mit `OpenFileMappingW(FILE_MAP_READ)` und `MapViewOfFile(FILE_MAP_READ)` (Seitenschutz `PAGE_READONLY`). Sie erzeugt nie eigene oder leere Ersatzseiten. Die Prozessprüfung blockiert keine lesbaren Seiten; ist sie nicht möglich, wird trotzdem gelesen. Bei unveränderten Packet-IDs werden die Handles nur freigegeben, wenn `acs.exe` nicht bestätigt laufen kann. So wird ein Spielneustart erkannt, ohne Pause, Menü oder Laden zu unterbrechen.

## Streckenkarte

**Karte ist eine offene Linie / „Strecke wird aufgezeichnet · NN %“:** Solange für Strecke und Layout noch keine vollständige Runde vorliegt, zeigt die Karte die bisher gefahrenen Positionen. Sie wächst über die Ziellinie hinweg weiter (sie fängt nicht jede Runde neu an); eine spätere Runde ersetzt Punkte an derselben Stelle, statt eine zweite Linie darüberzulegen. Noch nicht gefahrene Abschnitte bleiben eine Lücke, es wird nichts künstlich verbunden. Nach der ersten vollständigen Runde (von Start/Ziel bis Start/Ziel, ohne Teleport/Neustart, Ausritte zählen trotzdem) wird deren Geometrie gespeichert und künftig sofort verwendet.

**Ursache der früher nie vollständigen Karte (behoben):** Assetto Corsa setzt an der Ziellinie Streckenposition und Rundenuhr einen Frame **vor** dem Rundenzähler zurück (gemessen mit AC 1.16 in Sepang). Ältere Versionen werteten das als „Teleport/Neustart“ und brachen jede Runde an der Linie ab – deshalb gab es nie eine vollständige Runde und damit nie eine gespeicherte Karte. Beide Reihenfolgen (Position zuerst oder Zähler zuerst) gelten jetzt als dieselbe Überquerung. Wurde früher schon eine Runde komplett gefahren, wird die Karte beim nächsten Sessionstart automatisch aus dieser gespeicherten Runde erzeugt; die alten Runden selbst bleiben als „unvollständig“ markiert.

## Onboard-Aufnahme

- **Keine Aufnahme:** Läuft das Dashboard im Browser **auf dem PC** (localhost)? Nur dort wird aufgenommen; Handys sehen Videos nur an. Videoquelle „OBS Virtual Camera / Webcam“ gewählt und „Onboard automatisch aufnehmen“ gespeichert? Die Anzeige oben rechts nennt den Zustand (bereit, läuft, Videoquelle fehlt, wird gespeichert, gespeichert, fehlgeschlagen); ein Klick darauf öffnet die Einstellungen.
- **Aufnahme endet beim Schließen des Browsers** – das ist so: Aufgenommen wird im Browser-Tab. Bereits übertragene Teile bleiben erhalten und werden automatisch abgeschlossen.
- **Speicherlimit/Plattenplatz:** Die Aufnahme stoppt mit Meldung; es wird nichts gelöscht, solange die Löschregel aus ist. Nach dem Ändern „Erneut versuchen“.
- **Video lässt sich nicht spulen:** Nur wenn beim Abschluss zu wenig Platz für die Indizierung war (Hinweis in der Aufnahme). Die Datei spielt trotzdem ab Beginn.
- **Ton fehlt:** Die OBS-Kamera hat keinen Ton; das PC-Programm nimmt ihn selbst auf. „Spielsound“ braucht laufendes Assetto Corsa (acs.exe) und Windows 10 2004 oder neuer; sonst „Gesamter PC-Ton“ wählen. Der Status steht in Einstellungen → Onboard und HUD und im Player („mit Ton“/„ohne Ton“ mit Grund).
- Videos und Tondateien (.webm/.wav) liegen im Videoordner (Einstellungen → Onboard und HUD) und sind nicht im Datenbank-Backup enthalten.

## Runden, Sektoren und unbekannte Werte

**Erste Runde ungültig/unvollständig:** Einstieg mitten in einer Runde. Die nächste vollständig beobachtete Runde verwenden. Pit, Strafe, Off-Track, Teleport und große Aufzeichnungslücken besitzen eigene Gründe im Archiv. Original-AC liefert kein verifiziertes `isValidLap`; die Kennzeichnung ist abgeleitet.

**Pause/Replay produziert keine Runden:** Das ist beabsichtigt. Nur `AC_LIVE=2` wird aufgenommen. Eine Pause ist keine zusätzliche Live-Fahrt.

**Sektoren fehlen:** Die App braucht beobachtete `currentSectorIndex`-Wechsel und `lastSectorTime`. Nach Einstieg mitten im Sektor bleiben unbekannte Zeiten null. Der letzte Sektor kann aus offizieller Rundenzeit minus bekannten Sektoren bestimmt werden. Eine theoretische Runde entsteht erst aus vollständigen gültigen Sektoren.

**Session-Restzeit bleibt leer:** Der Timer wird gegen die tatsächlich laufende Rundenuhr auf Sekunden oder Millisekunden kalibriert. Das braucht mindestens 1,5 s. Ein konstanter, negativer oder nicht plausibel abnehmender Timer bleibt unbekannt; ein undokumentierter Rohwert wird nicht als Minuten ausgegeben.

**Motor/Aero/ABS-Aktivität steht auf N/A:** Für diese Bedeutung gibt es im verifizierten Basisvertrag keinen belastbaren Messwert. Details und Rohkanäle: [DATA_SOURCES.md](DATA_SOURCES.md).

**Reifen zeigen 0:** Das Spiel/der Mod kann Felder unbefüllt als Null liefern. Die App erhält den Rohwert. Grenzwerte passend zur Mischung konfigurieren; ein allgemeines Reifenoptimum wird nicht vorausgesetzt.

**Zu wenige oder unpassende Kurven:** Kurvennummern folgen prominenten Geschwindigkeitsminima. Chicanen oder flüssige Kurven können abweichen. Coach und Marker als lokale Schätzung lesen. Die genaue Regel steht in [ALGORITHMS.md](ALGORITHMS.md).

**Kein Coach-Hinweis:** Eine kompatible Referenz, genügend Messdaten und eine relevante messbare Differenz sind nötig. Ohne belegten Unterschied wird kein allgemeiner Tipp erfunden. Bei gleicher Runde als Referenz ist Delta erwartungsgemäß null.

## Netzwerk und Browser

**Tablet erreicht Server nicht:** LAN aktivieren, Token setzen, Backend neu starten. Richtige Windows-LAN-IP und Port verwenden. Private Windows-Firewallfreigabe prüfen. Gast-WLAN/AP-Isolation kann Geräte voneinander trennen. VPNs können die Route ändern.

**HTTP 401 / Access code required:** Auf dem Handy den 8-stelligen Zugriffscode eingeben; er steht am PC unter Einstellungen → Lokales Netzwerk und im Konsolenfenster. Nach „Neuen Code erzeugen“ auf jedem Gerät neu eingeben. **HTTP 429 / Too many wrong access codes:** 10 verschiedene falsche Codes – 60 s warten (Aufrufe ohne Code zählen nicht). Die Sperre liegt nur im Arbeitsspeicher; ein Neustart der App hebt sie auf. Der Code steht nicht im URL. Direktzugriffe auf die API brauchen `Authorization: Bearer <code>`. Live-Onboard-Video auf dem Handy: siehe [ONBOARD.md](ONBOARD.md).

**WebSocket verbindet nicht:** Gleiches Origin/Scheme/Port verwenden; Reverse Proxys müssen WebSocket-Upgrades und `Sec-WebSocket-Protocol` weiterleiten. Der Client verwendet die Subprotokolle `ac-agent` und den Token. Falsche Origins werden abgewiesen.

**PWA lässt sich im LAN nicht installieren:** Ein HTTP-LAN-Origin ist normalerweise kein sicherer Browserkontext. Vertrauenswürdiges HTTPS mit passendem Zertifikat einsetzen. `localhost` ist auf jedem Gerät sein eigener Rechner. Ohne laufenden Server kann die PWA nur die gecachte Anwendungshülle zeigen.

**Nach Update alte Oberfläche:** Seite neu laden. Falls nötig den Service Worker für diesen Origin und dessen App-Cache in den Browserwerkzeugen entfernen. API-Antworten und Telemetrie werden nicht vom Service Worker gecacht.

## Speicherung, Build und Performance

**Speicherlimit überschritten:** Aktive Session, Referenz und Favoriten sind geschützt. Backup erstellen, Schutz gezielt entfernen oder Limit erhöhen. Die App löscht geschützte Daten nicht erzwungen.

**Import abgelehnt:** Nur eine exportierte Session-JSON-Datei oder das SQLite-Backup dieses Projekts verwenden. Maximal 64 MiB JSON / 2 GiB SQLite / 192 MiB entpackte Telemetrie pro Runde. Quelle, Sample-Reihenfolge, Schema und Fremdschlüssel werden validiert. Eine fehlgeschlagene Wiederherstellung ergänzt keine halbe Session.

**Datenbank manuell sichern:** Vor direktem Kopieren `telemetry.sqlite` die App normal beenden. Im laufenden Betrieb stattdessen die Backup-Schaltfläche verwenden; WAL-Inhalte werden konsistent berücksichtigt. Datenordner: bei der portablen EXE der Ordner `Daten` neben der EXE, sonst `%LOCALAPPDATA%\StintPulse`.

**PyInstaller-Build fehlgeschlagen:** Auf Windows x64 mit Python 3.12 und Lockdateien bauen. Linux erzeugt keine Windows-EXE. `build-windows.ps1` bricht bei Test-/Buildfehlern ab. Für den Installer Inno Setup 6 und `ISCC.exe` auf PATH bereitstellen.

**Erfassung unter 60 Hz:** 60 Hz ist ein Polling-Ziel. Unveränderte Spielpakete werden nicht dupliziert. Tatsächliche Rate hängt von AC und Last ab. CPU/RAM/Dropped unten beobachten. Video abschalten, weniger Diagrammkanäle öffnen und Broadcast-Rate reduzieren. Erfassung und Live-Übertragung sind getrennt; weniger WS-Hz senken Netzwerkbedarf ohne die eingestellte Capture-Rate zu ändern.

**Abruptes Schließen verliert aktuelle Runde:** Noch nicht abgeschlossene Samples liegen im RAM. Mit Strg+C beziehungsweise normalem Server-Shutdown beenden; abgeschlossene SQLite-Transaktionen bleiben dauerhaft gespeichert.

Für nachvollziehbare Fehlerberichte Versionsnummer, Betriebssystem, AC-/Mod-Version, Quellmodus, Status, betroffene Runde und reproduzierbare Schritte notieren. Zugriffscode nicht mitsenden. Ein Export kann Fahrernamen und vollständige persönliche Telemetrie enthalten.
