# Projektakte – konkrete Bedienabläufe

Version 1.1, 12. November 2026. Die Beispiele verwenden ausschließlich erfundene Angaben. Sie legen das Verhalten fest, nicht die endgültige grafische Gestaltung.

## 1. Chronik am PC und am Handy

PC-Beispiel:

```text
BMW – Demonstration                            [+ Hinzufügen]
Überblick | Chronik | Umbauten | Teile | Dokumente | Aufgaben

[Suche in diesem Projekt ...] [Typ: Alle] [Zeitraum: Alle]

Datum          Vorgang                    km       Status       Kosten
12.11.2026  >   Ölwechsel                 165.200   Erledigt       75,00 EUR
01.–12.11. >   Soundsystem-Umbau                    Abgeschlossen 1.250,00 EUR
```

Auf dem Handy werden dieselben Angaben in einer kompakten Zeile mit zweiter Textzeile dargestellt: Titel/Datum zuerst, danach Status/Kosten und optionale km. Der Bildschirm darf nicht zur Bedienung von Hauptaktionen horizontal gescrollt werden müssen.

Tippen auf „Soundsystem-Umbau“ klappt darunter eine Kurzansicht auf:

```text
v Soundsystem-Umbau            01.–12.11.2026    1.250,00 EUR
  Ergebnis/Kurzbeschreibung ...
  5 Arbeitsschritte · 12 Bilder · 3 Rechnungen
  [Details öffnen] [Bearbeiten]
```

Erneutes Tippen klappt sie zu. Die Kurzansicht lädt zunächst Vorschaubilder und eine Zusammenfassung; alle Originalbilder werden erst bei Bedarf geladen. Die eigene Detailseite ist über eine beständige URL erreichbar und zeigt Beschreibung, Schritte, Bilder, Teile und Belege. Zurück führt in die vorherige Chronik mit erhaltenem Filter und möglichst erhaltener Scrollposition.

## 2. Ölwechsel in einem kurzen Formular

Ausgangslage: Projekt „BMW – Demonstration“ ist geöffnet, Vorlagentyp Auto.

1. „Hinzufügen → Wartung → Ölwechsel“ auswählen.
2. Projekt ist vorausgewählt. Datum ist editierbar; für das Beispiel auf 12.11.2026 setzen.
3. Kilometerstand `165.200`, Öl `Demo 5W-40`, Menge `5,0 l`, Filter `DEMO-FILTER-01` und bekannte Kosten `75,00 EUR` eingeben.
4. Optional eine Rechnung hinzufügen oder eine vorhandene Ausgabe verbinden. Upload-/OCR-Status darf das manuelle Speichern nicht blockieren.
5. „Speichern“ erzeugt einen Vorgang; bei wiederholter gleicher Übermittlung bleibt es einer.
6. Die Chronik zeigt `12.11.2026 · Ölwechsel · 165.200 km · 75,00 EUR`. Öffnen zeigt die gespeicherten Öl-/Filterangaben.

Wird später in den Stammdaten anderes Öl eingetragen, zeigt dieser Ölwechsel weiterhin `Demo 5W-40`. Unvollständige Eingabe bleibt als Entwurf erkennbar; automatische Entwurfsspeicherung ist keine Bestätigung einer durchgeführten Wartung.

## 3. Umbau über mehrere Tage

1. Im Auto „Hinzufügen → Umbau“ wählen. Titel `Soundsystem-Umbau`, Beginn 01.11.2026, Status `Geplant`, Budget `1.400,00 EUR`.
2. Auf der Detailseite Ziel und Ausgangszustand beschreiben.
3. Fünf Arbeitsschritte anlegen und sortieren. Zwölf selbst erzeugte Testbilder auf die Schritte verteilen, beschriften und zwei davon als vorher/nachher kennzeichnen.
4. Drei Belege über `500,00`, `450,00` und `300,00 EUR` hinzufügen und vollständig diesem Umbau zuordnen.
5. Status auf abgeschlossen und Ende auf 12.11.2026 setzen.

Erwartung: Eine Chronikzeile, Zeitraum 01.–12.11.2026, tatsächliche Kosten 1.250,00 EUR und verbleibendes Budget 150,00 EUR. Öffnen zeigt fünf Schritte, zwölf Bilder und drei Belege. Der standardmäßige Sortierbeginn bleibt 01.11.2026; der spätere Abschluss oder ein späterer Foto-Upload erzeugt keine zusätzliche Zeile.

## 4. Rechnung fotografieren und prüfen

