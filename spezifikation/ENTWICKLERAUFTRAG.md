# Projektakte – präzisierter Entwicklerauftrag

Version 1.1, 12. November 2026. Dieses Dokument legt eine umsetzbare Ausgangsspezifikation fest und präzisiert den vollständigen Projektplan. Zusätzliche Detailentscheidungen sind empfohlene Standards für die Entwicklung, bis der Auftraggeber sie ausdrücklich ändert.

## 1. Erfolgskriterium

Der Nutzer kann am Handy einen Ölwechsel oder ein Rechnungsfoto erfassen und später am PC eine sauber formatierte Projektchronik lesen. Er kann einen mehrtägigen Umbau mit vielen Bildern dokumentieren, während dieser in der Hauptchronik eine einzige Zeile belegt. Die Kosten stimmen auch bei mehreren Belegen oder Aufteilung einer Rechnung. Alle Daten können einschließlich Fotos und Belegen gesichert, wiederhergestellt und exportiert werden.

Ein reines Dashboard, eine Sammlung externer Links oder eine generische Tabelle ohne diese Bedienabläufe erfüllt den Auftrag nicht.

## 2. Umfang der ersten vollständigen Version

Die erste vollständige Version umfasst **alle als Kern bezeichneten Arbeitspakete aus Phase 0 bis einschließlich Phase 4** des Backlogs. Zwischenstände werden entsprechend als Prototyp oder Teilstand bezeichnet.

| Pflichtbereich | Nachweis |
|---|---|
| Anmeldung, persönliche Projekte und Vorlagen | Auto, Aquarium, Drohne und freies Projekt anlegbar; Daten und Dateien geschützt. |
| Stammdaten, eigene Felder, Archivierung | Felder ohne Programmänderung ergänzbar; alte Werte bleiben erhalten. |
| Wartung/Reparatur/Notiz/Umbau | Kurze Formulare, Entwürfe, Korrekturen und Papierkorb. |
| Kompakte Chronik | Aufklappbare Zeilen und dauerhaft verlinkbare Details; ein Umbau bleibt eine Zeile. |
| Umbauten | Arbeitsschritte, formatierter Text, geordnete Bilder mit Beschriftung, Teile, Budget und Status. |
| Teile und Einbauhistorie | Beschaffung, Ein-/Ausbau und damalige Werte nachvollziehbar. |
| Belege | Handyfoto/Upload, Eingang, vorhandene Paperless-Dokumente auswählen, mehrere Belege pro Vorgang. |
| Paperless/Gemini | Nachvollziehbare Hintergrundverarbeitung, KI-Vorschläge und ausdrückliche Übernahme nach Prüfung. |
| Kosten | Manuelle Ausgaben und Belege, Teilzuordnung über mehrere Projekte, Versand/Rabatt/Gutschrift, korrekte Summen. |
| Suche und einfache Aufgaben | Texte, Teile, Bilderbeschriftungen und verknüpfter OCR-Text auffindbar; Aufgaben mit Termin/Status. |
| Berichte und Migration | PDF für Historie/Umbau, portabler vollständiger Datenexport, prüfbarer Importentwurf für Bestandsdaten. |
| Betrieb | ARM64-Start, Sicherung/Restore, Lastprüfung auf Zielhardware, Dokumentation und Integration in vorhandene Verwaltung. |

Ausbau nach der Kernversion: wiederkehrende Wartungsregeln, Aquarium-Messkurven, Drohnen-/Akkuprotokolle und Word-Berichte. Optionale weitere Funktionen: Ollama, Sprache, Offline-Entwürfe, Fragen an die Dokumentation, Benachrichtigungen, QR-Codes und Fernzugriff. Ein technischer Adapter für spätere KI-Anbieter ist schon in der Kernversion vorzusehen; der lokale Modellbetrieb selbst ist Ausbau.

## 3. Eindeutige Fachregeln

### 3.1 Hierarchie und Darstellung

