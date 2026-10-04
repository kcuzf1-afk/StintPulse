# Onboard und OBS

Shared Memory enthält kein Video. Die App behandelt Video unabhängig von Telemetrie und besitzt vier nutzbare Wege: **OBS Virtual Camera** (oder eine andere Kamera), Browser-Fensterfreigabe, eine direkte browserkompatible Video-URL und eine lokale Videodatei. Ein fehlendes oder fehlerhaftes Bild blockiert weder Erfassung noch Speicherung.

## Live-Onboard über OBS Virtual Camera (empfohlen)

OBS bereitet das Bild auf (Game Capture, Zuschnitt, eigene Overlays) und gibt es als virtuelle Kamera aus. Das Dashboard liest diese Kamera wie eine Webcam (`getUserMedia`, nur Video, ohne Ton).

1. OBS Studio (ab Version 26, virtuelle Kamera eingebaut) starten. Szene mit **Spielaufnahme / Game Capture** für Assetto Corsa anlegen; bei schwarzem Bild **Fensteraufnahme** im randlosen Fenstermodus.
2. In OBS rechts unten **Virtuelle Kamera starten** klicken.
3. Dashboard unter `http://localhost:8765` in **Chrome oder Edge** öffnen.
4. Einstellungen → Videoquelle → **OBS Virtual Camera / Webcam** → Speichern.
5. Onboard öffnen. Beim ersten Mal fragt der Browser nach der Kamera-Berechtigung → **Zulassen**. Die App wählt automatisch die Kamera mit dem Namen „OBS Virtual Camera“, sonst die erste Kamera.
6. Bei mehreren Kameras erscheint in der Onboard-Leiste eine Auswahl; die Wahl wird gespeichert. ⟲ verbindet die Kamera neu.

Verhalten:

- Die Kamera wird beim Öffnen der Seite automatisch verbunden; es gibt genau einen MediaStream, der beim Wechsel der Quelle oder beim Schließen freigegeben wird.
- Wird die virtuelle Kamera in OBS gestoppt, zeigt die Leiste „NO VIDEO SOURCE“ mit Hinweis. Je nach OBS-Version zeigt eine gestoppte virtuelle Kamera stattdessen ein OBS-Platzhalterbild – dann in OBS die virtuelle Kamera wieder starten.
- Taucht die Kamera neu auf (OBS gestartet), verbindet die App über das Browser-Ereignis `devicechange` automatisch.
- Angefragt werden 1920 × 1080 bei 60 fps als Wunschwerte; tatsächlich liefert die virtuelle Kamera die in OBS unter *Einstellungen → Video* eingestellte Ausgabeauflösung und FPS.
- HUD, Vollbild und Bild-in-Bild funktionieren wie bei den anderen Quellen. Die Telemetrie läuft unabhängig vom Video.

Kamerazugriff erlaubt der Browser nur auf `localhost` oder HTTPS; die Berechtigung verwaltet der Browser (Schloss-Symbol in der Adressleiste).

## Live-Onboard auf Handy/Tablet (WebRTC)

Ist die Videoquelle *OBS Virtual Camera / Webcam* oder *Window capture*, entscheidet die Adresse, wer sendet:

- **PC** (`localhost`/`127.0.0.1`): nimmt die OBS-Kamera bzw. das Fenster auf und **sendet** es.
- **Handy/Tablet** (LAN-Adresse, z. B. `http://192.168.178.49:8765`): öffnet **keine** eigene Kamera, sondern **empfängt** das PC-Bild.

Ablauf: LAN-Zugriff und Zugriffscode einrichten (README, Abschnitt 7), in OBS die virtuelle Kamera starten, das Dashboard am PC auf *Engineering* oder *Onboard* geöffnet lassen, auf dem Handy *Onboard* öffnen. Statusleiste am Handy: „PC-BILD LIVE · WEBRTC“; am PC zeigt ein Handy-Symbol die Zahl der Zuschauer.

