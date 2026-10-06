# Setup-Assistent (Analyse → Setup)

Der Setup-Assistent verbessert ein vorhandenes Assetto-Corsa-Setup anhand gespeicherter Runden und deiner Rückmeldung. Er schlägt höchstens drei Änderungen vor, erklärt sie und exportiert auf Wunsch eine **neue** Setup-Datei. Das Ausgangssetup wird nie verändert, und nichts wird automatisch im Spiel geladen.

## Ablauf

1. **Fahrzeug und Strecke** wählen (aus deinen gespeicherten Sessions).
2. **Ausgangssetup** wählen: ein gespeichertes Setup aus `Dokumente\Assetto Corsa\setups\<auto>\<strecke|generic>` oder eine INI-Datei importieren. Es wird unverändert als Ausgangsversion gespeichert.
3. **Runden** wählen, möglichst mehrere gleichmäßige.
4. **Ziel** wählen (Qualifying, Rennen, Konstanz oder Individuell) und das **Fahrverhalten** beschreiben, z. B. „Untersteuern · Kurveneingang · stark“, plus Freitext.
5. **Setup analysieren (KI)** oder **Regelbasiert analysieren (ohne KI)** wählen. Mit **Daten-Vorschau** siehst du genau die Anfrage, die gesendet würde.
6. Empfehlungen prüfen und einzeln **übernehmen**.
7. **Exportieren und herunterladen**. Danach optional **in den AC-Setupordner speichern**. Die Datei wird dort neu angelegt und überschreibt nie eine vorhandene.
8. Im Spiel das neue Setup laden, Runden fahren und die Session unter **Versionen und Vergleich** der Version **zuordnen**. Das Zuordnen ist manuell, weil StintPulse das geladene Setup nicht auslesen kann. Danach vergleichst du Vorher und Nachher.

## Woher die Einstellgrenzen kommen

- Die Grenzen stammen aus der Datei `content\cars\<auto>\data\setup.ini` des Fahrzeugs (MIN, MAX, STEP, NAME, TAB). Sie werden nur gelesen.
- Die meisten Autos liefern nur die gepackte Datei `data.acd`. **StintPulse entschlüsselt sie nicht.** Ohne setup.ini sind Grenzen und Kodierung *unbestätigt*:
  - Analyse und Empfehlungen funktionieren trotzdem.
  - Ein Export ist gesperrt.
  - Abhilfe: die setup.ini des Fahrzeugs unter „1 · Fahrzeug und Strecke“ importieren, z. B. nach „Daten entpacken“ in Content Manager. Das geht nur, wenn der Fahrzeugautor es erlaubt.
- **Kodierung des Dateiwerts:** In der Setup-Datei steht `VALUE=` immer als ganze Zahl, aber je nach Parameter und Fahrzeug unterschiedlich:
  - als Wert,
  - als Klickzahl ab MIN,
  - beim Sturz als Zehntelgrad.

  `SHOW_CLICKS` entscheidet das nicht zuverlässig. StintPulse leitet die Kodierung deshalb aus allen gespeicherten Setups des Fahrzeugs ab:
  - **abgeleitet:** nur eine Kodierung passt zu allen gespeicherten Werten. Der Parameter ist exportierbar.
  - **mehrdeutig:** mehrere Kodierungen passen. Unter „Parameter, Grenzen und Kodierung“ kannst du bestätigen, welchen Wert das Spiel anzeigt. Danach gilt der Parameter als **bestätigt** und ist exportierbar. Bitte nur bestätigen, wenn du den Wert im Spiel abgelesen hast.
  - **widersprüchlich / unbestätigt / nicht unterstützt:** kein Export. Getriebeübersetzungen (Listen) werden nie geändert.
- Einheiten werden nur angezeigt, wo Assetto Corsa sie für alle Fahrzeuge festlegt: psi, Grad, Liter und Prozent bei der Bremsbalance.

## Telemetrie-Kennwerte

- **Ausgeschlossen** werden sichtbar, mit Grund:
  - unvollständige Runden und Boxen- (Out-/In-)Runden,
  - Runden mit Schaden, Dreher, Datenlücke,
  - deutliche Zeitausreißer.
- **Ungültige Runden (Streckenbegrenzung)** sind standardmäßig ausgeschlossen. Du kannst sie bewusst einbeziehen, sie bleiben dann markiert.
- **Kennwerte** (Mediane über die repräsentativen Runden):
  - Rundenzeit und Streuung, Sektoren, Höchstgeschwindigkeit,
  - je Kurvenphase: Schlupfverhältnis vorne/hinten (nur ohne starkes Bremsen oder Gas), Schwimmwinkel, Lenkung je g,
  - Reifen innen/mitte/außen, Kern, Druck und Verschleiß,
  - Ereignisse pro Runde (Blockieren, Durchdrehen, Rutscher …), Bodenfreiheit, Bremstemperaturen,
  - Bedingungen (Tank, Temperaturen, Reifenmischung).
