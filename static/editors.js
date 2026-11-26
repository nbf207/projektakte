function initEditors() {
  const changed = node => node.dispatchEvent(new Event('input', { bubbles: true }));
  const deleted = row => row.querySelector('input[name$="-DELETE"]')?.checked;
  const control = (row, suffix) => row.querySelector(`[name$="-${suffix}"]`);

  function fieldWidget(row) {
    const type = control(row, 'kind');
    if (!type) return;
    const kind = type.value, old = control(row, 'value');
    if (!old) return;
    const value = old.type === 'checkbox' ? old.checked : old.value;
    const options = (control(row, 'options')?.value || '').split(/\r?\n/).map(x => x.trim()).filter(Boolean);
    let next;
    if (kind === 'choice') {
      next = document.createElement('select');
      for (const text of ['', ...options]) next.add(new Option(text || '—', text));
      if (value && !options.includes(String(value))) next.add(new Option(String(value), String(value)));
      next.value = String(value || '');
    } else if (kind === 'multiline') {
      next = document.createElement('textarea'); next.rows = 3; next.value = String(value ?? '');
    } else {
      next = document.createElement('input');
      next.type = kind === 'boolean' ? 'checkbox' : kind === 'link' ? 'url' : 'text';
      if (kind === 'boolean') { next.checked = value === true || ['1', 'true', 'on', 'ja'].includes(String(value).toLowerCase()); }
      else next.value = String(value ?? '');
      if (kind === 'number') next.inputMode = 'decimal';
      if (kind === 'date') next.placeholder = 'TT.MM.JJJJ oder MM/JJJJ';
    }
    next.name = old.name; next.id = old.id; old.replaceWith(next);
    const optionLabel = row.querySelector('[data-editor-field="options"]');
    if (optionLabel) optionLabel.hidden = kind !== 'choice';
  }

  document.querySelectorAll('[data-formset]').forEach(section => {
    if (section.dataset.editorBound) return;
    section.dataset.editorBound = '1';
    const rows = section.querySelector('[data-editor-rows]');
    const total = section.querySelector('[name$="-TOTAL_FORMS"]');
    const maximum = Number(section.querySelector('[name$="-MAX_NUM_FORMS"]')?.value || 100);
    const absolute = Number(section.dataset.maxForms || maximum + 10);
    const add = section.querySelector('[data-editor-add]');
    const update = () => { add.disabled = [...rows.children].filter(row => !deleted(row)).length >= maximum || Number(total.value) >= absolute; };
    function initialize(row) {
      row.hidden = !!deleted(row);
      const deleteLabel=row.querySelector('.editor-delete');
      if (deleteLabel) deleteLabel.hidden=true;
      row.querySelector('[data-editor-remove]').addEventListener('click', () => {
        const box = row.querySelector('input[name$="-DELETE"]'); box.checked = true;
        row.hidden = true; update(); changed(box);
      });
      const box = row.querySelector('input[name$="-DELETE"]');
      box?.addEventListener('change', () => { row.hidden = box.checked; update(); });
      fieldWidget(row);
      control(row, 'kind')?.addEventListener('change', () => { fieldWidget(row); changed(row); });
      control(row, 'options')?.addEventListener('input', () => { if (control(row, 'kind')?.value === 'choice') fieldWidget(row); });
    }
    for (const row of rows.children) initialize(row);
    add.addEventListener('click', () => {
      if (add.disabled) return;
      const template = section.querySelector('[data-editor-template]');
      const fragment = document.createElement('template');
      fragment.innerHTML = template.innerHTML.replaceAll('__prefix__', total.value);
      total.value = Number(total.value) + 1;
      const row = fragment.content.firstElementChild; rows.append(row); initialize(row); update(); changed(total);
      row.querySelector('input:not([type="hidden"]),textarea,select')?.focus();
    });
    update();
  });

  document.querySelectorAll('[data-editor-form]').forEach(form => {
    if (form.dataset.editorFormBound) return;
    form.dataset.editorFormBound = '1';
    const selector = form.querySelector('select[name="field_editor"],select[name="positions_editor"]');
    const mode = () => { for (const panel of form.querySelectorAll('[data-mode-panel]')) panel.hidden = panel.dataset.modePanel !== selector.value; };
    if (selector) { selector.addEventListener('change', () => { mode(); changed(selector); }); mode(); }
  });

  function cents(raw) {
    let text = String(raw || '').trim();
    if (!text) return null;
    if (text.includes(',')) {
      if (!/^-?(?:\d+|\d{1,3}(?:\.\d{3})+),\d{1,2}$/.test(text)) return null;
      text = text.replaceAll('.', '').replace(',', '.');
    } else if (/^-?\d{1,3}(?:\.\d{3})+$/.test(text)) text = text.replaceAll('.', '');
    if (!/^-?\d+(?:\.\d{1,2})?$/.test(text)) return null;
    const sign = text.startsWith('-') ? -1 : 1;
    const [whole, fraction = ''] = text.replace('-', '').split('.');
    const amount = sign * (Number(whole) * 100 + Number(fraction.padEnd(2, '0')));
    return Number.isSafeInteger(amount) ? amount : null;
  }
  const review = document.querySelector('#review-form');
  if (!review || review.dataset.reviewTotalsBound) return;
  review.dataset.reviewTotalsBound = '1';
  function totals() {
    const t = window.t || (s => s);
    const locale = (document.documentElement.lang || 'de').toLowerCase().startsWith('en') ? 'en-US' : 'de-DE';
    const gross = cents(review.elements.gross_total.value);
    const currency = /^[A-Z]{3}$/.test(review.elements.currency.value) ? review.elements.currency.value : 'EUR';
    const money = n => new Intl.NumberFormat(locale, { style: 'currency', currency }).format(n / 100);
    const positions = review.querySelectorAll('[data-formset="positions"] [data-editor-row]');
    let positionTotal = 0, invalidPosition = false;
    for (const row of positions) {
      if (deleted(row)) continue;
      if (!control(row,'description').value.trim() && !control(row,'part_number').value.trim() && !control(row,'amount').value.trim()) continue;
      const amount = cents(control(row, 'amount').value);
      if (amount === null) invalidPosition = true; else positionTotal += amount;
    }
    const positionStatus = review.querySelector('[data-position-total]');
    positionStatus.textContent = invalidPosition ? t('Positionen noch unvollständig.') : `${t('Positionssumme: ')}${money(positionTotal)}${gross === null ? '' : t(' · Abweichung zu Brutto: ') + money(gross - positionTotal)}`;
    let allocated = 0, invalidAllocation = false, hasRows = false;
    for (const input of review.querySelectorAll('[name="alloc_amount"]')) {
      if (!input.value.trim()) continue;
      hasRows = true; const amount = cents(input.value);
      if (amount === null) invalidAllocation = true; else allocated += amount;
    }
    const connect = review.elements.connect;
    if (connect.value && !hasRows) allocated = cents(connect.selectedOptions[0].dataset.allocated) || 0;
    const status = review.querySelector('[data-allocation-summary]');
    if (gross === null || invalidAllocation) status.textContent = t('Gesamtbetrag und Zuordnungen bitte vervollständigen.');
    else if (review.elements.keep_unallocated.checked) status.textContent = t('Ausdrücklich vollständig unzugeordnet: {amount}. Eingetragene Zuordnungen werden bei dieser Auswahl nicht gebucht.', { amount: money(gross) });
    else status.textContent = t('Zugeordnet: {allocated} · Offener Rest: {remainder}', { allocated: money(allocated), remainder: money(gross - allocated) }) + (connect.value ? t(' · Vorhandene Ausgabe wird verbunden.') : '');
  }
  review.addEventListener('input', totals); review.addEventListener('change', totals); totals();
}
document.addEventListener('DOMContentLoaded', initEditors);
document.addEventListener('lg:navigate', initEditors);
