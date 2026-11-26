# Projektakte – Umsetzungsbacklog und Abnahme

Stand: 12. November 2026. Bezug: [vollständiger Projektplan](PROJEKTPLAN.md).

Alle Kästchen sind offen. Dieses Dokument ist ein Arbeitsplan, kein Nachweis bereits implementierter Funktionen.

## Prioritäten und Reihenfolge

**Kern** ist für die erste vollständige Version erforderlich. **Ausbau** ergänzt die Kernversion. **Optional** wird nach Bedarf umgesetzt. Die Arbeitspakete werden innerhalb ihrer Phase in der folgenden Reihenfolge bearbeitet; unabhängige Oberflächenarbeit darf vorgezogen werden, sobald die Datenregeln feststehen.

## Phase 0 – Bestand und überprüfbarer Entwurf

- [ ] **P0.1 – Laufzeitbestand prüfen (Kern).** Pi-Versionen, SSD-Mount, freie Ports, Host-RAM/Swap/CPU und Paperless-API feststellen; keine vertraulichen Inhalte in den Bericht übernehmen. Ergebnis: aktuelle technische Ausgangslage mit Datum.
- [ ] **P0.2 – Datenregeln festlegen (Kern).** Projekt/Vorgang/Arbeitsschritt, Datumsarten, Zähler, Währung und Kostenaufteilung anhand von Beispielen modellieren. Ergebnis: keine ungeklärte Doppelzählung oder Vermischung von Rechnungs-/Arbeitsdatum.
- [ ] **P0.3 – Drei Bedienabläufe entwerfen (Kern).** Projektübersicht, kompakte Chronik, Ölwechselformular, Umbauansicht und Belegprüfung als klickbarer Entwurf. Ergebnis: auf Handybreite und am PC durchspielbar, Umbau bleibt eine Hauptzeile.
- [ ] **P0.4 – Beispiele und Import festlegen (Kern).** Anonymisierte Ölwechsel-/Umbau-/Rechnungsfälle sowie schwierige Belege für die Entwicklung zusammenstellen; BMW-Quelldatei nur lesend behandeln. Ergebnis: erwartete Felder und Summen für spätere Tests dokumentiert.
- [ ] **P0.5 – Betriebsvoraussetzungen festhalten (Kern).** Sicherungsziel, Zugriffsweg, Smartphone-Format und Gemini-Guthabenprüfung einplanen. Ergebnis: vor Installation keine unbekannte Datenablage oder ungesicherte Originalsammlung.

## Phase 1 – Grundanwendung

Voraussetzung: P0.1–P0.3.

- [ ] **P1.1 – Projektgerüst und Datenbank (Kern).** Django, ARM64-Image, eigener SQLite-Datenbestand, Migrationen, Konfigurationsvorlage und synthetische Beispieldaten. Ergebnis: auf Entwicklungsrechner reproduzierbar startbar.
- [ ] **P1.2 – Anmeldung und Dateiberechtigungen (Kern).** Persönliches Konto, serverseitige Berechtigungsprüfung, geschützte Medien/Downloads und sichere Konfiguration. Ergebnis: unangemeldete Zugriffe erhalten keine privaten Inhalte.
- [ ] **P1.3 – Projekte und Vorlagen (Kern).** Auto/Aquarium/Drohne/freies Projekt, Stammdaten, eigene Felder, Favoriten und Archivierung. Ergebnis: später geänderte Vorlage beschädigt bestehende Daten nicht.
- [ ] **P1.4 – Vorgänge und Chronik (Kern).** Wartung, Reparatur, Notiz und Umbau als aufklappbare Zeilen mit direkt verlinkter Detailseite. Ergebnis: Filter, Sortierung und Bedienung mit Touch/Tastatur funktionieren.
- [ ] **P1.5 – Schnellformulare und Entwürfe (Kern).** Ölwechsel, Datum/Zähler/Kosten, letzte Werte als Vorschlag, Autospeicherung und Schutz gegen Doppelklicks. Ergebnis: ein vollständiger Ölwechsel erzeugt genau einen Vorgang.
- [ ] **P1.6 – Änderung und Papierkorb (Kern).** Versionsstand, Konflikterkennung, Wiederherstellen gelöschter Einträge und nachvollziehbare Änderungen. Ergebnis: zwei offene Bearbeitungen überschreiben sich nicht still.