- **Unter- und Übersteuern** werden nicht aus Lenkwinkel und Geschwindigkeit „gemessen“. Die Kennwerte sind Indizien. Je Rückmeldung zeigt StintPulse, ob die Messung sie *stützt*, ihr *eher widerspricht*, *nicht eindeutig* ist oder *nicht messbar*. Fehlende Kanäle werden nicht geschätzt.

## KI-Anbieter und Datenschutz

- **Anbieter:** Anthropic Claude über das offizielle Python-SDK. Standardmodell `claude-opus-5-5`, unter „KI-Anbieter“ änderbar. Die Anbieter-Schnittstelle ist austauschbar (`setup_ai.py`).
- **Standard ist „Aus“:** Dann gibt es nur die klar gekennzeichnete regelbasierte Analyse.
- **API-Schlüssel:**
  - Er wird nur am PC selbst eingegeben und im Backend gespeichert (`Daten\secrets\anthropic.key`), unter Windows mit DPAPI an dein Windows-Benutzerkonto gebunden verschlüsselt.
  - Er erscheint nie in Einstellungen, API-Antworten, im Browser oder im Protokoll.
  - Alternativ wird die Umgebungsvariable `ANTHROPIC_API_KEY` verwendet.
  - Die Datei ist auf einem anderen PC nicht lesbar. Dann zeigt StintPulse „bitte neu eingeben“.
- **Vor der ersten Nutzung** zeigt StintPulse, welche Daten an welchen Anbieter (`api.anthropic.com`) gehen. Erst nach deiner Bestätigung am PC ist die KI-Analyse möglich.
- **Gesendet wird nur beim Klick auf „Setup analysieren (KI)“:**
  - Fahrzeug- und Streckenname, Ziel, Rückmeldung, Abgleich Messung/Rückmeldung,
  - Setup-Werte mit Grenzen,
  - zusammengefasste Kennwerte und Bedingungen.
- **Nicht gesendet werden** Fahrername, Session- oder Runden-IDs, Rohtelemetrie, Videos und Ton.
- **Fallback:** Lehnt das Modell eine Anfrage ab, beantwortet Anthropic sie serverseitig mit einem Ersatzmodell (`fallbacks: "default"`).
- **Ohne Internet oder bei einem Fehler** zeigt StintPulse einen ehrlichen Fehlerzustand. Es entstehen keine Empfehlungen, und die regelbasierte Ersatzanalyse wird angeboten.

## Prüfung außerhalb des Modells

Die Antwort muss ein festes JSON-Schema erfüllen. Danach prüft deterministischer Code jede vorgeschlagene Änderung:

- Der Parameter existiert für das Fahrzeug und im Ausgangssetup.
- Der Wert liegt innerhalb von MIN…MAX, auf dem Schrittraster und als ganzzahliger Dateiwert, und er ändert tatsächlich etwas.
- Es gibt keine Dopplung und höchstens drei Änderungen.
- Die Begründung ist vollständig.

Weitere Regeln:

- Sätze mit Rundenzeit-Versprechen oder Garantien werden entfernt.
- Die Konfidenz wird bei weniger als drei Runden auf „niedrig“ begrenzt, ohne Rückmeldung auf „mittel“.
- Verworfene Vorschläge stehen mit Grund unter „Prüfung“.
- Für den Export wird alles noch einmal geprüft. Nur die gewählten `VALUE=`-Zeilen ändern sich, alle anderen Bytes der Datei bleiben gleich, einschließlich CSP-Skripteinträgen, Kommentaren und Zeilenenden.

## Speicherung

In der StintPulse-Datenbank liegen:

- Ausgangs- und exportierte Versionen (Inhalt, Änderungen, Speicherort),
- jede Analyse mit genau der gesendeten Anfrage und dem geprüften Ergebnis,
- die manuellen Zuordnungen Session → Version,
- importierte setup.ini-Dateien und deine Kodierungs-Bestätigungen.

Backup und Wiederherstellung enthalten diese Daten. Der API-Schlüssel ist nicht enthalten.

## Grenzen

- Kein Ingame-Test: Ob eine Änderung schneller macht, zeigt erst deine Testfahrt. StintPulse verspricht keine Zeitgewinne.
- Die Zuordnung Session → Setup ist manuell.
- Vergleiche berücksichtigen Tank, Temperaturen und Reifen nur als Hinweise. Verschleiß, Streckengummi und Tagesform werden nicht herausgerechnet.
- Die Oberfläche des Setup-Assistenten ist deutsch.
