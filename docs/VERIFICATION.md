# StintPulse 0.1.0 · Release-Prüfung 4. Oktober 2026

Umgebung: Windows 11 Pro (Build 26200), Python 3.12, Node 24.19.0, Inno Setup 6.7.3.

| Prüfung | Ergebnis |
|---|---|
| `scripts/build-windows.ps1` komplett (Frontend-Tests, Build, Backend-Tests, PyInstaller, Smoke-Test, ZIP, Installer, Prüfsummen) | bestanden: 93 Frontend-, 106 Backend-Tests; `StintPulse-0.1.0-Setup.exe` 20,8 MB, `StintPulse-0.1.0-Windows.zip` 25,4 MB, `SHA256SUMS.txt` |
| Installer still installieren (`/VERYSILENT /DIR=…`), installierte EXE Smoke-Test, Deinstallation | bestanden; Dateieigenschaften „StintPulse 0.1.0“, Eintrag in „Apps & Features“, Deinstallation entfernt Programm und Eintrag |
| Zweiter Start der EXE | öffnet nur das Dashboard der laufenden Instanz, berührt keine Aufnahmen |
| Port durch anderes Programm belegt | verständliche Meldung, Fenster bleibt offen, Exit-Code 1 |
| Bestehende Nutzerdaten mit StintPulse 0.1.0 (portabler Ordner `Daten`) | 5 Sitzungen geladen; zwei unterbrochene Aufnahmen (Baku 19 min, Silverstone 26 min) automatisch abgeschlossen; Spielsound in echten Fahrten: z. B. 788 von 905 s mit Ton |
| Download-Seite `website/index.html` | Desktop und 390 px ohne horizontales Scrollen |

**Nicht geprüft:** GitHub-Actions-Workflow und Release-Veröffentlichung (Repository existiert noch nicht), Update-Hinweis gegen ein echtes Release, Installation auf anderen PCs/Windows-Versionen, Code-Signatur (nicht vorhanden – SmartScreen-Warnung möglich).

---

# Onboard-Ton · Prüfung 4. Oktober 2026

Der Ton wird vom PC-Programm per WASAPI-Loopback aufgenommen (die OBS Virtual Camera hat keinen Ton). Assetto Corsa lief bei dieser Prüfung nicht.

| Prüfung | Ergebnis |
|---|---|
| Echte Windows-Aufnahme „Spielsound“ (Prozess-Loopback) eines Testprozesses mit leisem 440-Hz-Ton (Windows 11, Build 26200) | aufgenommen, 440 Hz, Pegel wie abgespielt, Einsatz 40–60 ms nach dem Abspielbefehl (Startlatenz von Windows) auf der gemeinsamen Uhr |
| Isolation: Prozess-Loopback eines stillen Prozesses, während ein anderes Programm den Ton spielt | Pegel 0 – fremder Ton wird nicht aufgenommen |
| Echte Windows-Aufnahme „Gesamter PC-Ton“ | aufgenommen, 440 Hz, Einsatz ≈ 40 ms |
| Zeitachse: Stille-Lücken, Überlappung, Auffüllen bis Abschnittsende | Backend-Test bestanden (Sample-Position = PC-Uhr ± 0,1 ms) |
| Absturz während der Aufnahme | WAV-Kopf repariert, Ton abspielbar (Backend-Test) |
| Wiedergabe im Browser (Chromium, Test-Ton statt Systemton) | Ton läuft bei 2× synchron zum Bild (< 0,1 s), pausiert am Rundenende; Range-Anfragen 206 |
| Opt-in-Test mit echtem Ton | `AC_AGENT_AUDIO_TEST=1 pytest tests/test_audio.py -k windows` (spielt einen kurzen leisen Testton) |

**Noch auf dem PC des Nutzers zu prüfen:** Spielsound während einer echten AC-Fahrt (acs.exe), Lautstärke/Klang, A/V-Versatz zur echten OBS-Latenz.

---

# Onboard-Aufnahme mit Rundenwiedergabe · Prüfung 4. Oktober 2026

Umgebung: Windows 11 Pro, Python 3.12, Node 24.19.0, Playwright-Chromium 1243 (headless). **Assetto Corsa und OBS liefen bei dieser Prüfung nicht.** Videoquelle war Chromiums synthetische Testkamera (`--use-fake-device-for-media-stream`), Telemetrie die Demo-Quelle im Zeitraffer (`AC_AGENT_DEMO_RATE=6`, nur Tests) – getrennte, ausdrücklich synthetische Testdaten.