Technik: Zuerst wird eine direkte **WebRTC**-Verbindung im LAN versucht (ohne STUN/TURN, ohne Internet, verschlüsselt); der Server vermittelt dabei nur Angebot/Antwort/ICE über `/ws/onboard`. Kommt sie nicht innerhalb von 6 s zustande – typisch, wenn das Windows-Netzwerk als „Öffentlich“ eingestuft ist und die Firewall den UDP-Verkehr des Browsers blockiert –, schaltet das Handy automatisch auf **„ÜBER PC-SERVER“** um: Der PC-Tab schickt dann JPEG-Bilder (max. 1280 px breit, ca. 15 Bilder/s) über die ohnehin freigegebene App-Verbindung; der Server leitet immer das neueste Bild weiter und staut nichts auf. Der PC kodiert nur, solange ein Handy diese Bilder braucht. Kein Ton.

Bricht WebRTC ab, baut das Handy die Verbindung automatisch neu auf (bei „getrennt“ nach 3 s, bei „fehlgeschlagen“ sofort). Sind mehrere Dashboard-Tabs am PC offen, sendet der Tab **mit Bild**; ein Tab ohne Kamera verdrängt ihn nicht. Wurde die PC-App aktualisiert, lädt ein offener Tab sich einmal selbst neu.

