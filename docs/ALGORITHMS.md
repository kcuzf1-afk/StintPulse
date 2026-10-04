# Algorithmen, Schwellenwerte und Grenzen

Alle Regeln laufen lokal. Grenzwerte stehen in `Settings` und können in der UI geändert werden, soweit unten vermerkt. Fixe Detektorparameter sind als Code-Konstanten sichtbar. `estimated=true` bezeichnet Näherungen, nicht zusätzliche Spielmesswerte. Kurvennummern sind fortlaufend erzeugte Labels.

| Erkennung | verwendete Messwerte | Regel / Standard | Grenze / Konfiguration |
|---|---|---|---|
| Bremsbeginn / Zone | Bremspedal, Zeit, Distanz | Pedal >0,08 für ≥0,12 s | `brake_threshold`; keine gemessene Leitungsdruckanalyse |
| Bremslösung | Ende der Bremszone | Ende des letzten Samples über Schwelle | Schwelle verändert den Punkt; keine absolute Fahrzeug-Bremskraft |
| Trail-Braking | Bremse und Steering raw | Bremse über Schwelle, abs(steer)>0,08, ≥0,2 s | Input-Überlappung als Schätzung; Input ist nicht automatisch Radwinkel |
| Turn-in | Steering raw | abs(steer)>0,1 für ≥0,3 s | Schätzung; normierte Inputs und Fahrzeuglenkung variieren; fixe Schwelle im Code |
| Apex / Minimum | Speed auf 5-m-Raster | lokales Minimum; ≥15 km/h Prominenz im 200-m-Fenster; 80 m Mindesttrennung | Mindestgeschwindigkeit als Proxy; Chicanen können zusammenfallen; Minima an S/F nicht zyklisch behandelt |
| Vollgas | Gaspedal | ≥0,97 für ≥0,3 s | `full_throttle`; Antrieb/Traktion nicht daraus allein beweisbar |
| Gangwechsel | Gang | Änderung gegenüber vorheriger Rasterstation | räumlich auf 5 m quantisiert; Schaltvorgänge im Raster können verloren gehen |
| Radblockieren | Raddrehzahl, statischer Radius, lokale Längsgeschwindigkeit, Bremse | abs(ω)×r / abs(vz) <0,65, Bremse über Schwelle, Speed >30 km/h; ≥0,12 s | `lock_ratio`; Radius/Kurvenfahrt/Schlupf machen dies zur Schätzung; fehlende Werte → null |
| Wheelspin | gleiche Werte, Gas | Rad/Fahrzeug-Verhältnis >1,2, Gas >0,3; ≥0,12 s | `spin_ratio`; keine automatische Bestimmung angetriebener Achsen |
| Fahrzeugschlupf | lokale vx/vz, Speed | abs(atan2(vx,vz)) >8°, Speed >40 km/h, ≥0,2 s | `slip_angle_deg`; Körper-Schräglauf ist nicht automatisch Übersteuern |
| Untersteuern | kalibrierte Lenkung, Speed, Gierrate, Radstand, Lenkübersetzung | erwartete Gierrate = v/L×tan(Radwinkel); Ist/Erwartet <0,6 für ≥0,3 s | nur mit `steering_lock_deg`, `wheelbase_m`, `steering_ratio`; lineares Einspurmodell, Schätzung |
| Übersteuern | wie oben | Ist/Erwartet >1,4 für ≥0,3 s | wie oben; Reifenkräfte, Reifenwinkel und Fahrbahnneigung nicht vollständig bekannt |
| Gegenlenken | Steering raw, Gierrate | unterschiedliche kalibrierte Vorzeichen während Kurvenfahrt ≥0,2 s | zusätzlich `steering_yaw_sign` nötig; ohne Kalibrierung keine Klassifikation |
| Instabiles Bremsen | Bremspedal in einer Zone | ≥4 Richtungswechsel mit Rasteränderung >0,035 | kann ABS-unabhängige Pedalkorrektur oder Unebenheit sein; keine automatische Ursache |
| Gas-Korrektur / mögliches frühes Gas | Gas, Bremse, Lenkung | Kurvenfahrt mit wenig Bremse; Gas >0,7 und anschließende Änderung <−0,08 | gemessene Korrektur, Ursache nur Hypothese; nicht pauschal als Fehler ausgegeben |
| Lupfen auf Gerade | Gas, Bremse, Lenkung, Speed | Gas <0,8, Bremse <0,03, abs(steer)<0,07, Speed >50, ≥0,2 s | Kandidat `lift_segment`; Verkehr/Flaggen können nötig machen; kein pauschales „unnötig“ |
| Off-Track | numberOfTyresOut, Strafzeit, Pit | ≥3 Reifen außerhalb oder positive Strafe | Grenzen mod-/spielabhängig; abgeleitete Rundengültigkeit, kein offizielles Flag |
| Fahrlinienunterschied | X/Z an gleicher Spline-Distanz | euklidischer Abstand in Metern | GPS-/Spline-Vergleich, keine bewiesene Ideallinie |
| Zeitgewinn/-verlust | Zeitverläufe beider Runden | t_lap(s)−t_ref(s), Abschnittsverlust als Differenz zweier Delta-Werte | beide Runden müssen Quelle/Auto/Track/Layout teilen; fehlende Bereiche bleiben null |
| Temperaturwarnung | Core pro Rad | >105 °C mindestens 2 s für Coach; UI unter70/über105 | `temp_cold`, `temp_hot`; kein universelles Reifenoptimum |
| Druckwarnung | Pressure psi pro Rad | unter20 / über35 psi | `pressure_low`, `pressure_high`; Grenzen pro Fahrzeug prüfen |
| Restreichweite | Kraftstoff zu Start/Ende Referenz | aktueller Kraftstoff / gemessener Verbrauch pro Runde | nur positiver Verbrauch >0,05 L; Refuelling/Boxen verfälschen; Schätzung |
| Mini-Sektoren | Distanz, Zeit | 20 gleich lange Abschnitte; beste gültige Zeit pro Abschnitt | Kombination ist theoretisch und nicht automatisch fahrbar |
| Konstanz / Ausreißer | vollständige gültige Runden | Mittelwert, Populations-σ, letzte5-σ, robustes MAD-Ausreißermaß | Schwellwert 3×1,4826×MAD; erst bei MAD>0,01 s |
| Kraftstoffeffekt | Start-Kraftstoff, Rundenzeit | lineare Korrelation, ab8 Runden und >3 L Spanne | keine Kausalität; Reifen, Verkehr, Lernfortschritt unkontrolliert |