| Abnahmepunkt | Nachweis | Ergebnis |
|---|---|---|
| 1. Neue Fahrt erzeugt eine abspielbare lokale Videodatei | Browser-Test: Aufnahme → `status ready`, indiziert, Range-Anfrage `206`, `<video>` mit Bildbreite > 0; Backend: Indizierung ohne Neukodierung, Bytes jedes Frames identisch | bestanden |
| 2. Zwei aufeinanderfolgende Runden → richtige Videoabschnitte | Browser-Test: Ende Runde n = Beginn Runde n+1 auf der Videozeitachse (< 50 ms); Backend-Test mit festen Zeiten 1,0–4,0 s / 4,0–7,5 s | bestanden |
| 3. „Onboard ansehen“ startet bei der gewählten Runde | Browser-Test: Startposition = Rundenbeginn | bestanden |
| 4. Wiedergabe endet am Rundenende | Browser-Test bei 2×: Stopp ±50 ms am Rundenende, Rundenuhr = offizielle Rundenzeit | bestanden |
| 5. HUD synchron beim Spulen und bei 2× | Browser-Test: zwei Sprungziele (pausiert) und Wiedergabe 2×: HUD-Rundenuhr = Telemetrie an derselben Videoposition (< 60 ms Spieluhr bei 6-fachem Zeitraffer) | bestanden |
| 6. Navigation unterbricht die Aufnahme nicht | Browser-Test: alle fünf Bereiche durchgeklickt, Aufnahme lief durch, ein einziger Abschnitt | bestanden |
| 7. Fehlende Videoquelle und Videolücken | Browser-Test „VIDEOQUELLE FEHLT“ bei laufender Telemetrie; Unit-Test Quellenausfall → neuer Abschnitt; Backend-Test Lücke → „teilweise“, nie „vollständig“ | bestanden |
| 8. Aufnahmen nach Neustart verfügbar | Backend-Test: neue App-Instanz auf demselben Datenordner liefert Aufnahme und Datei; offener Abschnitt wird als „unterbrochen“ abgeschlossen | bestanden |
| 9. Alte Sessions ohne Video | Backend- und UI-Test: „Kein Video“, Telemetrie/Analyse unverändert | bestanden |
| 10. Build und Tests | Backend 93 bestanden (1 Linux-Test übersprungen), Frontend 91, Browser 6, TypeScript- und Vite-Build, EXE-Smoke-Test | bestanden |

Zusätzlich gemessen: MediaRecorder-MP4 (Chromium) meldet nur die Dauer des ersten Fragments und springt beim Spulen auf 0 → nicht verwendet. Rohes MediaRecorder-WebM hat keine Dauer; nach der Indizierung korrekte Dauer und exaktes Spulen über Range-Anfragen. Eine Auflösungsbegrenzung über geklonte Kamerakanäle wirkte im Test nicht; die Qualitätsstufe stellt deshalb nur die Bitrate ein.

**Noch nicht geprüft (Live-Test auf dem PC des Nutzers nötig):** Aufnahme der echten OBS Virtual Camera während einer echten AC-Fahrt, tatsächliche OBS-Latenz (Video-Versatz), CPU-Last des Encoders während des Spiels, sehr lange Aufnahmen (> 1 h) und das Verhalten bei vollem Laufwerk auf echter Hardware.

---

# Verbindungsreparatur · Windows-Prüfung mit laufendem Assetto Corsa (3. Oktober 2026)

Umgebung: Windows 11 Pro 10.0.26200 x64, Python 3.12.10 (64 bit), Node 24.19.0, originales Assetto Corsa 1.16.4 (Shared-Memory-Version 1.7), gestartet über Content Manager. Ob CSP installiert war, wurde nicht geprüft; der Reader nutzt ausschließlich die drei Basisseiten des Spiels und keine CSP-Erweiterung. Session: `cim_2006_renault` auf `chq_sepang / f1_2026`, Practice, Status `AC_LIVE`.