| Problem am Handy | Vorgehen |
|---|---|
| „Warte auf das Bild vom PC“ | Am PC Dashboard auf *Engineering*/*Onboard* offen lassen; in OBS virtuelle Kamera starten; Videoquelle am PC *OBS Virtual Camera / Webcam* |
| „Direktverbindung zum PC fehlgeschlagen“ | Normal bei blockiertem WebRTC: nach wenigen Sekunden folgt „ÜBER PC-SERVER“. Bleibt es dabei: Handy und PC im selben Netz? Gast-WLAN/AP-Isolation; VPN auf dem Handy aus |
| „Zugriffscode ungültig oder geändert“ | Seite neu laden und aktuellen Code vom PC eingeben |
| Bild ruckelt | OBS-Ausgabe (Einstellungen → Video) auf 1280×720 / 30 fps reduzieren; 5-GHz-WLAN nutzen |

Grenzen: Es ist eine LAN-Lösung, kein Internet-Streaming. Die Verzögerung hängt von WLAN und Geräten ab und wurde nicht mit einem echten Handy gemessen.

| Problem | Vorgehen |
|---|---|
| „Kamerazugriff verweigert“ | Schloss-Symbol in der Adressleiste → Kamera erlauben → ⟲ |
| „Keine Kamera gefunden“ | In OBS „Virtuelle Kamera starten“; die App verbindet automatisch, sonst ⟲ |
| „Kamera ist belegt“ | Andere Programme schließen, die die virtuelle Kamera nutzen; in OBS virtuelle Kamera neu starten |
| Falsche Kamera | In der Onboard-Leiste „OBS Virtual Camera“ wählen |
| Schwarzes Bild | In OBS die Vorschau prüfen: Game Capture/Fensteraufnahme liefert dort schon kein Bild → Quelle in OBS korrigieren |

## Rundenzeit-HUD über dem Onboard-Bild

Kompakte Anzeige im Stil der TV-Zeitnahme oben rechts **innerhalb der sichtbaren Bildfläche** (bei Letterboxing am echten Bild ausgerichtet): rote Positionsbox, Nachname in Großbuchstaben, Reifenbuchstabe rechts, große Rundenzeit (M:SS.mmm, gleich breite Ziffern) und darunter S1–S3 als farbige Felder. Schrift: Titillium Web (SIL Open Font License, lokal gebündelt, funktioniert offline). Bewusst **ohne** Team-/Serienlogos und ohne die geschützte TV-Schrift. Reifenfarbe nur bei genau einem Buchstaben S/M/H/I/W (rot/gelb/weiß/grün/blau) aus dem Spiel-Kürzel oder der manuellen Zuordnung; alles andere neutral. Rundennummer, Referenz/Delta und Sektorzeiten zeigt der Schalter **„Details“** im HUD-Menü (standardmäßig aus, wie im Fernsehen). Es ist ein DOM-Overlay – nichts wird in die Videobilder eingebrannt. Es erscheint, sobald eine Videoquelle ein Bild liefert (OBS-Kamera, Fensterfreigabe, URL, lokale Aufnahme oder das PC-Bild auf dem Handy).

**Datenquelle:** ausschließlich die vorhandene Runden-/Sektorlogik des Backends (Rundenuhr `iCurrentTime`, offizielle Rundenzeit `iLastTime`, Sektorzeiten `lastSectorTime`). Das Frontend rechnet keine eigene Zeit hoch: Steht die Telemetrie, steht auch die Anzeige.

| Situation | Anzeige |
|---|---|
| Fahrt | laufende Rundenzeit; abgeschlossene Sektoren mit Zeit und Farbe, offene grau |
| Zieleinlauf | abgeschlossene Runde mit Differenz zur Referenz, für die eingestellte Dauer (Einstellungen → Onboard-Video, Standard 5 s); danach die neue Runde |
| erste, mitten begonnene Runde | „UNVOLLST.“ (nicht ab Start/Ziel erfasst), nicht rot |
| ungültige Runde | „UNGÜLTIG“ und rote Sektoren. Grundlage: Strecke verlassen (≥ 3 Reifen neben der Strecke) oder Strafe – aus Spielfeldern abgeleitet, Original-AC hat kein offizielles Gültig-Flag |
| Pause | Zeit eingefroren (gelb), Status „PAUSE“ |
| Daten veraltet, Spiel weg, Verbindung zum PC getrennt | Status statt Zeit („—“) – keine weiterlaufende Zeit |
| Demo | Kennzeichnung „DEMO“; beim Wechsel auf AC verschwinden alle Demo-Werte |
| Wiedergabe gespeicherter Runde | „REPLAY“ bzw. „DEMO-REPLAY“, nur Daten der wiedergegebenen Runde; der Video-Zeitversatz gilt wie bisher |
| Session-Neustart, anderes Auto/Strecke/Layout | Anzeige und Vergleichswerte beginnen neu |

**Sektorfarben:** grau = nicht abgeschlossen oder keine Vergleichsdaten · grün = schneller als der Referenzsektor · gelb = langsamer oder gleich schnell (1 ms Auflösung, Gleichstand ist keine Verbesserung) · violett = neuer **persönlicher** Sektorbestwert (gleicher Fahrer, Auto, Strecke, Layout, Datenquelle) – kein Sessionbestwert aller Fahrer · rot = Runde ungültig.

**Referenz:** standardmäßig die persönliche Bestzeit („PB“) derselben Kombination; eine in der Analyse gewählte Referenzrunde erscheint als „REF“. In der Wiedergabe wird nur innerhalb der wiedergegebenen Session verglichen („S-PB“ = schnellste andere gültige Runde; violett = besser als alle früheren gültigen Runden).

**Sektoren:** nur die vom Spiel gelieferten. Meldet das Spiel keine plausible Sektorzahl, steht „Sektoren nicht verfügbar“; es werden keine Grenzen erfunden.

**Position und Name** kommen aus dem Spiel (`graphics.position`, Fahrername aus der Static-Seite).

**Reifen:** angezeigt wird nur das Kürzel, das das Spiel selbst in Klammern liefert (z. B. „Pirelli Soft (S)“ → „S“). Fehlt es, steht der vollständige Name in einer eigenen Zeile. Eine **manuelle** Zuordnung (Einstellungen → Onboard-Video → „Reifen-Kürzel“, je Zeile `Name im Spiel = Kürzel`) ist mit * markiert. Es gibt keine automatische S/M/H-Zuordnung.

**Bedienung** (Stoppuhr-Symbol in der Onboard-Leiste): anzeigen/ausblenden, Größe, Deckkraft, Position sperren, Position zurücksetzen. Verschieben am Griff ⠿ links neben dem HUD – mit Maus oder Finger. Nur der Griff nimmt Eingaben an; das HUD selbst blockiert keine Videobedienung. Gesperrt verschwindet auch der Griff. Die Einstellungen werden **pro Gerät** gespeichert (Handy und PC getrennt). Auf kleinen Bildern verkleinert sich das HUD automatisch (max. ca. 62 % der Bildbreite).

**Vollbild:** Das Vollbild-Symbol vergrößert den gemeinsamen Container aus Video und HUD – das HUD bleibt sichtbar.

**Bild-in-Bild:** Normales Video-Bild-in-Bild übernimmt keine DOM-Overlays. Unterstützt der Browser *Document Picture-in-Picture* (Chrome/Edge am Desktop), wandert das Bild **mit** HUD in das Bild-in-Bild-Fenster (dort ohne Verschieben). Sonst öffnet sich normales Video-Bild-in-Bild und die App sagt ausdrücklich, dass das HUD dort fehlt. Das PC-Bild „über PC-Server“ auf dem Handy ist kein Video und kann nur per Document Picture-in-Picture schweben.

## Live-Fenster am selben Windows-PC

1. Original-AC starten; bei schwarzem Capture zunächst randlosen Fenstermodus verwenden.
2. Dashboard auf dem zweiten Monitor unter `http://localhost:8765` öffnen.
3. Einstellungen → Videoquelle → **Browser · Window capture** → Speichern.
4. Onboard öffnen, **Fenster freigeben** anklicken und das AC-Fenster im Browserdialog wählen.
5. HUD, Vollbild und Picture-in-Picture über die Leiste aktivieren. Das Stop-Symbol beendet die Freigabe.

Der Browser entscheidet, ob Fenster, Tab oder Bildschirm freigegeben werden kann; die App erzwingt keine heimliche Aufnahme. `getDisplayMedia` fragt bis zu 30 fps an, ohne eine tatsächliche Bildrate oder Latenz zu garantieren. Es wird kein Audio erfasst. Chrome/Edge auf localhost oder einem vertrauenswürdigen HTTPS-Origin sind die bevorzugte Umgebung.

Das ist eine lokale Browser-Capture-Lösung, kein eigener nativer Windows-Graphics-Capture-Adapter. Ein am Tablet geöffnetes Dashboard kann damit nicht automatisch das AC-Fenster des Windows-PCs empfangen. Für das Tablet ist ein eigener lokaler Videotransport erforderlich.

## Onboard automatisch aufnehmen (Video pro Session, Runde ansehen)

**Ablauf**

1. In OBS die virtuelle Kamera starten.
2. Einstellungen → **Onboard und HUD**: Videoquelle **OBS Virtual Camera / Webcam**, **Onboard automatisch aufnehmen** einschalten, speichern.
3. Das Dashboard **auf dem PC** (`http://localhost:8765`) geöffnet lassen und fahren. Oben erscheint **AUFNAHME LÄUFT 0:42**. Der Tab darf im Hintergrund liegen; die Seiten der App dürfen gewechselt werden.
4. Nach der Fahrt: **Sessions** → Session ankreuzen → in der Rundenliste bei einer Runde **Onboard ansehen**.

**Wie es technisch funktioniert**

- Aufgenommen wird im Browser-Tab auf dem PC (MediaRecorder) – das reine Kamerabild ohne HUD. Live-Ansicht und Aufnahme nutzen *denselben* Kamerastream; ein Seitenwechsel unterbricht nichts.
- Alle 2 Sekunden wird ein Datenblock an das PC-Programm geschickt und sofort an die Datei angehängt (`<Datenordner>\videos\…`, Ordner einstellbar). Lange Sessions liegen nie komplett im Arbeitsspeicher.
- Format: **WebM (VP8, sonst VP9)** – zur Laufzeit geprüft. MP4 aus dem Browser-Recorder wurde getestet und liefert in Chromium eine falsche Dauer und lässt sich nicht spulen; es wird deshalb nicht verwendet. Ohne WebM-Unterstützung meldet die App „Aufnahme nicht möglich“ (Chrome oder Edge verwenden).
- Nach jedem Abschnitt wird die Datei **ohne Neukodierung** indiziert (Dauer und Index je Keyframe ergänzt), damit auch lange Aufnahmen per HTTP-Range-Anfragen schnell gespult werden können. Bricht die Aufnahme ab (Browser geschlossen, Absturz), bleiben alle vollständig gespeicherten Bilder abspielbar; beim nächsten Start wird der Abschnitt automatisch abgeschlossen.
- **Ein Video pro Session.** Fällt die Videoquelle aus, endet der Abschnitt; kommt sie zurück, beginnt Abschnitt 2 derselben Session. Die Telemetrie läuft unabhängig weiter. Runden über eine Lücke gelten als **„Video teilweise“**, nie als vollständig.
- Eine Spielpause beendet die Aufnahme nicht. Ein Sessionwechsel oder ein Sessionende (länger als 15 s keine aktive Fahrt, z. B. Menü) schließt den Abschnitt sauber ab und ordnet ihn der richtigen Session zu.
- Auflösung und Bildrate kommen von OBS (virtuelle Kamera). Die Aufnahmequalität stellt nur die **Bitrate** ein (Sparsam 3, Standard 6, Hoch 12 Mbit/s). Höhere Qualität kostet während der Fahrt mehr CPU.

**Ton**

Die OBS Virtual Camera überträgt grundsätzlich keinen Ton. Deshalb nimmt das PC-Programm den Ton selbst aus Windows auf (WASAPI-Loopback, nur lesend, kein Zusatztreiber), parallel zu jedem Videoabschnitt:

- **Spielsound (Standard):** nur, was Assetto Corsa (`acs.exe` und Unterprozesse) ausgibt – ohne Discord, Musik oder Systemtöne. Benötigt Windows 10 Version 2004 oder neuer. Läuft AC nicht (z. B. Demo), entsteht ein Video ohne Ton mit entsprechendem Hinweis.
- **Gesamter PC-Ton:** alles, was am PC zu hören ist (auch Discord, Musik).
- **Kein Ton.**

Der Ton wird laufend als WAV (48 kHz, Stereo, 16 Bit, ≈ 0,7 GB pro Stunde) neben das Video geschrieben und zählt zum Video-Speicherlimit. Jedes Audiopaket trägt einen Zeitstempel der Windows-Systemuhr; die Datei wird auf der gemeinsamen PC-Uhr gehalten (Stille wird als Stille gespeichert, Uhrendrift in Schritten ≤ 20 ms korrigiert). Bei der Wiedergabe spielt der Player den Ton synchron zum Bild – auch nach Spulen, bei Pause und 0,25×–2× – mit derselben Bedeutung des Video-Versatzes wie das HUD. Ton an/aus und Lautstärke stellst du im Player ein (gilt pro Gerät). Bricht die App während der Fahrt ab, bleibt der bis dahin aufgenommene Ton erhalten.

Handys/Tablets spielen den Ton gespeicherter Runden ebenfalls ab. Das **Live**-Onboard auf dem Handy bleibt ohne Ton.

**Wichtig:** Die Aufnahme braucht den geöffneten Browser-Tab auf dem PC. Nach dem Schließen des Tabs oder Browsers wird **nicht** weiter aufgenommen (die App fragt beim Schließen während einer Aufnahme nach). Handys/Tablets im LAN nehmen nicht auf, können gespeicherte Videos aber ansehen.

**Zeitbasis und Video-Versatz**

Video und Telemetrie verwenden eine gemeinsame Uhr: Jede Aufnahme speichert, zu welcher PC-Uhrzeit Videozeit 0 lag (die Browser-Uhr wird dabei über eine gemessene Differenz auf die PC-Uhr umgerechnet); jedes Telemetrie-Sample trägt seine PC-Uhrzeit. Daraus folgt

`Videoposition = Telemetrie-Zeitpunkt − Aufnahmebeginn + Video-Versatz`

**Video-Versatz** (Einstellungen oder ± im Player): positiv, wenn das Bild der Telemetrie hinterherläuft (typische OBS-/Virtual-Camera-Verzögerung 0,05–0,2 s). Er wird bei der Wiedergabe mit dem aktuellen Wert angewendet. Dieselbe Bedeutung gilt für die manuell geladene Aufnahme unten.

**Speicher**

- Speicherlimit für Videos (Standard 20 GB). Ist es erreicht, stoppt die Aufnahme mit der Meldung „Speicherlimit erreicht“ – es wird **nichts automatisch gelöscht**, außer die Regel **„Älteste Videos automatisch löschen“** ist ausdrücklich eingeschaltet (nie die laufende Session, nie Favoriten).
- Zu wenig freier Plattenplatz (Reserve 512 MB) stoppt die Aufnahme ebenfalls mit Meldung.
- Das Telemetrie-Speicherlimit löscht keine Sessions mit Video. Wer eine Session löscht, löscht auch ihre Videos; einzelne Session-Videos lassen sich in Sessions über das Symbol „Videos löschen“ entfernen.
- Videos sind **nicht** Teil des Datenbank-Backups (es sind Dateien im Videoordner).

**Wiedergabe einer Runde**

- Startet automatisch am Rundenbeginn und hält am Rundenende an (optional **Runde wiederholen**).
- Zeitleiste in Rundenzeit, Sprünge ±5 s (Pfeiltasten, Umschalt = 1 s), Geschwindigkeit 0,25× / 0,5× / 1× / 2×, Vollbild, HUD ein/aus.
- Das HUD (Rundenzeit, Sektoren, Fahrer, Delta am Rundenende, Tempo, Gang, Gas/Bremse) zeigt ausschließlich die gespeicherten Daten dieser Runde und folgt der tatsächlichen Videoposition – auch nach Spulen, Pause und bei anderer Geschwindigkeit. Stetige Werte werden interpoliert, Gang und Sektor nicht. Eine gleichzeitig laufende Live-Fahrt gelangt nie in dieses HUD. Die Ansicht ist als **WIEDERGABE** gekennzeichnet.
- Ungültige Runden sind ansehbar und als „Ungültige Runde“ markiert; Outlaps/unvollständige Runden als „Unvollständig · keine gezeitete Runde“.
- Teilweise abgedeckte Runden zeigen die fehlenden Teile rot auf der Zeitleiste; sie sind nicht abspielbar.

## OBS-Aufnahme manuell laden (Alternative)

1. OBS Studio installieren und eine Szene erstellen.
2. Als Quelle **Game Capture** für den AC-Spielprozess versuchen. Alternativ **Window Capture** im randlosen Fenstermodus wählen. Den Bildausschnitt auf 16:9 setzen.
3. Als Aufnahmeformat beispielsweise MKV mit H.264 verwenden; nach der Aufnahme über OBS **File → Remux Recordings** nach MP4 umwandeln. Die tatsächlichen Menübezeichnungen können zur OBS-Sprache/Version variieren.
4. Aufnahme vor Beginn des Stints starten. Die Data-Agent-App muss gleichzeitig im AC-Modus mit automatischem Speichern laufen.
5. In der App Videoquelle **Local recording** auswählen und speichern.
6. Im Session-Archiv eine Runde oder **Session wiedergeben** starten. Im Onboard-Panel **Aufnahme öffnen** wählen und die MP4-/WebM-Datei laden.
7. Das Video zu einem gut sichtbaren Start-/Ziel-Durchgang ausrichten und den Offset in Sekunden konfigurieren.

Die Synchronisation lautet:

`Videozeit = Telemetrie-Rundenzeit + Session-Zeitbasis + Video-Offset`.

Einzelrunde: Beginnt die Runde bei 42,3 s in der Videodatei, den Offset auf **+42,3** setzen. Beginnt das Video 2 s nach Rundenstart, Offset **−2,0**; der Anfang bleibt ohne passendes Bild. Bei **Session wiedergeben** bezieht sich der Offset auf die erste wiedergegebene vollständige Runde. Folgerunden berücksichtigen ihre gemessenen Zeitstempelabstände; ausgelassene unvollständige Runden werden nicht künstlich aus dem Video geschnitten.

Der Browser korrigiert eine Abweichung über 0,2 s durch Seeking. Genauigkeit hängt von Aufnahme, Keyframes, Browser, Zeitstempeln und eventuellen Spielpausen ab. Es gibt keine automatische gemeinsame Video-/Telemetry-Clock, keine automatische OBS-Aufnahmesteuerung und keinen Videoexport. Pausen im Spiel und in der Aufnahme können eine neue manuelle Offset-Korrektur verlangen. Die Videodatei wird nicht hochgeladen oder dauerhaft in das App-Archiv aufgenommen.

## OBS WebSocket ist kein Bildtransport

OBS WebSocket ist ein Steuerkanal für Szenen, Start/Stop und OBS-Status. Eine WebSocket-Adresse kann nicht in **Direct video URL** als Bildquelle eingetragen werden. Dieses Release enthält keinen OBS-WebSocket-Client und fragt keine OBS-Zugangsdaten ab. Für Live-Onboard am lokalen PC die Fensterfreigabe verwenden; für gespeicherte Wiedergabe die OBS-Aufnahme.

## Video im lokalen Netzwerk

Eine manuell konfigurierte URL muss direkt vom HTML5-Videoelement abspielbar sein, z. B. ein unterstützter MP4-/WebM-Stream. Sie muss vom Tablet erreichbar sein; `localhost` auf dem Tablet bezeichnet das Tablet. Container, Codec, MIME-Type, Range-Support und bei HTTPS Mixed-Content-Regeln müssen zum Browser passen.

RTMP, NDI und eine URL zu einer WebRTC-HTML-Seite sind keine direkt unterstützten `video src`-Quellen. Für einen externen NDI-/WebRTC-/OBS-Relay wären ein eigener Transport und ein passender Player erforderlich. Dieser Server ist nicht mitgeliefert; die App behauptet deshalb keine Tablet-Live-Videoübertragung allein durch Aktivierung des LAN-Modus. Telemetrie auf dem Tablet funktioniert ohne Video. Eine konkrete Bridge kann später hinter der bestehenden Onboard-Komponente ergänzt werden.

## Fehlerbilder

| Problem | Vorgehen |
|---|---|
| Browser bietet keine Freigabe | localhost oder vertrauenswürdiges HTTPS öffnen; Browserberechtigung prüfen |
| schwarzes AC-Fenster | randloses Fenster verwenden; passendes Spiel-/Fenster-Capture wählen; Hardware/Browser-Unterstützung prüfen |
| PiP-Schaltfläche ohne Wirkung | Browser muss Video-PiP unterstützen; zuerst eine laufende Videoquelle laden |
| lokale Aufnahme spielt nicht | MKV nach MP4 remuxen; unterstützten Codec verwenden; Datei neu auswählen |
| Video springt oder driftet | Offset prüfen; Aufnahme-Pausen und große Keyframe-Abstände berücksichtigen |
| HTTPS-Dashboard und HTTP-Video | Browser kann Mixed Content blockieren; eine vertrauenswürdige HTTPS-Videoquelle bereitstellen |
| Fensterfreigabe verschwindet nach Reload | erneut manuell freigeben; Browserberechtigungen und MediaStreams sind nicht dauerhaft |
| „VIDEOQUELLE FEHLT“ während der Fahrt | OBS virtuelle Kamera starten; die Aufnahme beginnt dann automatisch (neuer Abschnitt). Telemetrie läuft weiter |
| „AUFNAHME FEHLGESCHLAGEN – Speicherlimit“ | Limit erhöhen, Videos löschen oder die Löschregel einschalten, dann „Erneut versuchen“ |
| „Ein anderer Browser-Tab nimmt bereits auf“ | nur ein Dashboard-Tab auf dem PC offen lassen |
| Runde zeigt „Video teilweise“ | Quelle war kurz weg oder die Aufnahme begann während der Runde; der Rest der Runde ist ansehbar |
| HUD läuft dem Bild vor/nach | im Player mit −/+ den Video-Versatz in 0,05-s-Schritten anpassen (verschiebt Ton und HUD gemeinsam gegenüber dem Bild) |
| Runde „ohne Ton“ | Einstellungen → Ton aufnehmen prüfen; „Spielsound“ braucht laufendes Assetto Corsa und Windows 10 2004+ |
| Kein Ton trotz „mit Ton“ | im Player Ton/Lautstärke prüfen; bei „Ton aktivieren“ einmal klicken (Browser-Autoplay-Regel) |

Telemetrie-Update- und Capture-Raten sind getrennte Einstellungen. Die gemessenen Performancewerte in `VERIFICATION.md` enthalten kein Video; deshalb werden keine gemessenen Capture-Latenzwerte behauptet.