## Coach-Regeln

Referenz-Kurvenabschnitte werden um Mindestgeschwindigkeitspunkte aufgeteilt. Pro Abschnitt werden Zeit, Ein-/Ausgangsgeschwindigkeit, Minimum, Apex-Proxy, Bremsbeginn/-Peak/-Release, Turn-in, Vollgas und Schaltpunkte berechnet. Ein gemessener Abschnittsverlust unter 0,05 s wird nicht als Tipp priorisiert.

Priorität je Abschnitt: Bremsbeginn >6 m früher, sonst Minimum >3 km/h niedriger, sonst Vollgas >0,12 s später relativ zum Mindestgeschwindigkeitspunkt, sonst reine Abschnittsverlustmeldung ohne belegte Ursache. Hinweise werden nach beobachtetem Zeitverlust sortiert und auf fünf begrenzt. Temperaturhinweise haben kein behauptetes Zeitpotenzial.

„High“ bedeutet mindestens 30 aufgezeichnete Samples pro Sekunde bei vollständiger, abgeleitet gültiger Runde und Referenz. Sonst „Medium“. Diese Stufe beschreibt Datenabdeckung, keine mathematische Wahrscheinlichkeit der Ursachenhypothese. Ungültige Daten dürfen als Vergleich geöffnet werden, werden aber nicht automatisch PB.

## Normalisierung und Datenlücken

Mehrere Runden müssen keine gleiche Erfassungsrate haben. Stationen sind auf der Streckenspline definiert, damit unterschiedliche Fahrlinien nicht über deren unterschiedlich lange tatsächliche Wege verschoben werden. Zwischen hinreichend nahen Samples wird linear interpoliert; Gang bleibt stufig. 100-m-Lücken und unbeobachtete Randbereiche werden nicht interpoliert. Kein Nullwert wird als künstlicher Sensorersatz eingefügt. Mögliche Start-/Ziel-Interpolation nutzt offizielle Rundenzeit und wird als synthetischer Randpunkt gekennzeichnet.

DRS-Zonen, Reifen-Idealtemperaturen, aerodynamische Schadenwirkung und Motorzustand werden nicht aus unbelegten Annahmen geschätzt. Eine spätere KI kann nur als zusätzliches, zustimmungsabhängiges Modul ergänzt werden; aktuell existiert kein Daten-Uploadpfad.