## Phase 2 – Umbauten, Bilder, Teile und Kosten

Voraussetzung: Phase 1; Kostenregeln aus P0.2.

- [ ] **P2.1 – Umbau-Dokumentation (Kern).** Status/Zeitraum/Budget, formatierter Text, sortierbare Arbeitsschritte, Probleme/Lösungen und Abschluss. Ergebnis: alle Schritte bleiben innerhalb einer Hauptchronikzeile.
- [ ] **P2.2 – Fotoupload und Galerie (Kern).** Handy-/PC-Upload, Originale, Vorschauen, Drehung, Beschriftung, Mehrfachauswahl und begrenzte Hintergrundverarbeitung. Ergebnis: fehlerhafter Upload verliert keinen gespeicherten Entwurf.
- [ ] **P2.3 – Teile und Einbauhistorie (Kern).** Teilenummern, Dokumente, Bestand/Bestellung/Einbau/Ausbau und historische Werte. Ergebnis: ersetztes Bauteil bleibt im alten Vorgang korrekt sichtbar.
- [ ] **P2.4 – Zentrale Kostenquellen (Kern).** Manuelle Ausgaben und spätere Rechnungsquellen, exakte Dezimalbeträge, Teilzuordnung, Rabatt/Versand/Gutschrift. Ergebnis: Projekt- und Umbausummen stimmen ohne Doppelzählung.
- [ ] **P2.5 – Dateilebenszyklus (Kern).** Gemeinsame Zuordnungen, Papierkorb und kontrollierte Bereinigung temporärer/verwaister Dateien. Ergebnis: Entfernen einer Zuordnung löscht kein weiterhin verwendetes Bild.

## Phase 3 – Paperless und Gemini

Voraussetzung: Phase 2; API-Zugang und bestätigte Gemini-Konfiguration für Verbindungstests.

- [ ] **P3.1 – Dauerhafte Hintergrundaufträge (Kern).** Serieller Worker, Statusmodell, Auftragsreservierung, Wiederanlauf und begrenzte Retries. Ergebnis: Worker-Neustart setzt einen gespeicherten Auftrag fort, ohne Fachdaten zu duplizieren.
- [ ] **P3.2 – Paperless-Adapter (Kern).** Versions-/Schemaabgleich, separater API-Zugang, Upload, Task-Verfolgung, Dokument-/OCR-Abruf und bestehendes Dokument auswählen. Ergebnis: keine direkten Änderungen an der Paperless-Datenbank.
- [ ] **P3.3 – Belegeingang (Kern).** Dauerhafte lokale Erstannahme, Projekt-/Umbaukontext, Status/Fehleranzeige, mehrere Seiten und spätere Zuordnung. Ergebnis: bereits angenommener Beleg bleibt nach OCR-Ausfall verfügbar.
- [ ] **P3.4 – Gemini-Adapter (Kern).** Modellwahl als Einstellung, strukturiertes Schema, OCR-Text, optionales Bild/PDF, Ausgabenprüfung, Timeout, Verbrauch und appinterne Budgets. Ergebnis: ungültige Antwort wird nicht als bestätigte Rechnung gespeichert.
- [ ] **P3.5 – Prüfvorschau (Kern).** Beleg links/oben, Felder und Positionen daneben/darunter, markierte Lücken/Abweichungen, Originalbezug und korrigierbare Vorschläge. Ergebnis: eine Korrektur bleibt bei erneutem KI-Vorschlag erhalten.
- [ ] **P3.6 – Übernehmen und Aufteilen (Kern).** Rechnung/Positionen, Teilevorschläge, Kostenallokation auf mehrere Vorgänge und bestehende manuelle Ausgabe zusammenführen. Ergebnis: zweite Bestätigung erzeugt keine zweite Kostenquelle.
- [ ] **P3.7 – Dubletten und Synchronisierung (Kern).** Dateihash, Anbieter-Auftragskennungen, bereits vorhandene Dokumente, wahrscheinliche Rechnungsdubletten, gelöschte/geänderte Belege. Ergebnis: unklarer Upload wird abgeglichen statt blind wiederholt; verschiedene echte Rechnungen werden nicht allein wegen ähnlicher Beträge zusammengeführt.
- [ ] **P3.8 – Erkennungsqualität bewerten (Kern).** 10–20 repräsentative freigegebene Belege mit bekannten Sollwerten: Datum, Betrag, Positionen, Teilenummern, mehrseitige Dokumente und unleserliche Stellen. Ergebnis: Feldgenauigkeit und nötige Korrekturen dokumentiert; keine pauschale Fehlerfreiheitszusage.