| Prüfung | Ergebnis |
|---|---|
| Ursprünglicher Code gegen laufendes AC | **0 Samples in 4 s**, Meldung „Waiting for AC: enter a driving session“ |
| `OpenFileMappingW("acpmf_graphic")` | fehlt (Windows-Fehler 2) – falscher Name im Originalpaket |
| `OpenFileMappingW("acpmf_graphics")` | vorhanden |
| `scripts/check_ac_live.py --seconds 8` (korrigiert) | `ok: true`, 461 Samples, 57,6 Hz, alle drei Seiten vorhanden/lesbar/initialisiert/`PAGE_READONLY`, 580/296/684 Byte erwartet = gelesen, Prozess `acs.exe` gefunden, Launcher `content manager.exe` |
| App (`python -m ac_agent --ac`) + `/api/diagnostics` | Status `live`, Empfangsrate 60,5 Hz, 0 Erfassungsfehler, 0 verworfene Samples |
| Dashboard im Browser | „BACKEND VERBUNDEN“ und „TELEMETRIE VERBUNDEN“ getrennt; echte Werte (Auto, Strecke, Drehzahl, Pedale); Diagnoseseite vollständig |
| Wechsel AC → Demo → AC im Dashboard | Demo als „DEMO AKTIV“ (orange), danach Demo-Werte entfernt und wieder echte Spieldaten |
| Backend-Tests | 51 bestanden, 1 Linux-spezifischer Test übersprungen (inkl. Windows-Integrationstest mit eindeutigen Test-Mapping-Namen) |
| Frontend-Tests | 16 bestanden; TypeScript- und Vite-Produktionsbuild erfolgreich |

Nicht am echten Spiel geprüft (nur durch Tests mit simulierten Seiten abgedeckt): eine vollständig gefahrene Runde, Pause/Fortsetzen, Replay, Sessionwechsel sowie Spielende und Neustart. Das Auto stand während der Prüfung (Geschwindigkeit ≈ 0 km/h, Leerlauf 2350 U/min). Ein bestandener Demo- oder Fixture-Test ist kein Nachweis einer Spielverbindung.

---

# Prüfprotokoll · 3. Oktober 2026

Geprüft wurde die lauffähige Anwendung mit ihrem echten FastAPI-/WebSocket-/SQLite-Pfad und der ausdrücklich synthetischen Demo-Quelle. Die Umgebung war Linux x86_64, Python 3.12.14, Node 24.19.0 und Chromium. In dieser Umgebung sind weder das Spiel noch ein Windows-Kernel verfügbar. Die Windows-Abnahme ist deshalb separat aufgeführt.

## Ausgeführte Prüfungen

| Prüfung | Ergebnis |
|---|---|
| Backend-Testlauf | 34 bestanden, 1 Windows-spezifischer Test übersprungen |
| Speicherung nach Optimierung der JSON-Batches | alle 12 betroffenen API-/DB-/Worker-Tests bestanden |
| Frontend-Unit-/Komponententests | 9 bestanden |
| Chromium-Integrationstests | 4 bestanden: Live/Analyse/Mobile, Desktop-Auflösungen, Archiv/CSV, mehrteiliger Session-Replay |
| TypeScript und Vite-Produktion | erfolgreich; Build liegt in `frontend/dist` |
| PyInstaller-Spec auf Linux | nativer Build und Smoke-Test erfolgreich: verpackte Demo, gespeicherte Runden und ausgelieferte Dashboard-Assets; kein Windows-Build |
| entpacktes Download-Archiv | separater Start aus dem entpackten Quellcode: Live-Demo, vier Reifen, zwei gespeicherte Runden, acht Kurven, zwei berechnete Tipps, Trackmap, CSV und beide Frontend-Assets geprüft |
| Browser-Laufzeit | keine JavaScript-Laufzeitfehler in der geprüften Demo-/Analyse-/Tablet-Navigation |
| Responsive Screenshots | 1920 × 1080, 1024 × 768 und 390 × 844; Smartphone ohne horizontalen Overflow |
| Desktop-Layout | 1920 × 1080, 2560 × 1440 und 3440 × 1440 zusätzlich im Browser auf Overflow und sichtbare Widgets geprüft |
| Vollständige Live-Demo-Runde | innerhalb der 100-s-Messung automatisch gespeichert; Archiv von 2 auf 3 Runden erweitert |
| Verluste bei Erfassung | 0 verworfene Samples in beiden dokumentierten Messungen |
| ABI | unabhängig gepackte Physics-/Graphics-/Static-Fixtures mit 580 / 296 / 684 Byte dekodiert |
| CSV, JSON, Import und SQLite-Backup | persistente Daten, neue Import-IDs, atomare Zurückweisung falscher Quellen, Token aus Backup entfernt |
| LAN-Zugriff | Host/Origin-, Bearer-/WebSocket-Token- und Eingabeprüfungen automatisiert getestet |
| Langsame Analyse / Sessionwechsel | Live-Frame bleibt lesbar; fertige Runde bleibt ihrer ursprünglichen Session zugeordnet |
| Speicherbereinigung | entfernt nur so viele ungeschützte Sessions wie nötig; berücksichtigt freigewordene Seiten und WAL |

