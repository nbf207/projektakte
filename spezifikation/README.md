# Projektakte – Entwicklerübergabe

Version 1.1, 12. November 2026. Dieses Paket kann ohne Kenntnis des bisherigen Chats gelesen werden. Es ist eine Spezifikation mit Beispielen, kein vorhandener Anwendungsquellcode.

## Auftrag in einem Absatz

Entwickelt werden soll eine persönliche Webanwendung für Auto, Aquarium, Drohne und weitere Hobbyprojekte auf einem vorhandenen Raspberry Pi 4. Kurze Formulare erzeugen eine übersichtliche Chronik. Größere Umbauten erscheinen als eine aufklappbare Zeile und enthalten Arbeitsschritte, Bilder, Teile und Belege. Rechnungsfotos werden im vorhandenen Paperless-ngx archiviert und per OCR verarbeitet; eine Gemini-Anbindung schlägt daraus prüfbare Daten und Kostenpositionen vor. Ein Entwurf ist von bestätigten Daten zu unterscheiden. Das Kernprodukt umfasst die Phasen 0–4 des Backlogs.

## Lesereihenfolge und Verbindlichkeit

1. [ENTWICKLERAUFTRAG.md](ENTWICKLERAUFTRAG.md): Auftragsumfang, feste Regeln, Standardentscheidungen und Lieferumfang.
2. [BEDIENABLAEUFE.md](BEDIENABLAEUFE.md): konkrete Bedienabläufe und verbindliches Verhalten der wichtigsten Ansichten.
3. [PROJEKTPLAN.md](PROJEKTPLAN.md): vollständiger Produkt-, Daten- und Betriebsentwurf.
4. [UMSETZUNGSBACKLOG.md](UMSETZUNGSBACKLOG.md): Arbeitspakete, Reihenfolge und Abnahmematrix.
5. [TESTFAELLE.json](TESTFAELLE.json) und die Dateien unter [beispiele](beispiele/BELEG_DEMO_OEL.txt): rein synthetische Eingaben und erwartete Ergebnisse.

Bei unterschiedlichen Detailgraden präzisiert der Entwicklerauftrag die übrigen Dokumente. Bedienabläufe präzisieren die Oberfläche. Widersprüche werden vor der betroffenen Implementierung benannt, statt still eine Interpretation zu wählen. Spätere ausdrückliche Entscheidungen des Auftraggebers werden mit Datum dokumentiert und in den betroffenen Dateien nachgeführt.

Die Vorgaben in Version 1.1 sind ausgearbeitete Vorschläge auf Basis des Gesprächs. Insbesondere neu präzisierte Standardwerte sind als solche bezeichnet; sie sind keine Behauptung, der Nutzer habe jeden einzelnen Wert bereits persönlich beschlossen.

## Ausgangsumgebung

Randbedingungen des ursprünglichen Entwurfs. Die konkrete Installation separat prüfen:

- Raspberry Pi 4 mit **2 GB physischem RAM**, Debian 13, ARM64, Docker Compose und SSD für Anwendungsdaten. Viele andere Dienste laufen bereits.
- Paperless-ngx 3.4.2 ist installiert: SQLite, Broker, deutsche/englische OCR, ein Verarbeitungsauftrag und eine OCR-Seite gleichzeitig. Eigene Paperless-Instanz weiterverwenden.
- Der dokumentierte Kernelzustand setzt Docker-RAM-Limits nicht durch. Reale Gesamtlast messen und sparsame Parallelität verwenden.
- Homepage und ein Sicherungs-/Update-Guard waren optionale Betriebsintegrationen. Sicherungsgrenzen werden pro Installation festgelegt.
- Ein unabhängiges regelmäßiges Sicherungsziel für neue App und Paperless ist noch festzulegen.
- API-Zugang und Anbieter-Abrechnung vor der Integration prüfen.
- Ein geeigneter separater Rechner kommt als optionaler Ollama-Testhost infrage.

Hostnamen, interne Adressen, reale Benutzer, Schlüssel und Anmeldedaten werden bei der konkreten Installation getrennt bereitgestellt. Diese Angaben sind für die lokale Entwicklung mit Testdaten nicht erforderlich. Alle Serverpfade und Endpunkte sind konfigurierbar.

## Was die Übergabe ermöglicht und was separat hinzukommt

Mit diesem Paket lässt sich das Produkt lokal entwickeln, mit synthetischen Daten demonstrieren und gegen die Fachregeln testen. Die separat benötigten Angaben für den produktiven Betrieb sind in Abschnitt 8 des Entwicklerauftrags aufgeführt. Die ursprünglichen privaten Serverprotokolle und echten BMW-Unterlagen sind für das Verständnis nicht erforderlich.

Die Dateien unter `beispiele/` sind Textbelege für Parser-, KI- und Fachlogiktests. Sie enthalten keine echten Rechnungen oder Zugangsdaten. Die 12 Fotos eines Umbau-Beispiels sind eine geforderte Testanzahl, keine in diesem Paket mitgelieferten Foto-Dateien. Für Kamerabedienung, OCR-Qualität und Bilderdarstellung sind während der Entwicklung eigene synthetische Bilder sowie bei der späteren Abnahme freigegebene echte Beispiele zu verwenden.

## Erster Lieferstand

Zuerst ein lokal startbarer, klickbarer Entwurf mit den drei vollständigen Bedienwegen Ölwechsel, Umbau und Belegprüfung, einschließlich der erwarteten Summen und Zustände. Danach schrittweise die Kernversion aus Phasen 1–4 implementieren. In der Übergabe enthaltene Herstellerverweise sind Referenzen; tatsächliche Versions-/API-Verträge vor Integration prüfen.

Mitgeliefert wird eine `MANIFEST.sha256` zur Prüfung der Paketdateien. Die Prüfsummen bestätigen Dateivollständigkeit, keine bereits getestete Anwendungsimplementierung.