## Phase 4 – Vollständige Kernversion und Pi-Betrieb

Voraussetzung: Phasen 1–3.

- [ ] **P4.1 – Suche und Übersichten (Kern).** Berechtigte Texte/OCR/Teile/Bildbeschriftungen indexieren; Filter, Trefferkontext, Kosten- und Budgetübersicht. Ergebnis: ein Treffer in einem eingeklappten Schritt öffnet die richtige Stelle.
- [ ] **P4.2 – Einfache Aufgaben (Kern).** Projekt-/Umbauaufgaben, Status, Termin und Fälligkeitsanzeige. Ergebnis: erledigte Aufgabe kann zum passenden Dokumentationseintrag führen.
- [ ] **P4.3 – PDF und portabler Export (Kern).** Wartungshistorie, Umbau-Bericht und versionierter Daten-/Dateiexport. Ergebnis: PDF visuell geprüft; Export enthält auch tatsächliche Belegdateien und lässt sich ohne laufende Projektakte lesen.
- [ ] **P4.4 – BMW-Importentwurf (Kern).** Stammdaten/Wartungen aus der vorhandenen Quelle übernehmen, Unklarheiten markieren und manuell gegenprüfen. Ergebnis: Datums-/Kilometer-/Kostenwerte stimmen mit der bestätigten Quelle; wiederholter Import ist eindeutig erkennbar.
- [ ] **P4.5 – Sicherung und Restore (Kern).** Zusammengehörige Projektakte-/Paperless-Stände, geschützte Konfiguration, Prüfsummen, unabhängiges Ziel und dokumentierte Rücksicherung. Ergebnis: Wiederherstellung mit Datenbank, Bildern und mindestens einem Beleg in getrenntem Testverzeichnis erfolgreich.
- [ ] **P4.6 – Last- und Fehlertests (Kern).** Fotoserie, OCR, KI-Ausfall, Worker-Neustart und repräsentativer Suchbestand; Host-RAM/Swap/CPU und bestehende Dienste beobachten. Ergebnis: keine anhaltende Überlast durch die neue App; Zielwerte/Abweichungen dokumentiert.
- [ ] **P4.7 – Gezielte Installation (Kern).** Eigene SSD-Ablage, geprüfter freier Port, Compose-Web/Worker, Healthchecks und automatischer Wiederanlauf. Ergebnis: neue Anwendung startet reproduzierbar und behält ihre Daten.
- [ ] **P4.8 – Homepage und Update-Guard (Kern).** Verknüpfung, passende Sicherungsgruppe, manuelle Updates und nachvollziehbarer Versionsstand. Ergebnis: Sicherung vor einem Testupdate erfolgreich, fehlgeschlagene Sicherung verhindert Update.
- [ ] **P4.9 – Bedienung und Übergabe (Kern).** Kurzanleitung für Eintragen, Belege prüfen, Fehler erneut versuchen, Export und Wiederherstellung; erforderliche Betriebsangaben separat. Ergebnis: tägliche Nutzung ohne direkte Datenbank-/Containerarbeit möglich.

## Phase 5 – Wartung und Hobbymodule

