/* Browser-local appearance. Apply validated values before CSS paints. */
(() => {
  const root = document.documentElement;
  const palettes = ['mint', 'cobalt', 'amber', 'graphite', 'ruby', 'violet', 'cyan', 'custom'];
  const options = {
    theme: {key: 'projektakte-theme', values: ['light', 'dark'], fallback: () => window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'},
    palette: {key: 'projektakte-palette', values: palettes, fallback: () => 'mint'},
    secondaryPalette: {key: 'projektakte-secondary-palette', values: palettes, fallback: () => read('palette')},
    primaryColor: {key: 'projektakte-primary-color', color: true, fallback: () => '#6be8b5'},
    secondaryColor: {key: 'projektakte-secondary-color', color: true, fallback: () => '#00a6de'},
    density: {key: 'projektakte-density', values: ['standard', 'compact'], fallback: () => 'standard'},
    radius: {key: 'projektakte-radius', values: ['subtle', 'standard', 'soft'], fallback: () => 'standard'}
  };
  const colorNames = ['palette', 'secondaryPalette', 'primaryColor', 'secondaryColor'];
  const hexPattern = /^#[0-9a-f]{6}$/i;
  function validate(name, value) {
    const option = options[name];
    if (option.color) return hexPattern.test(value || '') ? value.toLowerCase() : option.fallback();
    return option.values.includes(value) ? value : option.fallback();
  }
  function read(name) {
    try { return validate(name, localStorage.getItem(options[name].key)); }
    catch { return options[name].fallback(); }
  }
  function channels(hex) {
    return [1, 3, 5].map(offset => parseInt(hex.slice(offset, offset + 2), 16));
  }
  function mix(first, second, amount) {
    const a = channels(first), b = channels(second);
    return '#' + a.map((v, i) => Math.round(v + (b[i] - v) * amount).toString(16).padStart(2, '0')).join('');
  }
  function refreshCustomColors() {
    // Custom primary colors are exact user choices, including very dark tones.
    const accent = validate('primaryColor', root.dataset.primaryColor);
    // Semantic CSS variables remain in stylesheets so print and accessibility
    // rules can override custom colors just like predefined palettes.
    root.style.setProperty('--custom-accent', accent);
    root.style.setProperty('--custom-accent-hover', accent);
    root.style.setProperty('--custom-secondary', validate('secondaryColor', root.dataset.secondaryColor));
  }
  function apply(name, value) {
    root.dataset[name] = validate(name, value);
    document.querySelectorAll('[data-appearance="' + name + '"]').forEach(input => {
      if (input.type === 'radio') input.checked = input.value === root.dataset[name];
      else {
        input.value = root.dataset[name];
        input.setCustomValidity('');
      }
    });
    if (name === 'theme') {
      const button = document.getElementById('theme-toggle');
      if (button) {
        const dark = root.dataset.theme === 'dark';
        button.setAttribute('aria-pressed', String(dark));
        button.textContent = dark ? 'Hellmodus' : 'Dunkelmodus';
        button.title = dark ? 'Hellmodus aktivieren' : 'Dunkelmodus aktivieren';
      }
    }
  }
  function syncBrowserColor() {
    const dark = root.dataset.theme === 'dark';
    const color = root.dataset.secondaryPalette === 'custom'
      ? mix(dark ? '#05080d' : '#ffffff', root.dataset.secondaryColor, dark ? 0.05 : 0.08)
      : getComputedStyle(root).getPropertyValue('--bg').trim();
    if (color) document.querySelector('meta[name="theme-color"]')?.setAttribute('content', color);
  }
  function status(text) {
    const node = document.getElementById('appearance-status');
    if (node) node.textContent = text;
  }
  function choose(name, value) {
    apply(name, value);
    if (name === 'primaryColor') apply('palette', 'custom');
    if (name === 'secondaryColor') apply('secondaryPalette', 'custom');
    refreshCustomColors();
    syncBrowserColor();
    let stored = true;
    try {
      // Freeze both roles on a color change, preserving the other color and
      // remembered custom values when switching back to a predefined palette.
      const names = colorNames.includes(name) ? ['secondaryPalette', 'palette', 'primaryColor', 'secondaryColor'] : [name];
      for (const key of names) localStorage.setItem(options[key].key, root.dataset[key]);
    } catch { stored = false; }
    status(stored ? 'Übernommen und in diesem Browser gespeichert.' : 'Für diese Seite übernommen. Der Browser erlaubt keine dauerhafte Speicherung.');
  }
  for (const name of Object.keys(options)) apply(name, read(name));
  refreshCustomColors();
  document.addEventListener('DOMContentLoaded', () => {
    for (const name of Object.keys(options)) apply(name, root.dataset[name]);
    syncBrowserColor();
    document.getElementById('theme-toggle')?.addEventListener('click', () => choose('theme', root.dataset.theme === 'dark' ? 'light' : 'dark'));
    document.querySelectorAll('[data-appearance]').forEach(input => {
      const name = input.dataset.appearance;
      if (!Object.hasOwn(options, name)) return;
      if (options[name].color) {
        input.addEventListener('input', () => {
          const valid = hexPattern.test(input.value);
          input.setCustomValidity(valid ? '' : 'Bitte einen Hex-Wert mit sechs Zeichen eingeben, zum Beispiel #12abef.');
          if (valid) choose(name, input.value);
          else status('Noch nicht übernommen: Bitte eine vollständige Hex-Farbe wie #12abef eingeben.');
        });
      } else {
        input.addEventListener('change', () => { if (input.checked) choose(name, input.value); });
      }
    });
  });
  window.addEventListener('storage', event => {
    if (event.key !== null && !Object.values(options).some(option => option.key === event.key)) return;
    for (const name of Object.keys(options)) apply(name, read(name));
    refreshCustomColors();
    syncBrowserColor();
  });
})();