Eine Starlette-TestClient-Deprecation-Warnung betrifft den Alias für AnyIOs BlockingPortal; die Prüfungen bestehen. Sie ist kein Spiel- oder Telemetriefehler.

## Gemessene Performance

Ein Loopback-WebSocket-Client, 60-Hz-Capture-Ziel und 20-Hz-Broadcast-Ziel. Startup/Generierung der zwei Referenzrunden werden vor der Messung abgewartet. `scripts/benchmark.py` ermittelt Prozess-CPU/RAM, tatsächlich empfangene Frames, Datenvolumen und Sample-Zähler. CPU 100 % bedeutet einen vollständig ausgelasteten logischen Kern. Messung in einer geteilten Entwicklungsumgebung; kein kontrollierter Hardwarevergleich.

| Größe | 30 s ohne Rundenabschluss | 100 s einschließlich Rundenabschluss |
|---|---:|---:|
| Erfassung | 60,00 Hz | 59,99 Hz |
| WebSocket | 19,58 Hz | 19,42 Hz |
| Telemetrievolumen | 136,23 KiB/s | 134,35 KiB/s |
| mittlere Backend-CPU | 4,92 % eines Kerns | 7,76 % eines Kerns |
| beobachteter RAM-Peak | 236,77 MiB | 274,74 MiB |
| verworfene Samples | 0 | 0 |
| größter Abstand zwischen WS-Nachrichten | 54,43 ms | 137,01 ms |
| Datenbank nach Messung | 6,75 MiB | 10,04 MiB |

Rohberichte: [performance-demo.json](performance-demo.json) und [performance-stint.json](performance-stint.json). Die kurze Messung entstand vor der letzten Speicheroptimierung; die 100-s-Messung verwendet den separaten Rundenanalyse-Worker und begrenzte JSON-Kompressionsbatches. Das größte beobachtete WS-Intervall liegt über dem nominalen 50-ms-Takt; es wird keine harte Echtzeitgarantie gegeben.

Die Messung schließt Browser-/GPU-/Video-Kosten nicht ein. Original-AC liefert zusätzliche Rohkanäle und kann größere Frames erzeugen. Auswirkungen auf Spiel-FPS, Tablet-WLAN und echte Capture-Latenz wurden hier nicht gemessen. 60 UI-FPS bleiben ein Ziel; eine Hardwaremessung auf dem vorgesehenen Windows-Rechner ist erforderlich.

## MVP-Abnahme und Plattformgrenzen

| Kriterium | Stand und Nachweis |
|---|---|
| ohne Spiel starten | Demo-Server und gebaute Oberfläche im Browser ausgeführt |
| echte AC-Datenquelle | lesender Windows-mmap-Adapter implementiert, ABI anhand etablierter Quellen geprüft; echter Spielbetrieb hier nicht ausführbar |
| automatische Live-Updates | WebSocket-/React-Tests und Browserprüfung ohne Reload |
| Runden-/Sektorwechsel | aufgezeichnete synthetische Fixtures, Reset-/Partial-/Off-Track-/Pit-Fälle und vollständiger Live-Demo-Umlauf |
| dauerhafte Runden | SQLite-Reopen, Import/Export, Backup/Restore und Live-Demo-Archiv geprüft |
| mindestens zwei Runden vergleichen | gemeinsame 5-m-Achse, Delta, freie Referenz, bis zu vier passende Runden |
| Trackmap erzeugen | aus gemessenen/synthetischen X/Z-Koordinaten, gespeichert und mit Cursor/Live-Position gekoppelt |
| vier Reifen | FL/FR/RL/RR, Rohwerte/Temperaturen/Druck, konfigurierbare Warnschwellen |
| datenbasierter Coach | gemessene Kurvenunterschiede, lokale Regeln, maximal fünf Hinweise, Ursachen als Hypothesen |
| fehlende Werte | null / N/A statt erfundener Motor-, Aero-, ABS-/TC-Aktivität oder Video-Daten |
| Backend-/Frontend-Tests | oben genannte automatisierte Prüfungen enthalten und ausgeführt |
| Windows-Distribution | Lockdateien, PyInstaller-Spec, Inno Setup und Windows-CI vorhanden; Windows-EXE-/Installer-Ausführung hier ausstehend |
| vollständige Einrichtung | README, Benutzerhandbuch, Quellen, Algorithmen, Architektur, Troubleshooting und OBS-Anleitung |