- **Projekt** ist der langfristige Gegenstand, etwa das Auto oder Aquarium.
- **Vorgang** ist eine Wartung, Reparatur, Notiz oder ein Umbau innerhalb eines Projekts.
- **Arbeitsschritt** gehört zu einem Umbau und wird nicht als zusätzlicher eigenständiger Chronikvorgang ausgegeben.
- „Unterprojekt“ im allgemeinen Entwurf bezeichnet in der Kernversion einen Umbau-Vorgang; eine zusätzliche rekursive Projekthierarchie ist dafür nicht erforderlich.
- Ein Klick auf die Zeile öffnet deren Vorschau, ein zweiter schließt sie. „Details öffnen“ führt auf eine eigene URL. Mehrere Zeilen dürfen gleichzeitig offen sein.
- Hauptchronik standardmäßig nach Chronikdatum absteigend; bei Gleichstand nach stabiler interner ID. Unbekanntes Datum steht am Ende. Eine Bildänderung beeinflusst die Sortierung nicht.

### 3.2 Datum, Eingabe und Historie

- Tageswerte werden als Datum ohne Zeitzonenverschiebung behandelt. Technische Zeitstempel intern UTC, Anzeige Europe/Berlin.
- Chronikdatum, Rechnungsdatum, Leistungsdatum, Uploadzeit und Bildaufnahmezeit sind getrennte Felder.
- Beim schnellen Arbeitseintrag ist zunächst heute vorgeschlagen und editierbar. Beim Beleg ist ein erkennbares Rechnungsdatum ein Vorschlag; ein Arbeitsdatum entsteht daraus nicht automatisch.
- Der Umbau zeigt Beginn/Ende und verwendet zunächst den Beginn als Chronikdatum.
- Bei nur Monat/Jahr wird die Genauigkeit mitgespeichert. Sortierung innerhalb dieses Monats nutzt intern den Monatsanfang, Anzeige bleibt „MM/JJJJ“; bei nur Jahr entsprechend Jahresanfang, Anzeige nur Jahr. Ein solcher Hilfswert wird nicht als exakter Arbeitstag exportiert.
- Deutsche Eingaben wie `165.200` km und `75,00` EUR werden entsprechend verarbeitet. Widersprüchliche Trennzeichen werden zur Korrektur markiert.
- Ein später geänderter Teile- oder Öl-Stammdatensatz verändert keine bestätigte alte Wartung.
- Optionaler Zählerstand: Zahl und Einheit sowie Bezug zum konkreten Zähler. Fehlend ist kein Wert von null.

### 3.3 Kosten ohne Doppelzählung

- Geldbeträge exakt als Dezimalwert mit Währung; Testdateien verwenden Dezimalstrings.
- Jede Rechnung bzw. manuelle Ausgabe ist genau eine Kostenquelle. Listen zeigen deren Zuordnungen zum jeweiligen Vorgang/Projekt.
- Gleiches Rechnungsdokument an zwei Stellen verlinken erzeugt keine zweite Kostenquelle. Reine Dokumentverknüpfung bucht noch keine Ausgabe.
- Teilepositionen, Versand und Rabatt werden innerhalb einer Rechnung abgestimmt. Ein Projekt erhält ausschließlich den ihm zugeordneten Betrag.
- Positionen können insgesamt oder anteilig zugeordnet werden. Offener Rest bleibt sichtbar. Eine Überzuordnung ist beim abschließenden Übernehmen zu verhindern.
- Gutschriften sind negative Quellen. Ausgewiesene Projektkosten enthalten deren zugeordneten Betrag.
- Arbeitskosten innerhalb eines Umbaus werden diesem zugerechnet. Die zusätzlich sichtbare Umbausumme ist eine Aggregation, keine zweite Ausgabe.
- Wird eine manuelle Ausgabe später mit einer Rechnung ergänzt, bietet die Oberfläche „Mit vorhandener Ausgabe verbinden“ statt erneut zu buchen.
- `unbekannt`, `kostenlos` und `bekannter Betrag` sind getrennte Zustände.

### 3.4 Dokumente, KI und Übernahme

