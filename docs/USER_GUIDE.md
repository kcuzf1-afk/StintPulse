# Benutzerhandbuch

## Erste Fahrt

Nach Installation gemäß README `scripts/start.ps1 -Demo` ausführen und `http://localhost:8765` öffnen. Der orange Hinweis **SYNTHETIC DEMO** kennzeichnet erzeugte Daten. Zwei gespeicherte Demo-Runden erlauben sofort einen Vergleich. Für echte Daten **AC verbinden** wählen und das originale Assetto Corsa in einer Fahrsession öffnen. Die Verbindung funktioniert ausschließlich auf Windows.

Die App erkennt das Spiel automatisch, solange ihr Backend läuft. **Mit Windows starten** ist eine optionale Einstellung der verpackten EXE. Es gibt keinen zusätzlichen Hintergrunddienst, der eine vollständig beendete App selbstständig bei Spielstart startet.

Die erste angebrochene Runde wird als unvollständig gespeichert. Ab der nächsten vollständigen Runde können persönliche Bestzeiten entstehen. Pause und Spiel-Replay werden angezeigt, aber nicht als neue Live-Runden aufgezeichnet. **REC**, **PAUSED**, **GAME REPLAY** und **WAITING FOR AC** machen den Zustand sichtbar. **SAVE OFF** bedeutet, dass aktuell keine Live-Samples gespeichert werden.

## Arbeitsbereiche

| Bereich | Zweck |
|---|---|
| Live-Dashboard | die laufende Fahrt: Session, aktuelle/beste/letzte Runde, Delta, Kraftstoff mit Reichweite, Tempo/Gang/Drehzahl/Pedale, Streckenkarte, Reifen, Sektoren, Warnungen, kompakte Live-Telemetrie; weitere Fahrzeugdaten aufklappbar |
| Analyse | **Vergleich:** Rundenvergleich aus gespeicherten Sessions mit Diagrammen über die Rundendistanz, Delta, Sektoren, theoretischer Bestzeit, Karte und Engineering-Coach. **Setup:** Setup-Assistent, siehe [Setup-Assistent](SETUP_ASSISTANT.md). Funktioniert ohne laufendes Spiel. |
| Onboard | großes Video mit Rundenzeit-HUD, optional Tacho (Tempo, Gang, Pedale); Videoquelle, HUD, Vollbild; Infos-Seitenpanel (standardmäßig zu); Wiedergabe gespeicherter Runden |
| Sessions | Archiv: Suche, Filter, Rundenauswahl, Import/Export, Backup; **In Analyse vergleichen** übergibt zwei Runden an die Analyse |
| Einstellungen | Allgemein · Daten und Speicherung · Onboard und HUD · Netzwerk · Erweitert / Diagnose |

Alte Adressen funktionieren weiter: `#/engineering`, `#/live-mode`, `#/tablet` führen zum Live-Dashboard, `#/diagnostics` zu Einstellungen → Erweitert / Diagnose. Der frühere Tablet-Bereich ist entfallen; das Live-Dashboard passt sich selbst an Handy- und Tabletbreiten an (LAN-Zugriff unverändert).

Kopfzeile: vier getrennte Zustände – **Backend** (Browser ↔ PC-Programm), **Assetto Corsa** (Spielstatus), **Telemetrie** (kommen neue Messwerte an?) und im Onboard **Video**. Werden keine neuen Werte mehr empfangen, steht „Letzter Stand HH:MM:SS“ und die Werte werden abgeblendet – alte Werte erscheinen nie als Live-Daten. CPU-, RAM- und Datenvolumen-Anzeigen stehen nur noch unter Erweitert / Diagnose; dort auch die technischen Fehlerdetails. Fehlermeldungen in der Oberfläche sind verständlich formuliert und nennen eine Handlung.

## Runden vergleichen

1. **Sessions** öffnen, eine oder zwei Sessions auswählen.
2. Zwei vollständige Runden markieren (auch aus zwei Sessions).
3. **In Analyse vergleichen** wählen. Die App prüft Quelle, Fahrzeug, Strecke und Layout; nicht passende Runden lassen sich nicht übergeben. Die schnellere gültige Runde wird Referenz. In der Analyse lassen sich Referenz und Vergleich jederzeit innerhalb der kompatiblen Runden umstellen.
4. In Analyse die Referenz frei ändern. **Live-Referenz setzen** übernimmt sie für den laufenden Fahrbetrieb; **Auto PB** verwendet wieder die beste persönliche gültige Runde.
5. Kanäle über die Schaltflächen oder die Kanalliste aktivieren. Im Diagramm mit dem Mausrad zoomen und ziehen, um zu verschieben. Alle X-Achsen und Cursor sind gekoppelt.
6. Start und Ende eines Abschnitts in Metern eintragen, oder eine Kurve/Trackmap-Markierung anklicken.

