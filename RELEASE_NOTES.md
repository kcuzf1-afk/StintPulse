# StintPulse 0.2.0

**Deutsch**
- Neu: **Setup-Assistent** unter Analyse → Setup.
  - Ablauf: vorhandenes AC-Setup wählen oder importieren, Runden auswählen, Ziel und Fahrverhalten beschreiben, Empfehlungen prüfen.
  - Höchstens drei Änderungen. Jede mit Beobachtung, möglicher Ursache, Testvorschlag, Begründung, erwarteter Wirkung, Nachteilen und Konfidenz.
  - Export als **neue** Setup-Datei: zuerst Download, optional Speichern in den AC-Setupordner. Nie überschreiben, nie automatisch laden.
  - Versionen, manuelle Zuordnung von Sessions und Vorher/Nachher-Vergleich.
- Grenzen und Kodierung:
  - Einstellgrenzen kommen aus der setup.ini des Fahrzeugs.
  - Gepackte Fahrzeugdaten (data.acd) werden nicht entschlüsselt. Ohne setup.ini bleiben Parameter „unbestätigt“, und der Export ist gesperrt. Die setup.ini lässt sich importieren.
  - Wie ein Wert in der Datei steht (Wert, Klick, Zehntelgrad), wird aus deinen gespeicherten Setups abgeleitet oder von dir bestätigt.
- Die KI-Analyse mit **Claude** (Anthropic, eigener API-Schlüssel) ist optional und standardmäßig aus.
  - Vor der ersten Nutzung siehst du, welche Daten gesendet werden. Rohtelemetrie, Videos, Ton, Namen und IDs werden nie gesendet.
  - Der Schlüssel bleibt verschlüsselt im PC-Programm.
  - Jede Antwort wird außerhalb der KI geprüft: Grenzen, Schritte, Kodierung, höchstens drei Änderungen, keine Zeitversprechen.
  - Ohne KI gibt es eine klar gekennzeichnete regelbasierte Analyse.
- Telemetrie-Kennwerte über mehrere Runden. Out-/Inlaps, Unfälle, Dreher, Datenlücken und Zeitausreißer werden sichtbar ausgeschlossen. Messung und Rückmeldung werden abgeglichen (stützt / widerspricht eher / nicht eindeutig / nicht messbar).

Hinweis: Das Programm ist (noch) nicht digital signiert. Windows SmartScreen kann warnen: „Weitere Informationen → Trotzdem ausführen“. Prüfsummen: `SHA256SUMS.txt`.

**English**
- New: setup assistant (Analysis → Setup).
  - Up to three validated, explained setup changes from your laps and feedback.
  - Exported as a new setup file, with versions and before/after comparison.
- AI analysis with Claude is optional (your own API key), off by default and consent-based. Every answer is checked deterministically outside the model.
- A labelled rule-based analysis works without AI.

StintPulse ist ein unabhängiges Projekt und nicht mit Kunos Simulazioni verbunden. Assetto Corsa ist eine Marke von Kunos Simulazioni.

---

# StintPulse 0.1.1

**Deutsch**
- Behoben: Auf dem Handy startete „Onboard ansehen“ bei jeder Runde am Anfang des Session-Videos (also bei der ersten Runde). Jetzt startet jede Runde an ihrem eigenen Beginn – auch wenn der Handy-Browser den ersten Sprung verwirft.

**English**
- Fixed: on phones, "Watch onboard" started every lap at the beginning of the session video. Each lap now starts at its own beginning.

StintPulse ist ein unabhängiges Projekt und nicht mit Kunos Simulazioni verbunden. Assetto Corsa ist eine Marke von Kunos Simulazioni.

---

# StintPulse 0.1.0

Erste öffentliche Version / first public release.

**Deutsch**
- Live-Telemetrie aus dem originalen Assetto Corsa (Shared Memory, ohne CSP): Rundenzeit, Sektoren, Delta, Reifen, Pedale, Streckenkarte – im Browser und auf dem Handy (LAN mit 8-stelligem Zugriffscode).
- Automatische Rundenaufzeichnung mit Sektorzeiten, Analyse über die Distanz, theoretische Bestzeit, regelbasierter Coach.
- Onboard: OBS Virtual Camera live (auch aufs Handy), Rundenzeit-HUD im TV-Stil.
- Onboard-Aufnahme mit Spielsound: jede Runde später mit HUD aus den gespeicherten Daten ansehen.
- Alle Daten bleiben lokal (`%LOCALAPPDATA%\StintPulse`), kein Konto, kein Upload. Optionaler Update-Hinweis über GitHub.

Hinweis: Das Programm ist (noch) nicht digital signiert. Windows SmartScreen kann warnen: „Weitere Informationen → Trotzdem ausführen“. Prüfsummen: `SHA256SUMS.txt`.

**English**
- Live telemetry from original Assetto Corsa, lap/sector recording, distance-based analysis, coach, live onboard via OBS Virtual Camera with TV-style lap-timing HUD, onboard recording with game sound and per-lap playback. Local only, no account, no upload.

StintPulse is an independent project, not affiliated with Kunos Simulazioni. Assetto Corsa is a trademark of Kunos Simulazioni.