1. Im Ölwechsel oder Umbau „Rechnung hinzufügen → Foto/Datei“ wählen. Die Herkunft wird als Zielvorschlag gespeichert.
2. Nach dauerhafter Annahme erscheint die Rechnung im Eingang. Zustände: „Wartet auf Paperless“, „OCR läuft“, „Wartet auf KI“ und schließlich „Prüfung erforderlich“.
3. Die Prüfansicht zeigt am PC Original links und Datenfelder/Positionen rechts. Am Handy erst den Beleg, darunter die Felder; Wechsel zwischen beiden Bereichen ohne Verlust von Änderungen.
4. Für `beispiele/BELEG_DEMO_OEL.txt` sollen Rechnungsnummer `DEMO-OEL-001`, Rechnungsdatum 10.11.2026, Summe 75,00 EUR, Motoröl, Filter und Versand vorgeschlagen werden. Kilometerstand und Leistungsdatum bleiben leer.
5. Die Person prüft/korrigiert die Werte, bestätigt das Ziel und wählt „Übernehmen“. Bei einer vorhandenen manuellen Ausgabe über 75,00 EUR wird „Mit vorhandener Ausgabe verbinden“ angeboten.

Erwartung: Die Rechnung wird einmal übernommen. Der Ölwechsel bleibt am 12.11.2026; die Rechnung besitzt weiterhin ihr Rechnungsdatum 10.11.2026. Die Gesamtkosten steigen beim Verbinden nicht von 75 auf 150 EUR. Erneutes Übernehmen erzeugt keinen zweiten Datensatz.

Papierbeleg nicht vorhanden, aber Dokument schon in Paperless: „Vorhandenes Dokument auswählen“ führt zur gleichen Prüfansicht. Kein zweiter Upload nötig.

## 5. Eine Rechnung aufteilen

`beispiele/BELEG_DEMO_SPLIT.txt` enthält Positionen Auto 60,00 EUR und Aquarium 40,00 EUR, Versand 10,00 EUR und Rabatt 5,00 EUR. Summe: 105,00 EUR.

Für dieses Beispiel wählt die Person ausdrücklich die proportionale Aufteilung der zusätzlichen 5,00 EUR im Verhältnis 60/40. Damit erhalten Auto 63,00 EUR und Aquarium 42,00 EUR. Eine beliebige andere fachlich zulässige Verteilung kann manuell gewählt werden, solange die Gesamtsumme stimmt.

Bei nur 60,00 EUR Zuordnung zeigt die Oberfläche 45,00 EUR offenen Rest. Überzuordnung wird bei abschließender Übernahme mit der betroffenen Summe erklärt. Eine anschließend ausdrücklich erfasste Gutschrift von 5,00 EUR für das Auto reduziert dessen Kosten auf 58,00 EUR; Aquarium bleibt 42,00 EUR.

## 6. Ausfall und Wiederholung

- **Paperless aus:** Foto ist im Eingang gespeichert, Zustand/Fehler lesbar, erneuter Versuch möglich. Ölwechsel/Notiz lassen sich trotzdem manuell erfassen.
- **KI aus oder Budget erreicht:** OCR-Beleg bleibt vorhanden; manuell ausfüllen oder später auswerten.
- **Verbindung nach Upload unklar:** Auftrags-/Dokumentstatus abgleichen. Kein automatisch neu gebuchter Beleg.
- **Worker-Neustart:** gespeicherten Auftrag an der letzten bestätigten Stufe fortsetzen.
- **Doppelklick auf Speichern/Übernehmen:** ein Vorgang/eine Kostenquelle.
- **Gleiche Datei noch einmal:** vorhandenen Beleg zum Verknüpfen anbieten oder als Dublette kennzeichnen. Ähnlicher Betrag allein löst keine Zusammenführung aus.
- **Zwei Bearbeitungen:** die zweite Speicherung erkennt die zwischenzeitliche Änderung und bietet eine verständliche Auflösung, statt Werte still zu überschreiben.

## 7. Suche, Export und Wiederherstellung

Suche nach `DEMO-FILTER-01` findet den Ölwechsel und den zugeordneten OCR-Beleg. Der Treffer führt zum passenden Vorgang oder Beleg, auch wenn der Umbau vorher eingeklappt war.

Ein PDF-Export der Wartung enthält die bestätigten Angaben; ein Umbau-PDF zusätzlich Schritte, ausgewählte Bilder und Kosten. Ein vollständiger ZIP-Export enthält versionierte Daten, Dateien und ausgewählte Belegoriginale, sodass der Inhalt auf einem anderen Rechner nachvollziehbar ist.

Für die Betriebsabnahme eine konsistente Sicherung in einer getrennten Testumgebung wiederherstellen. Mindestens Projekt, Ölwechsel, Umbau, Foto und Paperless-Beleg müssen aus dem zusammengehörigen Stand wieder auffindbar und aufrufbar sein.