Die gemeinsame Distanz ist die Streckenspline-Station, nicht die unterschiedlich lange gefahrene Linie. Die Analyse interpoliert auf ein 5-m-Raster. `Delta = Rundenzeit an dieser Station − Referenzzeit an dieser Station`: negativ ist schneller, positiv langsamer. Große Aufzeichnungslücken bleiben leer. Am Cursor zeigen die oberen Werte und Reifen den ausgewählten gespeicherten Datenpunkt. **DEMO REVIEW** beziehungsweise **RECORDED AC** kennzeichnet diese Ansicht; neue Live-Daten ändern ihre Werte nicht. Zurück zu Engineering zeigt wieder die aktuelle Fahrt.

Die cyan gestrichelte Linie ist die Referenz. Zusätzliche Vergleichsrunden haben eigene Farben. Die Trackmap zeigt gemessene Fahrwege, den aktuellen Cursor und die Ghost-Position zur gleichen verstrichenen Zeit. Der Linienabstand in Metern ist ein Vergleich gemessener Positionen, keine bestätigte Ideallinie.

## Karte und Kurven

Vor der ersten vollständigen Runde baut die App die Geometrie fortlaufend auf. Danach wird eine Karte gespeichert. Start/Ziel folgt dem Spline-Übergang. Sektorgrenzen stammen aus beobachteten offiziellen Sektorwechseln. Nummerierte Kurven entstehen aus Geschwindigkeitsminima und können von offiziellen Kurvennummern abweichen.

Layer schalten Brems-/Gaszonen, Schaltpunkte und Ereignisse ein. Grün zeigt lokalen Zeitgewinn, Gelb kleinen, Orange größeren und Rot deutlichen lokalen Verlust. Grau bezeichnet fehlende Vergleichsdaten. Eine rote Stelle ist kein Beweis einer bestimmten Fehlerursache. Mit Stern markierte Blockier-/Wheelspin-/Stabilitätsereignisse sind Schätzungen. DRS-Zonen werden nicht erfunden.

## Reifen und Fahrzeug

FL = vorne links, FR = vorne rechts, RL = hinten links, RR = hinten rechts. Die große Zahl ist die Kerntemperatur; darunter stehen Druck und Innen-/Mittel-/Außentemperatur. **Details anzeigen** öffnet Last, Federweg, Rohverschleiß, Schlupf, Raddrehzahl und Bremsentemperatur.

Der konfigurierbare Startbereich 70–105 °C beschreibt nur die eingestellten Schwellen. Für Fahrzeug und Mischung passende Werte setzen. Druckschwellen werden in psi konfiguriert; die metrische Hauptanzeige zeigt bar. Primäre Geschwindigkeits-, Kraftstoff- und Reifenanzeigen wechseln bei imperialen Einheiten zu mph, gal und °F. Technische Rohkanäle behalten ihre beschrifteten Originaleinheiten.

**— / N/A / Nicht verfügbar** ist ein unbekannter Messwert. Original-AC liefert keine verifizierte Motortemperatur, eindeutige Aero-/Motorgesundheit oder ABS-/TC-Aktivitätsflags. Schadenwerte, Verschleiß und ABS/TC bleiben ausdrücklich Rohwerte. Ein vom Mod gelieferter Nullwert kann auch eine unbefüllte Messgröße sein; die App erfindet keinen Ersatz.

Unter-/Übersteuerschätzungen benötigen bekannten Radstand, Lenkübersetzung und kalibrierten Winkel bei Input 1. Gegenlenken benötigt zusätzlich das Vorzeichen zwischen Lenkung und Gierrate. Leere Kalibrierung deaktiviert diese Klassifikationen. Algorithmen und Grenzen: [ALGORITHMS.md](ALGORITHMS.md).

## Coach und Sektoren

Nach jeder vollständigen Runde werden höchstens fünf relevante Hinweise aus den Daten erzeugt. Jeder nennt Position, gemessenen Unterschied, mögliche Ursache, konkrete Handlung und Vertrauen. Ein ausgewiesenes Zeitpotenzial ist der **beobachtete Abschnittsverlust als Obergrenze**, kein versprochener Gewinn. Temperaturhinweise besitzen kein erfundenes Zeitpotenzial. Die Ursache bleibt eine Hypothese.

