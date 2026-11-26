# Projektakte · User Guide

> **Language note:** The web UI is currently in German. English terms below are paired with the corresponding German UI labels where helpful.

---

## 1. Core Principle: Suggestions Instead of Blind Auto-Booking

Projektakte is built around a two-speed workflow:

1. **Fast capture on mobile:** Throw in a quick note, photo, PDF, or voice memo in seconds—without needing to pick categories, dates, or cost splits right away.
2. **Structured review on desktop:** OCR (via Paperless-ngx) and optional AI (Google Gemini) pre-fill dates, titles, line items, meter readings, and project suggestions. **Nothing is booked into confirmed project costs until you review and confirm it.**

Fields interpreted by AI are marked with an asterisk (`*`). As soon as you edit a field manually, the marker disappears.

---

## 2. Quick Capture (*Schnellerfassung*)

On smartphones, the quick-capture card sits directly at the top of the home screen (`/`), with navigation tucked into the top-bar menu button (`≡`). On desktop, it sits above your project directory.

You can combine any of the following in a single capture:
- **Text note:** Quick description of what you did or plan to do (`Ctrl+Enter` / `⌘+Enter` submits immediately on desktop).
- **Voice (*Sprechen*):** Record directly via your browser microphone, or pick an existing audio file under *Audio-Datei & Optionen*.
- **Camera (*Foto*):** Snap a photo directly from your phone camera.
- **Files (*+ Dateien*):** Attach up to 8 images or PDFs (max 8 MB per file, 12 MB combined with audio).
- **Project selector:** Leave on *Automatisch erkennen* (auto-detect) or pick a project directly.

If your mobile connection drops or your session expires during upload, the draft is cached locally in the browser and restored after signing back in.

---

## 3. Projects, Chronicle & Entries

Each project (`/projekte/<id>/`) contains:
- **Summary tiles:** Confirmed costs, open/overdue tasks, and open receipts in the inbox.
- **Tabs:** *Wissen* (Knowledge), *Chronik* (Chronicle), *Umbauten* (Builds/Mods), *Teile* (Parts), *Dokumente* (Documents), *Kosten* (Costs), *Aufgaben* (Tasks), *Stammdaten* (Master Data), and *Wartungsplan* (Maintenance).
- **Chronicle:** Entries grouped by category with search, category, status, date-range, and tag filters. Click any row to expand a preview, or open the full entry page (`/vorgaenge/<id>/`).

Inside an entry you can:
- Write Markdown descriptions and structured custom fields (e.g. oil grade, quantity, meter reading, meter replacement).
- Document ordered **work steps** (*Arbeitsschritte*).
- Upload, rotate (`90°`), caption, and tag **work photos** (`vorher` / `nachher`).
- Log **part installation / removal events** (*Einbau / Ausbau*).
- Link or split **receipts and costs**.
- **Merge duplicate entries** (*Mit bestehendem Vorgang verbinden*): moves receipts, costs, steps, and photos onto another entry in the same project and sends the duplicate to the reversible trash.
- Attach **videos** (chunked upload) or promote notes to the **Knowledge Base**.

---

## 4. Receipt Inbox, AI Review & Costs

- **Inbox (`/eingang/`):** Lists all uploaded, emailed (IMAP), or Paperless-synced receipts waiting for review.
- **Review screen (`/belege/<id>/`):**
  - Displays the original PDF/image and OCR text alongside the editable proposal.
  - Supports **partial and multi-project cost allocations**—over-allocation is rejected.
  - Existing manual costs can be linked instead of creating a duplicate expense.
  - **Save drafts** (*Korrekturen speichern*, plus autosave) or **Confirm once** (*Geprüft übernehmen*). Repeating confirmation never creates duplicate bookings.
  - Under *Verarbeitung & Verbindung*, you can refresh OCR, reconcile Paperless uploads, re-run AI extraction (optionally including the original image/PDF up to the configured size limit), or review purely manually without AI.

---

## 5. Maintenance Schedules, Tasks & Parts

- **Maintenance (*Wartungsplan*, `/wartungsplan/`):** Define recurring rules by **time interval** (months) and/or **meter interval** (e.g. km). Whichever threshold is reached first marks the rule due. After logging a completed maintenance entry, link it (*Erledigte Arbeit zuordnen*) to advance the next due date/reading without creating duplicate costs.
- **Tasks (*Aufgaben*, `/aufgaben/`):** Project-linked to-dos with due dates and overdue indicators.
- **Parts (*Teile*):** Track ordered/stocked parts with part numbers and manufacturers. Purchasing a part does not automatically count as installing it; installations are recorded on the relevant chronicle entry.

---

## 6. Knowledge Base & Repair Library

- **Knowledge (*Wissen*, `/wissen/`):** Reusable notes, torque specs, and procedures linked to projects or entries.
- **Repair Library (`/wissen/bibliothek/<id>/`):** Browse operator-supplied workshop manuals, diagrams, tables, PDFs, and videos with full-text search and optional AI search guidance.

---

## 7. Search, Shortcuts & Mobile PWA

- **Quick search:** Press `/` or `Ctrl+K` (`⌘+K` on macOS) from anywhere to jump to `/suche/`.
- **Light / Dark mode:** Toggle via the sun/moon pill in the top bar.
- **Installable PWA & Android Share Target (`/teilen/`):** Install Projektakte from Chrome on Android to share photos, PDFs, or invoices straight from your gallery or email app into the inbox.

---

## 8. Trash, Restore & Permanent Deletion

Deleted entries, receipts, and photo links move to the **Trash (`/papierkorb/`)** first. Each item offers **Restore** (*Wiederherstellen*) and **Delete permanently** (*Endgültig löschen*). Permanent deletion shows a confirmation summary of affected records and costs; originals archived in Paperless-ngx remain untouched.

---

## 9. Settings, Appearance & Exports

- **Settings (`/einstellungen/`):** Manage project categories/templates/fields, account sessions, AI/Paperless/IMAP status, media cleanup, user feedback, system monitor, and **Appearance** (independent primary/secondary palettes, custom hex colors, density, and corner radius).
- **Exports (`/system/` or per project/entry):**
  - **Compact PDF:** Clean maintenance/work table without photos.
  - **Detailed PDF:** Includes work steps and photos.
  - **ZIP Export:** Versioned JSON, CSV, readable index, media, and receipt originals for sharing or archiving. Note that exports are for portability and do not replace full system backups (see [Self-Hosting & Operations Guide](SELF-HOSTING.md)).
