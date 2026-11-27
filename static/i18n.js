/* Lightweight client-side DE/EN localization helper (CSP-compliant, zero inline scripts). */
(() => {
  const EN = {
    // app.js
    'Prüfentwurf wird gespeichert …': 'Saving review draft …',
    'Entwurf wird gespeichert …': 'Saving draft …',
    'Entwurf nicht gespeichert. Eingabe bleibt im Formular.': 'Draft not saved. Your input remains in the form.',
    'Entwurf nicht bestätigt. Eingabe bleibt im Formular; Speichern prüft den Versionsstand.': 'Draft not confirmed. Your input remains in the form; saving checks the version state.',
    'Anmeldung abgelaufen. Vor dem Erneuern bitte noch nicht gespeicherte Texte kopieren. ': 'Session expired. Please copy any unsaved text before signing in again. ',
    'Ungespeicherte Änderung …': 'Unsaved change …',
    'Upload läuft. Annahme wird nach dauerhafter Speicherung bestätigt.': 'Upload in progress. Acceptance will be confirmed once saved durably.',
    'Upload nicht bestätigt. Eingabe bleibt erhalten; Status im Eingang prüfen.': 'Upload not confirmed. Your input is kept; check status in Inbox.',
    'Verbindung unklar. Eingang prüfen; identische Datei wird anhand ihres Hashes wiedererkannt.': 'Connection uncertain. Check Inbox; identical files are recognized by their hash.',
    'Navigation schließen': 'Close navigation',
    'Navigation öffnen': 'Open navigation',

    // auth.js
    'Anmeldung oder Sicherheitsnachweis erneuern.': 'Renew sign-in or security token.',
    'Antwort nicht bestätigt. Eingaben bleiben erhalten.': 'Response not confirmed. Your inputs are preserved.',
    'Anmeldung oder Verbindung unterbrochen. Deine Eingaben bleiben erhalten. ': 'Sign-in or connection interrupted. Your inputs are preserved. ',
    'Anmeldung erneuern und fortsetzen': 'Sign in again and continue',

    // capture.js
    'Audio ausgewählt: ': 'Audio selected: ',
    'Dateiauswahl leeren': 'Clear file selection',
    'Deine Erfassung wurde nach der Anmeldung wiederhergestellt. Du kannst jetzt speichern.': 'Your capture was restored after signing in. You can save now.',
    'Browserentwurf nicht verfügbar. Bitte Dateien erneut auswählen.': 'Browser draft unavailable. Please select files again.',
    'Bitte Text, Aufnahme oder Dateien hinzufügen.': 'Please add text, a recording, or files.',
    'Höchstens 8 Fotos/PDFs, je Datei 8 MB und zusammen mit Audio maximal 12 MB verwenden.': 'Use at most 8 photos/PDFs, max 8 MB each, and 12 MB total including audio.',
    'Wird dauerhaft gespeichert … Danach kannst du das Handy weglegen.': 'Saving durably … You can put your phone away afterwards.',
    'Speicherung nicht bestätigt. Eingaben bleiben erhalten.': 'Save not confirmed. Your inputs are preserved.',
    'Speicherung nicht bestätigt. Dateien und Notiz bleiben im Browser gespeichert. ': 'Save not confirmed. Files and note remain saved in the browser. ',
    'Speicherung nicht bestätigt. Vor dem Anmelden bitte Text kopieren; Dateien danach erneut auswählen. ': 'Save not confirmed. Please copy your text before signing in; re-select files afterwards. ',

    // editors.js
    'Positionen noch unvollständig.': 'Line items are still incomplete.',
    'Positionssumme: ': 'Line items total: ',
    ' · Abweichung zu Brutto: ': ' · Difference to gross: ',
    'Gesamtbetrag und Zuordnungen bitte vervollständigen.': 'Please complete total amount and allocations.',
    'Ausdrücklich vollständig unzugeordnet: {amount}. Eingetragene Zuordnungen werden bei dieser Auswahl nicht gebucht.': 'Explicitly left completely unallocated: {amount}. Entered allocations will not be booked with this option.',
    'Zugeordnet: {allocated} · Offener Rest: {remainder}': 'Allocated: {allocated} · Remaining: {remainder}',
    ' · Vorhandene Ausgabe wird verbunden.': ' · Existing expense will be linked.',

    // entry-editor.js
    'Eigenen Titel eingeben': 'Enter custom title',
    'Von der KI interpretiert; bitte prüfen': 'Interpreted by AI; please verify',

    // feedback.js
    'Feedback nicht bestätigt.': 'Feedback not confirmed.',
    'Verbindung oder Anmeldung unterbrochen. Deine Notiz bleibt im Eingabefeld.': 'Connection or sign-in interrupted. Your note remains in the input field.',

    // knowledge.js
    'YouTube-Video': 'YouTube video',
    'Unfertiges Video und gespeichertes Original entfernen?': 'Remove unfinished video and saved original?',
    'Unterbrochener Upload: dieselbe Datei auswählen und fortsetzen oder abbrechen.': 'Interrupted upload: select the same file to resume, or cancel.',
    'Upload nicht bestätigt. Dieselbe Datei erneut auswählen und fortsetzen.': 'Upload not confirmed. Select the same file again to resume.',
    'Abbruch nach dem aktuellen Teilstück …': 'Cancelling after current chunk …',
    'Upload abgebrochen.': 'Upload cancelled.',
    'Original maximal 1 GB. Bitte größere Dateien vorab verkleinern.': 'Original max 1 GB. Please reduce larger files beforehand.',
    'Bitte dieselbe Datei auswählen oder den bisherigen Upload abbrechen.': 'Please select the same file or cancel the previous upload.',
    'Upload nicht mehr vorhanden. Bitte abbrechen und neu beginnen.': 'Upload no longer exists. Please cancel and start over.',
    'Hochladen: ': 'Uploading: ',
    'Gespeichert. Das Video wird im Hintergrund komprimiert. Du kannst diese Seite verlassen und später neu öffnen.': 'Saved. The video is being compressed in the background. You can leave this page and reopen it later.',
    'Upload pausiert. Nach Anmeldung dieselbe Datei auswählen. ': 'Upload paused. Select the same file after signing in. ',

    // pwa.js
    'Teilen-Eingang bereit. Projektakte in Chrome als App installieren, danach aus Galerie oder PDF-App teilen.': 'Share inbox ready. Install Projektakte as an app in Chrome, then share from your gallery or PDF app.',
    'Teilen-Eingang konnte nicht aktiviert werden. Seite neu laden oder Dateien unten auswählen.': 'Could not activate share inbox. Reload the page or select files below.',

    // repair-advice.js
    'Originaltreffer werden eingeordnet …': 'Classifying original matches …',
    'Suchhilfe nicht verfügbar.': 'Search assistant unavailable.',
    'Noch zu klären: ': 'Still to clarify: ',
    'Zuerst ansehen · KI-Vorschlag': 'Check first · AI suggestion',
    'Alternative · anderer Arbeitsumfang': 'Alternative · different scope',
    'Bitte prüfen: ': 'Please verify: ',
    'Originalanleitung öffnen →': 'Open original manual →',
    'KI-Orientierung · Arbeitsanweisungen und Drehmomente stehen im verlinkten Original.': 'AI guidance · Procedures and torque specs are in the linked original.',
    'Verbindung unterbrochen. Die normale Suche bleibt verfügbar.': 'Connection interrupted. Regular search remains available.',

    // review-refresh.js
    'Status derzeit nicht verfügbar': 'Status currently unavailable',
    'Die Verarbeitung ist fertig. Deine Eingaben bleiben erhalten. ': 'Processing finished. Your inputs are preserved. ',
    'Aktuelle Prüfdaten öffnen': 'Open updated review data',

    // review-simple.js
    'Das Projekt des bestehenden Eintrags bitte über „Eintrag bearbeiten“ ändern.': 'Please change the existing entry’s project via "Edit entry".',
    'Kategorie, Titel-Vorlagen und Zusatzfelder aktualisiert.': 'Category, title templates, and extra fields updated.',
    'Felder konnten nicht geladen werden. Bitte Projekt erneut auswählen.': 'Could not load fields. Please select the project again.',

    // settings.js
    'Noch unbekannt': 'Unknown yet',
    'vor {sec} Sekunden': '{sec} seconds ago',
    'noch nicht ermittelt': 'not measured yet',
    ' · Stand ': ' · updated ',
    'aktiv': 'active',
    'prüfen (langer Auftrag oder Unterbrechung)': 'check (long job or interruption)',
    'Status aktualisiert · ': 'Status updated · ',
    'Status momentan nicht aktualisierbar. Bei Bedarf Seite neu öffnen / anmelden. Eingaben bleiben erhalten.': 'Status cannot be refreshed right now. Reopen or sign in if needed. Inputs are preserved.',
    'Offene Punkte kopiert.': 'Open items copied.',
    'Bitte den markierten Prompt kopieren.': 'Please copy the highlighted prompt.',
    'Bild bitte auf höchstens 5 MB begrenzen.': 'Please limit image size to 5 MB.',
    'Ausgewähltes Feedback-Bild': 'Selected feedback image',
    'Bild entfernen': 'Remove image',
    'Bild entfernt. Du kannst ein anderes auswählen oder ohne Bild senden.': 'Image removed. You can select another one or send without an image.',
    'Paperless-Belege werden gesammelt …': 'Collecting Paperless documents …',
    'Datenbank- und Medien-Snapshot wird erstellt …': 'Creating database and media snapshot …',
    'Sicherungsarchiv wird gepackt …': 'Packing backup archive …',
    'Sicherungsarchiv wird geprüft …': 'Verifying backup archive …',
    'Sicherung wird abgeschlossen …': 'Finalizing backup …',
    'Sicherung fertiggestellt.': 'Backup completed.',
    'Vorherige Sicherung wurde unterbrochen.': 'Previous backup was interrupted.',
    'Sicherung fehlgeschlagen.': 'Backup failed.',
    'Sicherung läuft …': 'Backup in progress …',
    'Sicherung abgeschlossen.': 'Backup finished.',
    '{done} / {total} Dateien': '{done} / {total} files',
    'Die App bleibt während der Sicherung normal nutzbar.': 'The app remains fully usable while the backup runs.',
    'Sicherung abgeschlossen. Seite neu laden, um den neuen Download anzuzeigen.': 'Backup completed. Reload the page to see the new download.',
    'Bitte unter „Letzte Hintergrund-Aktionen“ oder Fehlermeldungen prüfen.': 'Please check "Recent background actions" or error logs.',

    // share.js
    'Entfernen': 'Remove',
    'Dateien werden dauerhaft gespeichert. KI-Auswertung folgt im Hintergrund …': 'Saving files durably. AI extraction follows in the background …',
    'Dateien werden dauerhaft gespeichert …': 'Saving files durably …',
    'App-Installation und Android-Teilen-Menü erfordern HTTPS (oder localhost). Über HTTP im LAN kannst du Dateien direkt oben über die Dateiauswahl hochladen.': 'App installation and Android share menu require HTTPS (or localhost). Over HTTP on your LAN you can upload files directly using the file picker above.',
    'Dieser Browser unterstützt die App-Installation nicht. Du kannst Dateien direkt oben über die Dateiauswahl hochladen.': 'This browser does not support app installation. You can upload files directly using the file picker above.',
    'Bitte 1–8 Dateien, je höchstens 8 MB und zusammen 12 MB teilen.': 'Please share 1–8 files, max 8 MB each and 12 MB total.',
    'Geteiltes Dateiformat nicht unterstützt. Bitte Bilder oder PDFs teilen.': 'Shared file format not supported. Please share images or PDFs.',
    'Der lokale Teilen-Eingang enthält bereits fünf Pakete. Bitte vorhandene Pakete zuerst speichern.': 'The local share inbox already contains five packages. Please save existing packages first.',
    'Browser-Zwischenspeicherung fehlgeschlagen. Dateien erneut teilen oder unten auswählen.': 'Browser caching failed. Share files again or select them below.',
    'Ein Bildformat konnte im Browser nicht umgewandelt werden. Bitte das betreffende Bild entfernen und als JPEG, PNG oder WebP teilen. Das lokale Paket bleibt erhalten.': 'An image format could not be converted in the browser. Please remove that image and share as JPEG, PNG, or WebP. Your local package is preserved.',
    'Das geteilte Paket fehlt oder ist abgelaufen. Bitte erneut aus der Galerie teilen oder Dateien auswählen.': 'The shared package is missing or expired. Please share from gallery again or select files.',
    ' Datei(en) übernommen. Projekt auswählen und bestätigen.': ' file(s) imported. Select a project and confirm.',
    ' Bild(er) für die Verarbeitung im Browser in JPEG umgewandelt.': ' image(s) converted to JPEG in the browser for processing.',
    'Teile Bilder/PDFs aus einer Android-App mit Projektakte oder wähle sie hier aus.': 'Share images/PDFs from an Android app to Projektakte or select them here.',
    'Lokales Paket konnte nicht gelesen werden. Bitte erneut teilen oder Dateien auswählen.': 'Could not read local package. Please share again or select files.',
    'Noch nicht gesendet: ': 'Not sent yet: ',
    ' Datei(en) · ': ' file(s) · ',
    'Bitte nicht lesbare Bildformate entfernen oder als JPEG, PNG bzw. WebP erneut auswählen.': 'Please remove unreadable image formats or re-select as JPEG, PNG, or WebP.',
    '1–8 Bilder/PDFs, je Datei 8 MB und zusammen mit Audio höchstens 12 MB verwenden.': 'Use 1–8 images/PDFs, max 8 MB each and 12 MB total including audio.',
    'Speicherung nicht bestätigt. Das lokale Paket bleibt erhalten.': 'Save not confirmed. The local package is preserved.',
    'Speicherung nicht bestätigt. Das lokale Paket bleibt erhalten; erneutes Senden erzeugt keinen zweiten Eintrag. ': 'Save not confirmed. The local package is preserved; retrying will not create a duplicate entry. ',

    // theme.js
    'Hellmodus aktivieren': 'Enable light mode',
    'Dunkelmodus aktivieren': 'Enable dark mode',
    'Übernommen und in diesem Browser gespeichert.': 'Applied and saved in this browser.',
    'Für diese Seite übernommen. Der Browser erlaubt keine dauerhafte Speicherung.': 'Applied for this page. The browser does not allow persistent storage.',
    'Bitte einen Hex-Wert mit sechs Zeichen eingeben, zum Beispiel #12abef.': 'Please enter a six-character hex value, for example #12abef.',
    'Noch nicht übernommen: Bitte eine vollständige Hex-Farbe wie #12abef eingeben.': 'Not applied yet: Please enter a complete hex color such as #12abef.',

    // voice.js
    'Für Mikrofonaufnahme HTTPS und einen unterstützten Browser verwenden. Du kannst stattdessen eine Audio-Datei auswählen.': 'Use HTTPS and a supported browser for microphone recording. You can select an audio file instead.',
    'Aufnahme leer oder zu groß. Bitte kürzer aufnehmen.': 'Recording empty or too large. Please record a shorter clip.',
    'Aufnahme fertig. Mit „{label}“ hochladen.': 'Recording ready. Upload with "{label}".',
    'Sprachnachricht speichern': 'Save voice note',
    'Aufnahme konnte nicht übernommen werden. Bitte Audio-Datei auswählen.': 'Could not attach recording. Please select an audio file.',
    'Aufnahme fehlgeschlagen. Audio-Datei auswählen oder erneut versuchen.': 'Recording failed. Select an audio file or try again.',
    'Aufnahme läuft …': 'Recording …',
    'Mikrofonzugriff nicht möglich. Berechtigung prüfen oder Audio-Datei hochladen.': 'Microphone access unavailable. Check permissions or upload an audio file.',

    // public_demo.js
    'Vom Ölwechsel zur fertigen Akte': 'From oil change to complete logbook',
    'Du hast einen Ölwechsel gemacht.': 'You just did an oil change.',
    'Die Rechnung liegt noch auf dem Tisch. Schau sie dir an und setze sie mit einem Klick ein.': 'The receipt is still on the table. Preview it and insert it with one click.',
    'Rechnung ansehen': 'View receipt',
    'Erfassen & zuordnen': 'Capture & assign',
    'Foto und ein Satz reichen.': 'A photo and one sentence are enough.',
    'Die Rechnung ist eingesetzt. Die Notiz ergänzt, was du gemacht hast und bei welchem Kilometerstand. Jetzt darf die App sortieren.': 'The receipt is attached. The note adds what you did and at what mileage. Now the app can sort it.',
    'Deine Arbeit ist festgehalten.': 'Your work is recorded.',
    'Hier stehen Datum, Kilometerstand und Öl-Angaben zusammen. Später kannst du Arbeitsfotos ergänzen. Die Rechnung ist gespeichert, aber noch nicht als Kosten bestätigt.': 'Date, mileage, and oil details are all together. You can add work photos later. The receipt is saved, not yet booked as a cost.',
    'Du behältst die Kontrolle.': 'You stay in control.',
    'Vergleiche das Original links mit den Angaben rechts. Mit deiner Bestätigung werden die 89,50 € genau einmal zugeordnet.': 'Compare the original on the left with the details on the right. Your confirmation allocates the €89.50 exactly once.',
    'Angaben bestätigen': 'Confirm details',
    'Der nächste Ölwechsel bleibt im Blick.': 'The next oil change stays in sight.',
    'Das Intervall ist hinterlegt. Die bestätigte Wartung setzt den Ausgangspunkt auf deinen neuen Eintrag. Fertig: festhalten, prüfen, rechtzeitig erinnern.': 'The interval is configured. Confirming the service sets the baseline to your new entry. Done: log, review, remind on time.',
    'Sprich es einfach ein': 'Just speak it in',
    'Sag es, wie du es erzählen würdest.': 'Say it the way you would tell a friend.',
    'Höre eine vorbereitete Sprachnotiz an. Du brauchst kein Formular auszufüllen. Die folgende Erkennung ist ein vorbereitetes Beispiel, kein echter KI-Aufruf.': 'Listen to a prepared voice note. No form needed. The extraction below is a prepared example, not a live AI call.',
    'Beispiel anhören': 'Play example',
    'Aus Sprache werden klare Angaben.': 'Speech becomes structured details.',
    'Projekt und Kategorie wurden vorgeschlagen. Ein Stern bedeutet: interpretiert. Betrag und Datum bleiben leer, weil sie nicht genannt wurden. Du kannst den Titel hier korrigieren.': 'Project and category were suggested. An asterisk means interpreted. Amount and date stay empty because they were not mentioned. You can edit the title here.',
    'Auch eine Rechnung liefert Informationen.': 'A receipt provides details too.',
    'Dieses vorbereitete Beispiel zeigt, wie eine Rechnung Projekt, Händler und Betrag liefern kann. Die Erkennung ist simuliert; Kosten werden noch nicht gebucht.': 'This prepared example shows how a receipt can provide project, vendor, and amount. Extraction is simulated; costs are not booked yet.',
    'Aus einer Notiz wird eine Aufgabe.': 'A note becomes a task.',
    '„Dachträger vor dem Urlaub kontrollieren.“ So kurz darf es sein. Wir speichern diese Notiz jetzt in deinem eigenen Demo-Bestand.': '"Check roof rack before vacation." It can be that short. Let us save this note into your demo workspace.',
    'Notiz speichern': 'Save note',
    'Gespeichert und wieder auffindbar.': 'Saved and easy to find again.',
    'Die Notiz ist jetzt in deiner Akte. Aus dem noch offenen Vorhaben wurde zusätzlich eine Aufgabe.': 'The note is now in your logbook. The open item also created a task.',
    'Du entscheidest später, wann es erledigt wird.': 'You decide later when to get it done.',
    'Die neue Aufgabe hat absichtlich keinen erfundenen Termin. In der persönlichen App kannst du ihn ergänzen und die Aufgabe abhaken.': 'The new task intentionally has no invented due date. In your personal app you can add one and check it off.',
    'Dein Projekt im Überblick': 'Your project at a glance',
    'Ein Umbau wächst mit deinem Projekt.': 'Modifications grow with your project.',
    'Hier wurde ein Dachträger montiert. Beschreibung, Beleg und Kosten gehören zu derselben Arbeit. Öffne nicht mehrere Ablagen: alles bleibt am Eintrag.': 'A roof rack was mounted here. Description, receipt, and cost stay together on the same entry.',
    'Bilder erklären mehr als lange Notizen.': 'Photos explain more than long notes.',
    'Füge ein vorbereitetes Werkstattfoto zur Dokumentation hinzu. Dieses Foto ist nur ein Muster; es zeigt nicht den tatsächlichen Dachträger.': 'Add a prepared workshop photo to the documentation. This photo is a sample only; it does not show the actual roof rack.',
    'Beispielfoto hinzufügen': 'Add sample photo',
    'Ein Wort bringt dich zurück.': 'One word brings you right back.',
    'Wir suchen nach „Bremsen“. Die Treffer führen zurück zur Chronik, auch wenn die Arbeit schon länger her ist.': 'Let us search for "Brakes". Results lead straight back to the chronicle, even for older work.',
    'Kosten bleiben nachvollziehbar.': 'Costs stay transparent.',
    'Diese Summe enthält bestätigte Zuordnungen. Ungeprüfte Rechnungen zählen noch nicht dazu. So bleibt dieselbe Rechnung nicht zweimal in der Summe.': 'This total includes confirmed allocations only. Unreviewed receipts are excluded so the same receipt is never counted twice.',
    'Hier ist ein druckbarer Bericht. „Drucken / als PDF speichern“ öffnet den Druckdialog. Du kannst ihn jetzt ausprobieren oder die Tour abschließen.': 'Here is a printable report. "Print / save as PDF" opens the print dialog. Try it now or finish the tour.',
    'Noch einmal erleben': 'Replay tour',
    'Tour starten': 'Start tour',
    'Geschafft! Wähle einen weiteren Ablauf oder erkunde deinen Demo-Workspace selbst.': 'Done! Choose another walkthrough or explore your demo workspace freely.',
    '<h2>Eine kurze Sprachnotiz</h2><p>Vorbereitetes Audio · keine echte KI-Verarbeitung</p><p id="demo-speech-status" role="status">Klicke auf „Beispiel anhören“. Dein Browser liest den vorbereiteten Text vor.</p><button type="button" id="demo-audio-skip">Ohne Ton weiter</button>':
      '<h2>A short voice note</h2><p>Prepared audio · no live AI call</p><p id="demo-speech-status" role="status">Click "Play example". Your browser will read the prepared text aloud.</p><button type="button" id="demo-audio-skip">Continue without audio</button>',
    '<h2>Das wurde gesagt</h2><p>„Beim Reisekombi vorne die Bremsen gewechselt. Kilometerstand 128450. Die Probefahrt war ruhig.“</p><div class="demo-result-grid"><label>Titel – probiere eine Korrektur<input value="Bremsen vorne gewechselt" maxlength="150"></label><p>Projekt<br><strong>Nordlicht · Reisekombi *</strong></p><p>Kategorie<br><strong>Reparatur *</strong></p><p>Kilometerstand<br><strong>128450</strong></p><p>Datum<br><strong>— nicht genannt</strong></p><p>Betrag<br><strong>— nicht genannt</strong></p></div><small>* Interpretiert. Dies ist eine Vorschau, noch kein gespeicherter Eintrag.</small>':
      '<h2>What was said</h2><p>"Replaced front brakes on the touring wagon. Mileage 128450. Test drive felt smooth."</p><div class="demo-result-grid"><label>Title – try editing it<input value="Front brakes replaced" maxlength="150"></label><p>Project<br><strong>Nordlicht · Touring Wagon *</strong></p><p>Category<br><strong>Repair *</strong></p><p>Mileage<br><strong>128450</strong></p><p>Date<br><strong>— not mentioned</strong></p><p>Amount<br><strong>— not mentioned</strong></p></div><small>* Interpreted. Preview only, not saved yet.</small>',
    '<h2>Eine Rechnung als Eingabe</h2><a class="demo-invoice-card" href="/static/demo/musterrechnung.pdf" target="_blank" rel="noopener"><small>NORDLICHT WERKSTATT · MUSTER</small><strong>Ölwechsel & Inspektion</strong><span>Motoröl · Filter · Arbeitszeit</span><b>89,50 €</b><small>Original öffnen ↗</small></a><p>Vorbereiteter Vorschlag: Nordlicht · Wartung · Ölwechsel · 89,50 €</p>':
      '<h2>A receipt as input</h2><a class="demo-invoice-card" href="/static/demo/musterrechnung.pdf" target="_blank" rel="noopener"><small>NORDLICHT WORKSHOP · SAMPLE</small><strong>Oil change &amp; inspection</strong><span>Engine oil · Filter · Labor</span><b>€89.50</b><small>Open original ↗</small></a><p>Prepared suggestion: Nordlicht · Maintenance · Oil change · €89.50</p>',
    'Dachträger vor dem Urlaub kontrollieren.': 'Check roof rack before vacation.',
    'Schritt ': 'Step ',
    ' von ': ' of ',
    'Zurück': 'Back',
    'Weiter': 'Next',
    'Tour abschließen': 'Finish tour',
    'Vorlesen nicht verfügbar. Mit „Ohne Ton weiter“ geht es weiter.': 'Speech synthesis unavailable. Click "Continue without audio".',
    'Heute Ölwechsel bei 128450 km. 4,5 Liter 5W-30, neuer Ölfilter. Rechnung 89,50 €.': 'Oil change today at 128450 km. 4.5 liters 5W-30, new oil filter. Receipt €89.50.',
    '<small>NORDLICHT WERKSTATT · MUSTERRECHNUNG</small><strong>Ölwechsel & Inspektion</strong><span>Motoröl 5W-30 · 4,5 Liter &nbsp; 37,82 €<br>Ölfilter &nbsp; 12,60 €<br>Arbeitszeit &nbsp; 24,79 €</span><span>Netto 75,21 € · MwSt. 19 %: 14,29 €</span><b>Gesamt 89,50 €</b><small>PDF öffnen ↗</small>':
      '<small>NORDLICHT WORKSHOP · SAMPLE RECEIPT</small><strong>Oil change &amp; inspection</strong><span>Engine oil 5W-30 · 4.5 L &nbsp; €37.82<br>Oil filter &nbsp; €12.60<br>Labor &nbsp; €24.79</span><span>Net €75.21 · VAT 19%: €14.29</span><b>Total €89.50</b><small>Open PDF ↗</small>',
    'Deine Rechnung. Gleich in deiner Akte.': 'Your receipt. Soon in your logbook.',
    '<small>NORDLICHT WERKSTATT · MUSTERRECHNUNG</small><strong>Rechnung DEMO-2026-042</strong><span>Ölwechsel & Inspektion<br>Projektfahrzeug Nordlicht · 128450 km</span><hr><span>Motoröl 5W-30 · 4,5 Liter <b>37,82 €</b><br>Ölfilter <b>12,60 €</b><br>Arbeitszeit / Kontrolle <b>24,79 €</b></span><span>Netto 75,21 €<br>19 % MwSt. 14,29 €</span><b>Gesamt 89,50 €</b><small>Fiktives Beispiel · keine echte Zahlungsforderung</small>':
      '<small>NORDLICHT WORKSHOP · SAMPLE RECEIPT</small><strong>Invoice DEMO-2026-042</strong><span>Oil change &amp; inspection<br>Project vehicle Nordlicht · 128450 km</span><hr><span>Engine oil 5W-30 · 4.5 L <b>€37.82</b><br>Oil filter <b>€12.60</b><br>Labor / check <b>€24.79</b></span><span>Net €75.21<br>19% VAT €14.29</span><b>Total €89.50</b><small>Fictitious example · not a real invoice</small>',
    'Rechnung einsetzen →': 'Insert receipt →',
    'Abbrechen': 'Cancel',
    'Mikrofon nicht verfügbar. Bitte Audio-Datei auswählen.': 'Microphone unavailable. Please select an audio file.',
    'Aufnahme bereit. In der Demo wird Audio nicht transkribiert. Ergänze eine Notiz oder nutze das Textbeispiel.': 'Recording ready. Audio is not transcribed in the demo. Add a note or use the text example.',
    'Aufnahme abgebrochen. Audio-Datei-Upload ist weiterhin möglich.': 'Recording cancelled. Audio file upload is still available.',
    'Aufnahme läuft · maximal 30 Sekunden.': 'Recording · max 30 seconds.',
    'Kein Mikrofonzugriff. Du kannst eine Audio-Datei auswählen.': 'No microphone access. You can select an audio file.',
    'Bitte zuerst die Aufnahme stoppen.': 'Please stop the recording first.',
    'Bitte eine Notiz oder Datei ergänzen.': 'Please add a note or file.',
    'Bitte maximal vier Dateien mit insgesamt weniger als 2 MB wählen.': 'Please select at most four files totaling less than 2 MB.',
    'Eingabe wird verarbeitet … · Demo-Simulation': 'Processing input … · Demo simulation',
    'Projekt, Betrag und Zusatzfelder werden zugeordnet … · Simulation': 'Assigning project, amount, and extra fields … · Simulation',
    'Bitte kurz warten oder die Demo zurücksetzen.': 'Please wait a moment or reset the demo.',
    'Nicht gespeichert. Sitzung, Dateityp oder Größenlimit prüfen.': 'Not saved. Check session, file type, or size limit.',
    'Deine Sitzung ist abgelaufen. Öffne die Anmeldung erneut; die Eingabe bleibt hier erhalten.': 'Your session has expired. Open sign-in again; your input is kept here.',
    'Deine Dokumentation kann mitkommen.': 'Your documentation can come with you.',
    'Fortsetzen': 'Continue',
    'Beispiel wird vorgelesen …': 'Reading example aloud …',
    'Beim Reisekombi vorne die Bremsen gewechselt. Kilometerstand 128450. Die Probefahrt war ruhig.': 'Replaced front brakes on the touring wagon. Mileage 128450. Test drive felt smooth.',
    'Bremsen': 'Brakes',
    'Neu aufnehmen': 'Record again',
    'Aufnahme stoppen': 'Stop recording',
    'Verbindung unterbrochen. Du kannst erneut speichern; dieselbe Erfassung wird nicht doppelt angelegt.': 'Connection interrupted. You can save again; the same capture will not be created twice.',
  };

  function isEnglish() {
    const lang = (document.documentElement.lang || '').toLowerCase();
    return lang.startsWith('en');
  }

  function t(key, vars) {
    let text = isEnglish() && Object.prototype.hasOwnProperty.call(EN, key) ? EN[key] : key;
    if (vars) {
      for (const [k, v] of Object.entries(vars)) {
        text = text.replaceAll('{' + k + '}', String(v));
      }
    }
    return text;
  }

  window.t = t;
  window.ProjektakteI18n = { t, isEnglish };
})();