- [ ] **P5.1 – Wiederkehrende Wartungen (Ausbau).** Zeit-/Zählerintervalle, Kombination „was zuerst eintritt“, veralteter Zählerstand und verknüpfte Erledigung. Ergebnis: alte Nachträge setzen den Zyklus nicht versehentlich zurück; keine doppelten Fälligkeiten.
- [ ] **P5.2 – Aquarium-Messwerte (Ausbau).** Wählbare Werte, Einheiten, Sollbereiche, Verlaufskurven und CSV. Ergebnis: reale Messreihe lässt sich zügig eintragen und nachvollziehbar anzeigen.
- [ ] **P5.3 – Drohne/Akkus (Ausbau).** Flüge, Dauer, Komponenten, Akkukennungen und optionale Zyklen. Ergebnis: Projektchronik bleibt auch ohne aktiviertes Flugprotokoll nutzbar.
- [ ] **P5.4 – Word-Berichte (Ausbau).** Bearbeitbare Wartungs-/Umbaudokumentation mit derselben Datenbasis wie PDF. Ergebnis: gerenderter Bericht ohne abgeschnittene Tabellen, fehlende Bilder oder überlagerte Texte.
- [ ] **P5.5 – Weitere Vorlagen (Ausbau).** Neue Hobbytypen und wiederverwendbare Formularvorlagen. Ergebnis: weitere Projekte benötigen keine Änderungen am Programmcode für gewöhnliche Zusatzfelder.

## Phase 6 – Optionale Funktionen

- [ ] **P6.1 – Ollama und lokale GPU (Optional).** Gemeinsamer Anbieteradapter, Heimnetzverbindung und Warteschlange bei ausgeschaltetem PC. Ergebnis: Rechnungen mit identischen Sollwerten gegen Gemini vergleichen; kein unbeabsichtigter Cloud-Fallback.
- [ ] **P6.2 – Spracheingabe (Optional).** Gesprochene Notiz in einen prüfbaren Entwurf umwandeln. Ergebnis: Datum, Zahlen und Projektzuordnung bleiben vor Übernahme korrigierbar.
- [ ] **P6.3 – Fragen an die eigene Dokumentation (Optional).** Nur berechtigte Inhalte verwenden, Quellen anzeigen und fehlendes Wissen markieren. Ergebnis: Antwort verweist direkt auf tatsächlich vorhandenen Eintrag/Beleg.
- [ ] **P6.4 – Offline-Entwürfe (Optional).** Lokale Eingaben und Fotos bis zur bestätigten Synchronisierung aufbewahren, Konflikte/Anmeldung behandeln. Ergebnis: Verbindungsabbruch während Fotoaufnahme verursacht keinen Datenverlust oder doppelten Import.
- [ ] **P6.5 – Benachrichtigungen (Optional).** Einen gewählten Zustellweg anbinden; Status/Zustellung nachvollziehbar halten. Ergebnis: Testfälligkeit kommt einmal am gewünschten Ziel an.
- [ ] **P6.6 – Fernzugriff (Optional).** Gewählte Domain, HTTPS, Anmeldung/Schutz und erreichbare Belegvorschauen. Ergebnis: mobile Nutzung außerhalb des Heimnetzes einschließlich Bildern funktioniert mit den vorgesehenen Rechten.
- [ ] **P6.7 – QR-Codes, Bildmarkierungen und Freigaben (Optional).** Pro Funktion einen konkreten Alltagstest umsetzen. Ergebnis: lesbare QR-Ziele bzw. gezielt ausgewählte geteilte Inhalte ohne unbeabsichtigte private Zusatzdaten.
- [ ] **P6.8 – Bestehende Dienste verknüpfen (Optional).** Gewünschte Verweise oder definierte Synchronisierung zu Vikunja, BookStack oder Home Assistant. Ergebnis: für synchronisierte Daten ist jeweils eindeutig festgelegt, welches System maßgeblich ist.

## Zentrale Abnahmematrix

