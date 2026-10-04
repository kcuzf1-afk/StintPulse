# Reproduzierbare Testdaten

`demo-recording.json` enthält zwei mit 30 Hz aufgezeichnete **synthetische** Demo-Runden. Dies ist kein Mitschnitt eines echten Assetto-Corsa-Fahrers. Quelle und Provenienz stehen im Dateikopf und in jedem Sample. Die zweite Runde hat definierte Geschwindigkeitsverluste in den automatisch erkannten Kurven 3 und 6.

`physics.bin`, `graphics.bin`, `static.bin` sind unabhängig über `struct.pack_into` und feste, geprüfte Windows-ABI-Offsets erzeugte Testseiten. Sie prüfen unter anderem UTF-16-Umlaute und ein Streckenlayout mit mehr als 15 Zeichen. Es wurden keine AC-Kernel-Seiten für die Demo angelegt.

`mediarecorder-vp8.webm` ist eine echte, 8 s lange Aufnahme von Chromiums MediaRecorder (synthetische Testkamera, VP8, Keyframe alle 2 s) – genau das Format, das das Dashboard beim Onboard-Aufnehmen erzeugt (Segment und Cluster ohne Größe, ohne Dauer und Index). Die Tests indizieren sie, kürzen sie künstlich und laden sie in Datenblöcken hoch.

Neu erzeugen: `python scripts/generate_fixtures.py`. Die Tests lesen die eingecheckten Dateien; sie benötigen weder das Spiel noch eine Windows-Grafikoberfläche. Ein zusätzlicher Named-mmap-Integrationstest läuft nur unter Windows. Ein echter Spielmitschnitt und dessen Prüfung auf Windows sind noch erforderlich, um Mod-spezifische Datenqualität zu beurteilen.
