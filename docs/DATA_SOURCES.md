# Datenquellen und ABI-Vertrag

Geprüft am 03.10.2026 über Firecrawl anhand der Quellimplementierungen. Das Projekt verwendet das Basis-Shared-Memory des **originalen** AC, nicht die anders aufgebauten ACC-Seiten und keine CSP-Erweiterung.

Primärquellen der verwendeten Open-Source-Implementierungen:

1. [Rombiks sim_info.py im CSP-App-Repository](https://github.com/ac-custom-shaders-patch/acc-extension-apps/blob/master/apps/python/AccExtHelper/sim_info.py) – vollständige originale Seiten bis AC 1.16. Der Speichervertrag benötigt CSP nicht; lediglich diese geprüfte Datei liegt in dessen Repository. Der Autor erlaubt freie Verwendung im Dateikopf.
2. [mdjarv Physics.cs](https://github.com/mdjarv/assettocorsasharedmemory/blob/master/Physics.cs), [Graphics.cs](https://github.com/mdjarv/assettocorsasharedmemory/blob/master/Graphics.cs), [StaticInfo.cs](https://github.com/mdjarv/assettocorsasharedmemory/blob/master/StaticInfo.cs) – unabhängige C#-Strukturen, MIT-Lizenz. Das README verweist auf den ursprünglichen [Kunos-Shared-Memory-Thread](https://www.assettocorsa.net/forum/index.php?threads/shared-memory-reference.3352/). Die Strukturen wurden direkt gelesen; der geschützte Forum-Thread wurde nicht als zusätzlich verifiziert ausgegeben.

Ein relevanter Konflikt: mdjarvs ältere `StaticInfo.cs` deklariert `TrackConfiguration` mit 15 UTF-16-Zeichen. Die neuere Python-Quelle hat **33 Zeichen**. Das Projekt verwendet 33; der Test mit einem langen Layout und `ersMaxJ`-Offset 592 schützt vor diesem Fehler. Es übernimmt keine alten Folge-Offsets. In Python sind Reifen-Kontaktvektoren explizit `float[4][3]`; die Dimensionen in der Quelle sind nicht wörtlich als vertauschte Python-Indizes übernommen.

Zusätzlich zu den normalisierten UI-Kanälen werden **alle numerischen Physics-/Graphics-Felder** unter `raw_physics_*` / `raw_graphics_*` erhalten, einschließlich Array-Indizes. Die vollständige Static-Seite steht in den Session-Metadaten unter `raw_static`. Rohfelder haben keine erfundenen Einheiten oder Bedeutungen. Graphics-Zeitstrings sind redundant zu den gespeicherten numerischen Zeitfeldern und werden nicht als eigene Sensoren gespeichert.

## Binärlayout

Little Endian, `_pack_ = 4`, `int32 = 4 Byte`, `float32 = 4 Byte`, `UTF-16-Codeeinheit = uint16 = 2 Byte`. Niemals plattformabhängiges `ctypes.c_wchar` auf Linux benutzen.

| Seite | Windows-Name | Größe | geprüfte Schlüssel-Offsets |
|---|---|---:|---|
| Physics | `acpmf_physics` | 580 Byte | speedKmh 28, clutch 364, brakeBias 564, localVelocity 568 |
| Graphics | `acpmf_graphics` | 296 Byte | iCurrentTime 140, sessionTimeLeft 152, sectorIndex 164, normalizedCarPosition 248 |
| Static | `acpmf_static` | 684 Byte | sectorCount 400, trackSPlineLength 520, trackConfiguration 524, ersMaxJ 592 |

**Seitennamen:** `acpmf_physics`, `acpmf_graphics` (Plural!), `acpmf_static` – identisch in mdjarvs `AssettoCorsa.cs` (`MemoryMappedFile.OpenExisting("Local\acpmf_graphics")`) und in Rombiks `sim_info.py` (`mmap.mmap(0, …, "acpmf_graphics")`). Bis Oktober 2026 verwendete dieses Projekt fälschlich `acpmf_graphic`; dadurch kam nie eine Verbindung zustande (siehe [TROUBLESHOOTING.md](TROUBLESHOOTING.md)). Der Test `test_page_names_match_original_ac` schützt davor.

`OpenFileMappingW(FILE_MAP_READ)` öffnet nur bestehende Seiten; `MapViewOfFile(FILE_MAP_READ)` bildet sie mit Seitenschutz `PAGE_READONLY` ab, `VirtualQuery` liefert die tatsächlich abgebildete Größe. Es werden nie leere Ersatzseiten erzeugt oder als Verbindung ausgegeben. Die Prozessprüfung (`acs.exe`/`acs_x86.exe`) ist reine Diagnose und blockiert keine lesbaren Seiten. Die Static-Seite gilt als initialisiert, sobald `carModel` oder `track` befüllt ist (`acVersion` wird nur angezeigt). Unplausible Einzelwerte (`sectorCount` außerhalb 1–20, `maxRpm` > 50 000, Spline-Länge > 100 km, überlange Namen) werden als `unknown_fields` gekennzeichnet statt den Empfang zu blockieren. Physik/Graphics werden per `packetId` vor und nach dem Kopieren geprüft, Static per vollständigem Doppelvergleich. Die Seiten besitzen voneinander unabhängige Aktualisierungstakte; eine perfekte atomare Aufnahme aller drei Seiten wird nicht behauptet. Originale Versionen mit kürzeren Seiten und ACC sind nicht unterstützte Datenquellen.

## Anzeige und Herkunft

| Daten | Herkunft / Interpretation |
|---|---|
| Speed, RPM, Gas, Bremse, Kupplung | direkte Physics-Felder; Speed km/h, Pedale 0–1 |
| Gang | `gear - 1`: −1 rückwärts, 0 neutral, 1 erster Gang |
| Lenkung | `steerAngle` als **Rohinput**; Grad nur aus Benutzerkalibrierung geschätzt |
| G-Kräfte | `accG[0]` lateral, `[1]` vertikal, `[2]` longitudinal |
| Gierrate / Fahrzeuggeschwindigkeit | `localAngularVel[1]`, `localVelocity[0/2]`; Koordinatensystem-Kalibrierung für Gegenlenken erforderlich |
| Kraftstoff | `fuel`, L; Restreichweite aus gemessenem Verbrauch einer Referenzrunde |
| Zeiten | `iCurrentTime`, `iLastTime`, `iBestTime`, `lastSectorTime` in ms; letzte Sektorzeit aus offizieller Runde minus vorhandene Sektoren |
| Session-Zeit | `sessionTimeLeft` bleibt als Rohwert erhalten. Der AC-Adapter verifiziert s/ms anhand der tatsächlichen Abnahmerate gegen die bekannte ms-Rundenuhr über mindestens1,5 s. Erst danach entsteht `session_left_ms`; ein unbekannter, konstanter oder negativer Timer bleibt null. Keine ACC-abgeleitete Einheit. |
| Strecke / Layout / Fahrer / Auto | UTF-16-Static-Felder; Layout 33 Zeichen |
| Rundendistanz | `normalizedCarPosition * trackSPlineLength`; gemeinsame Spline-Station, nicht tatsächliche Länge jeder unterschiedlichen Fahrlinie |
| Trackmap | Graphics-`carCoordinates`, X/Z; Geometrie einer aufgezeichneten Runde, keine offizielle Strecken-Grafik |
| Reifen | FL, FR, RL, RR; Core, I/M/O, Pressure (psi), Wear raw, Load, Slip raw, Angular Speed (rad/s), Suspension Travel (m) |
| Temperaturstatus | ausschließlich konfigurierte Grenzwerte; kein universelles Mischungsoptimum |
| Bremsentemperaturen | `brakeTemp[4]`; manche Fahrzeugmods füllen das Feld unbrauchbar oder gar nicht |
| Fahrwerk / Balance | `rideHeight[2]`, `cgHeight`, Federweg, Roh-Bremsbalance; keine erfundene Setup-Datei |
| Schäden | alle fünf `carDamage`-Rohwerte; keine unbelegte Umrechnung zu Prozent oder Motor-/Aero-Zustand |
| DRS / KERS / ERS | nur bei passenden `hasDRS`/`hasKERS`/`hasERS`-Static-Flags; Ladung, Rohlevel und kJ aus verifizierten Physics-Feldern |
| ABS / TC | Rohwerte werden gespeichert; **keine zuverlässige Aktivitätsanzeige** daraus konstruiert |
| Luft / Strecke | Physics-`airTemp`, `roadTemp`; Wetterbeschreibung und vollständiges Setup stehen auf null |

Wichtig: Feld vorhanden heißt nicht, dass jeder Mod es sinnvoll befüllt. Null zeigt fehlende Daten, der Gedankenstrich ist deren UI-Darstellung. Ein vom Spiel gelieferter numerischer Nullwert bleibt ein Rohwert und wird nicht automatisch zu einer erfundenen Temperatur oder Aktivitätsflag umgedeutet.

## Nicht verfügbare oder bewusst begrenzte Funktionen

- Motortemperatur, eindeutige Motor-/Aero-Gesundheit, separater Bremsdrucksensor, gemessene Dämpfergeschwindigkeit, eindeutige Bodenberührung und ein separates verifiziertes Oberflächentemperaturfeld sind `null`.
- Der I/M/O-Temperatursatz wird angezeigt, aber nicht als zusätzlich gemessener Oberflächensensor ausgegeben.
- Reifenverschleiß und Schlupf bleiben Rohwerte; ihre modabhängige Skalierung wird nicht erfunden.
- Unter-/Übersteuern und Gegenlenken sind nur kalibrierte Näherungen; ohne erforderliche Eingaben gibt es keine entsprechende Klassifikation.
- Kurvennummern stammen aus Mindestgeschwindigkeitspunkten. Ein Apex ist ein Mindestgeschwindigkeits-Proxy, kein geometrisch bestätigter Randstein-Apex.
- DRS-Aktivierung wird als Kanal erfasst. Eine offiziell erlaubte DRS-Zone lässt sich aus den Basisfeldern nicht sicher rekonstruieren und wird deshalb nicht als Karte erfunden.
- Kein Video aus Shared Memory, keine automatisierte Setup-Änderung, kein LLM und kein externer Telemetrie-Upload.

Diese Grenzen sind Datenvertragsentscheidungen. Sie verhindern, dass eine überzeugende Darstellung ungemessene Werte als Tatsachen ausgibt.