| Test | Eingabe/Situation | Erwartetes Ergebnis |
|---|---|---|
| A01 | Ölwechsel mit Datum, km, Öl, Filter und Kosten | Genau ein Vorgang, sauber formatiert, später unverändert nachvollziehbar. |
| A02 | Umbau mit fünf Schritten und zwölf Fotos | Eine Hauptzeile; alle Details nach Öffnen sichtbar, Bildreihenfolge korrekt. |
| A03 | Eine Rechnung über 100 EUR, 60/40 auf zwei Projekte | Projektsummen 60/40, Gesamt 100, keine zusätzliche Addition der Rechnungssumme. |
| A04 | Versand/Rabatt/Gutschrift sowie teilweise zugeordneter Beleg | Exakte Summen und erkennbarer Restbetrag; Vorzeichen und Währung erhalten. |
| A05 | Gekauftes Öl vor späterem Ölwechsel | Einkauf und Arbeitsdatum getrennt; keine vorzeitige Wartungserledigung. |
| A06 | Gleiches Formular zweimal senden | Genau ein bestätigter Vorgang. |
| A07 | Gleicher Beleg erneut, Importtimeout und Worker-Neustart | Status abgleichen; keine doppelte Rechnung/Kostenquelle. |
| A08 | OCR-/KI-Auswertung mit fehlendem/falschem Datum und Betrag | Lücken/Abweichungen sichtbar, Korrektur möglich, keine erfundenen Bestätigungen. |
| A09 | Paperless/Gemini nicht erreichbar oder Budget erreicht | Manuelle Arbeit nutzbar; gespeicherte Aufträge warten mit verständlichem Status. |
| A10 | Derselbe Bildanhang an zwei Stellen, eine Zuordnung entfernen | Andere Zuordnung und Datei bleiben erhalten. |
| A11 | Vorlage, Stammdaten oder aktuell verbautes Teil ändern | Alte Wartungs-/Einbauangaben behalten ihren damaligen Inhalt. |
| A12 | Alter Zählernachtrag und expliziter Zählertausch | Historische Werte zulässig; Erinnerung berechnet sich nicht still von einer falschen Basis. |
| A13 | Zwei Bearbeitungen desselben Eintrags | Zwischenzeitliche Änderung erkannt und Konflikt verständlich aufgelöst. |
| A14 | Suche nach Teilenummer im Rechnungs-OCR/Arbeitsschritt | Richtiger berechtigter Treffer mit direktem Kontext. |
| A15 | Unangemeldeter Direktaufruf von Bild/Download/Belegvorschau | Keine privaten Inhalte; Anmeldung bzw. Zugriff verweigert. |
| A16 | PDF-/ZIP-Export auf anderem Rechner | Lesbare, vollständige Inhalte einschließlich ausgewählter Originalbelege. |
| A17 | Wiederherstellung nach simulierter leerer App-Datenablage | Projekt-, Medien- und Paperless-Verknüpfungen aus zusammengehörigem Stand funktionieren. |
| A18 | Große Fotofolge und Hintergrundverarbeitung auf Pi 4 | Keine blockierte Eingabe; begrenzte Verarbeitung und vertretbare Gesamtlast. |
| A19 | Kaputtes Archiv bzw. fehlgeschlagene Update-Sicherung | Update startet nicht; vorhandene nutzbare Version/Daten bleiben verfügbar. |
| A20 | Smartphone-Foto, Rücknavigation, Entwurf wieder öffnen | Text und bestätigte Uploads erhalten; Speichern erzeugt keine Dublette. |

## Abschluss einer Phase

Eine Phase ist erst abgeschlossen, wenn ihre Ergebnisse vorliegen und die relevanten Abnahmeszenarien erfüllt sind. Normale Funktionen werden mit sinnvollen fachlichen Tests geprüft; Layout und Handyabläufe zusätzlich visuell. Für integrationsabhängige Schritte gehören Ausfall-/Wiederholungsfälle ausdrücklich dazu. Testergebnisse, bekannte Grenzen und offene Punkte werden neben diesem Backlog dokumentiert.