- Ein angenommener Upload wird lokal dauerhaft gespeichert, bevor die Verarbeitung als gestartet bestätigt wird.
- Paperless verwaltet den Originalbeleg. Projektakte verwaltet Fotos der Dokumentation, Fachangaben und Zuordnungen. Eine abgeschlossene Übergabe ist erst nach bestätigtem Paperless-Dokument gültig.
- Vorhandenes Paperless-Dokument auswählen und einen neuen Beleg hochladen sind gleichberechtigte Wege.
- Der KI-Schritt erzeugt einen Vorschlag. „Übernehmen“ bestätigt ihn genau einmal. Beim bloßen Öffnen einer Vorschau wird nichts gebucht.
- Fehlende Daten bleiben leer. Ein Rechnungstext mit Produktnamen belegt noch keinen erfolgten Einbau oder Ölwechsel.
- Vorschläge sind mit Original/OCR vergleichbar und korrigierbar. Ungültiges Schema, unplausible Summen und Teilfehler bekommen einen verständlichen Status.
- Eigenständig abgeänderte Werte werden bei einer neuen Auswertung erhalten bzw. als Konflikt angeboten.
- Dateiidentität, Paperless-ID und Einreichungskennung verhindern technische Dubletten. Ähnliche Rechnungsnummern/Beträge lösen eine Nachfrage in der Oberfläche aus; sie berechtigen nicht zum automatischen Zusammenführen verschiedener Belege.
- Ein unklar beantworteter Upload wird zuerst abgeglichen. Derselbe request wird nicht blind erneut als neues Dokument eingereicht.
- Beleginhalt wird als unzuverlässige Datenquelle behandelt; enthaltene Handlungsanweisungen ändern weder Systemregeln noch Berechtigungen.

## 4. Standardentscheidungen für die Entwicklung

Diese Standards machen eine Umsetzung mit Beispieldaten möglich, während die späteren Betriebsangaben noch fehlen:

| Thema | Vorgabe |
|---|---|
| Architektur | Django 5.2 LTS mit geprüftem Patchstand, serverseitiges HTML, HTMX, SQLite auf SSD, ein serieller Hintergrund-Worker. |
| Datenzugang | Fachlogik in wiederverwendbaren Services; atomare Übernahme von Vorschlag und Kostenzuordnungen; keine externen Netzaufrufe innerhalb lang gehaltener DB-Transaktionen. |
| Entwicklung | Mit Testdaten lokal startbar, austauschbare Paperless-/KI-Testadapter; Live-Schlüssel sind keine Voraussetzung für die Grundentwicklung. |
| Sprache | Deutsch, EUR als Standard, Europe/Berlin; Währung trotzdem pro Kostenquelle gespeichert. |
| Nutzer | Ein persönlicher Nutzer in Version 1, Eigentümerprüfung an allen Daten-/Dateizugriffen. |
| Entwürfe | Speicherung nach kurzer Eingabepause, sichtbarer Speicherstatus, Wiederaufnahme; fertige Bestätigung weiterhin ausdrücklich. |
| Dateien | JPEG/PNG/WebP/PDF; vorläufig 20 MB je Foto, 50 MB je Belegdatei, serverseitig konfigurierbar; echte Mobiltests vor Freigabe. |
| Papierkorb | Vorgeschlagener Standard 30 Tage. Endgültiges Entfernen nur nach Prüfung noch vorhandener Zuordnungen. Paperless-Belege werden beim Löschen einer App-Zuordnung nicht mitgelöscht. |
| KI | Gemini-Adapter; bevorzugtes günstiges stabiles Modell vor Integration verifizieren. Modell und API-Basis konfigurierbar. |
| KI-Budget | Vorgeschlagen 1 USD Warnung/2 USD appinterne Monatssperre, einstellbar. Verfügbares Abo-Guthaben wird nicht erfunden oder als hartes Limit vorausgesetzt. |
| Fehlerbehandlung | Fortschrittszustände, Timeout, begrenzte verzögerte Wiederholungen, manuelles Wiederholen, fehlertoleranter Neustart. |
| Auftragsstatus | Fachlich: hochgeladen, wartet auf Paperless, OCR läuft, wartet auf KI, Prüfung erforderlich, übernommen, fehlgeschlagen; technische Unterzustände zulässig. |
| Oberfläche | Kompakte helle Grundansicht, eine Akzentfarbe, lesbare Statuslabels. Dichte Tabellen am PC; kompakte aufklappbare Zeilen am Handy. |
| Erreichbarkeit | Heimnetz zuerst; externe Erreichbarkeit ist eine spätere Ausbaustufe. |

Exakte Bibliotheks-Patchstände, Indexdetails und interne Modulnamen bestimmt der Entwickler passend zur verifizierten Zielumgebung und dokumentiert sie. Änderungen am Produktverhalten oder an diesen Architekturgrundsätzen werden mit Begründung vorgeschlagen, nicht still eingeführt.