Es handelt sich um einen echten lokalen Datenpfad mit einem zusätzlichen Demo-Modus. **Die tatsächliche Verbindung zum laufenden originalen AC und die Windows-EXE werden erst auf Windows endgültig abgenommen.** Dafür ist `scripts/check_ac_live.py` enthalten; der Windows-Build führt zusätzlich einen EXE-Smoke-Test aus.

## Bewusste Grenzen dieses Release

- Basis-AC, keine erfundenen CSP-Erweiterungen. Wetter/Setup, Motorzustand, gesonderte Aero-Schäden, Bremsdruck, echte Dämpfergeschwindigkeit und verlässliche ABS-/TC-Aktivität bleiben unbekannt, sofern der verifizierte Vertrag sie nicht liefert.
- Rundengültigkeit wird abgeleitet. Automatische Kurvenlabels, Apex über Mindestgeschwindigkeit, Lock-/Spin- und Balance-Ereignisse sind ausdrücklich Näherungen. Die fahrzeugspezifische Lenk-/Gierratenkalibrierung ist optional und standardmäßig leer.
- Temperatur-/Druckgrenzen gelten für die aktuelle Konfiguration; automatische Profile pro Fahrzeug/Mischung sind nicht enthalten. Die Grenzwerte vor einem Fahrzeugwechsel prüfen.
- Onboard verwendet Browser-Freigabe oder eine separat gewählte HTML5-Videoquelle/-Datei. OBS-Einrichtung ist dokumentiert; kein eigener nativer WGC-, OBS-WebSocket-, NDI- oder WebRTC-Transport. Tablet-Telemetrie funktioniert unabhängig vom Video.
- Replay-Video wird manuell über Offset synchronisiert; Video liegt nicht im Session-Backup. Browser-PiP und Capture benötigen Browserunterstützung/Berechtigung.
- Die laufende Companion-App verbindet sich beim Erkennen des Spiels automatisch. Ein separater Betriebssystem-Dienst, der eine zuvor nicht gestartete App beim Spielstart startet, ist nicht enthalten. Windows-Autostart ist in der gepackten EXE manuell aktivierbar.
- PWA-Cache enthält nur die App-Hülle; keine vollständige Offline-Analyse ohne Backend. LAN-HTTP ist unverschlüsselt; vertrauenswürdiges HTTPS ist über Zertifikat/Schlüssel möglich. Eigene DNS-Hostnamen sind nicht in der Host-Allowlist; für LAN die private IP verwenden.
- Die KI-Setup-Analyse (optional, standardmäßig aus) ist der einzige Pfad, der Daten nach außen sendet. Sie sendet nur zusammengefasste Kennwerte nach Zustimmung und auf Klick. Ihre Antwortverarbeitung ist mit kontrollierten Testantworten und einem Test-Client geprüft, ohne echten KI-Aufruf. Lokale Analysen benötigen keine Cloud. Parquet erfordert das ausdrücklich optionale Paket und ist nicht Teil der Standard-EXE.

## Reproduktion auf Windows

1. `scripts/install.ps1` ausführen und `scripts/start.ps1 -Demo` starten.
2. Dashboard unter `http://localhost:8765` öffnen, Analyse sowie Session-Wiedergabe prüfen und eine komplette Demo-Runde laufen lassen.
3. Tests und Benchmark wie in der README ausführen.
4. Original-AC in eine Fahrsession bringen; `scripts/check_ac_live.py --seconds 8` ausführen, anschließend im AC-Modus mindestens zwei vollständige Runden fahren. Offizielle Zeiten, Sektorwechsel und Archiv mit dem Spiel vergleichen.
5. `scripts/build-windows.ps1` für EXE/Smoke-Test, optional `-Installer` für Inno Setup ausführen. Nur hier entsteht eine Windows-Distribution; PyInstaller ist kein Cross-Compiler.
6. LAN/Token auf dem eigenen privaten Netzwerk sowie eine tatsächliche Onboard-Quelle separat prüfen.