Der Vergleichs-Coach wird bei einer neuen Referenz erneut berechnet. Theoretische Runde und beste Mini-Sektoren kombinieren die besten gültigen Teilzeiten; diese Kombination muss nicht in einer realen Runde fahrbar sein. Archivdetails zeigen Mittelwert, Standardabweichung, Ausreißer sowie Kraftstoff-/Reifenentwicklung. Die Kraftstoffkorrelation erscheint erst ab acht geeigneten Runden und ausreichender Kraftstoffspanne; sie beweist keine Kausalität.

## Wiedergabe und Video

**Onboard-Video einer Runde ansehen:** Mit „Onboard automatisch aufnehmen“ (Einstellungen → Onboard und HUD) speichert die App während jeder Fahrt das OBS-Kamerabild zusammen mit der Telemetrie. In **Sessions** zeigt die Spalte **Video** je Runde „Video verfügbar“, „Video teilweise · NN %“, „Video wird noch gespeichert …“ oder „Kein Video“; **Onboard ansehen** öffnet die Wiedergabe genau dieser Runde mit dem HUD aus den gespeicherten Daten. Ältere Sessions ohne Video funktionieren unverändert. Details: [ONBOARD.md](ONBOARD.md#onboard-automatisch-aufnehmen-video-pro-session-runde-ansehen).

**Telemetrie-Wiedergabe ohne Video:** Im Archiv startet das Play-Symbol eine gespeicherte Runde. **Session wiedergeben** spielt die vollständigen Runden der ersten ausgewählten Session nacheinander ab. Die Timeline steuert Telemetrie, Karte und eine separat geladene Aufnahme. Pause, Neustart, Scrubbing und Exit replay sind verfügbar.

Eine lokale Videodatei bleibt im Browser und wird nicht in SQLite kopiert. Ihr Offset wird in den Einstellungen gespeichert. Für eine Session-Aufnahme ist der Bezug die erste wiedergegebene vollständige Runde; zeitliche Abstände der folgenden Runden werden aus den aufgezeichneten Zeitstempeln berücksichtigt. Datei und Offset müssen manuell gewählt werden. Audio ist standardmäßig stumm. Einrichtung und Synchronisationsbeispiel: [ONBOARD.md](ONBOARD.md).

## Archiv, Backup und Speicherlimit

CSV/JSON exportieren die Telemetrie einer Runde. Session-JSON enthält die komplette Session mit allen Runden, Metadaten, Ereignissen und Coach-Hinweisen. Der Import fügt neue IDs hinzu; er vermischt keine Datenquellen. Daten sind persönliche Fahrdaten und können den Fahrernamen enthalten.

**Backup** lädt einen konsistenten SQLite-Snapshot aller Sessions und Maps. Einstellungen und Zugriffscode werden daraus entfernt. **Wiederherstellen** validiert und ergänzt das vorhandene Archiv atomar; bestehende Sessions werden nicht überschrieben. Wiederholt importierte Backups erzeugen absichtlich neue Kopien. JSON ist auf 64 MiB begrenzt, SQLite auf 2 GiB; eine einzelne entpackte Runde maximal 192 MiB.

Favoriten, aktive Session und gewählte Referenz sind vor automatischer Bereinigung geschützt. Das Speicherlimit entfernt ältere ungeschützte Sessions. Eine gelbe Speicheranzeige bedeutet, dass geschützte Daten das Limit überschreiten. Favoriten vor wichtigen Stints setzen und regelmäßig ein Backup exportieren. Sessions können im Archiv gelöscht werden; die aktive Session ist geschützt. Ein Löschen wird nicht durch eine separate Undo-Funktion zurückgenommen.

## Datenschutz und Verbindung

Grundfunktionen sind lokal, es gibt keinen Telemetrie-Upload. Die KI-Setup-Analyse ist optional und standardmäßig aus. Sie sendet erst nach Bestätigung der Datenübersicht und nur auf Klick zusammengefasste Kennwerte, Setup-Werte und deine Rückmeldung an den eingestellten Anbieter, aber keine Rohtelemetrie, Videos, Ton, Namen oder IDs. Der API-Schlüssel bleibt verschlüsselt im PC-Programm ([Setup-Assistent](SETUP_ASSISTANT.md)). Externe Video-URLs werden nur nach manueller Auswahl vom Browser geladen. LAN ist standardmäßig aus; Aktivierung benötigt Token und Neustart. HTTP überträgt auch den Token unverschlüsselt, HTTPS ist über `--cert` und `--key` möglich. PWA und Bildschirmfreigabe benötigen localhost oder vertrauenswürdiges HTTPS. Details in der [README](../README.md).