## 5. Erwarteter Ablauf und Lieferumfang

1. Mit diesen Unterlagen drei vollständige klickbare Abläufe als ersten Lieferstand darstellen. Noch offene Farb-/Layoutfragen werden am Entwurf geklärt.
2. Grundanwendung, Umbauten, Dateien und Kosten mit den mitgelieferten Testfällen entwickeln.
3. Paperless/Gemini zuerst mit Testadaptern, danach mit bereitgestellten Endpunkten/Schlüsseln prüfen.
4. Kernversion fertigstellen: Suche, Aufgaben, Berichte, Import, Export und Restore.
5. Vor produktiver Installation Live-Bestand, Last, Anmeldungen und Sicherungsziel prüfen und einen gezielten Installationsablauf vorbereiten.
6. Geprüfte Installation und Übergabe mit dokumentiertem Versionsstand; danach Ausbau aus Phase 5/6 nach Bedarf.

Zum fertigen Kernprodukt gehören Quellcode, Abhängigkeits-/Versionsdateien, Dockerfile, Compose-Konfiguration, Datenbankmigrationen, `.env.example` ohne Geheimnisse, Testdaten, relevante Tests, Backup-/Restore-Verfahren, Datenexport, Installationsanleitung und kurze Benutzeranleitung.

Die Umsetzung muss auf dem Zielgerät nachvollziehbar starten und Dateien dauerhaft auf dem vorgesehenen Datenträger speichern. Ein nur auf dem Entwickler-PC funktionierender Prototyp ist kein abgeschlossener Pi-Betrieb.

## 6. Abnahme und Nachweise

Maßgeblich sind die 20 Abnahmeszenarien im Backlog, die Abläufe in `BEDIENABLAEUFE.md` und die isolierten fachlichen Fälle in `TESTFAELLE.json`.

Jeder Lieferstand benennt: enthaltene Funktionen, offene Arbeitspakete, durchgeführte Prüfungen, bekannte Grenzen und reproduzierbaren Start. Für Kernversion: reale Mobilbedienung, Belegimport, Kostenberechnung, Wiederholungs-/Ausfallfälle und Restore nachweisen. OCR-/KI-Güte anhand freigegebener Belege messen; keine allgemeine Fehlerfreiheit behaupten.

Die konkreten Backend-Routen und internen Datenbanktabellen dürfen dem Framework folgen. Fachregeln, sichtbare Status und erwartete Ergebnisse bleiben davon unabhängig verbindlich. Für Quellen-/Datensatzkennungen stabile IDs verwenden.

## 7. Produktgrenzen und offene Entscheidungen

Der Entwickler kann die Entwicklung starten, ohne die folgenden Betriebswerte zu erraten. Sie werden getrennt vor dem jeweils abhängigen Schritt erhoben:

- Handytyp/Browser und Wunsch nach HEIC-Unterstützung: vor endgültiger Mobilabnahme.
- Unabhängiges Sicherungsziel, zugelassener Speicherverbrauch und Wartungszeitfenster: vor produktivem Betrieb.
- Zugriff von unterwegs, Domain und zusätzliche Nutzer: vor entsprechenden Ausbaustufen.
- Gewünschte Wasserwerte, Flug-/Akkufelder und Benachrichtigungskanal: vor Phase 5 bzw. 6.
- Endgültiger Produktname, Akzentfarbe und visuelle Feinheiten: am klickbaren Entwurf.

Ein optisch exakt passendes Endprodukt kann eine schriftliche Spezifikation allein nicht garantieren. Deshalb sind die drei Bedienwege als früher sichtbarer Lieferstand enthalten.

## 8. Getrennt bereitzustellende Installationsangaben

Aktueller Pi-Zugang, SSD-/Compose-Pfade, gewünschter freier Port, Paperless-Basisadresse/API-Schema und geeigneter Zugang, Gemini-API-Schlüssel/Projekt und bestätigte Abrechnung, Backupziel sowie gegebenenfalls vorhandene Homepage-/Update-Guard-Konfiguration.

Echte Bestandsdokumente werden nur für den ausdrücklich gewünschten Import separat bereitgestellt. Das Übergabepaket liefert synthetische Beispiele und braucht keine Passwörter, privaten Rechnungen oder ursprünglichen Chatdaten.
