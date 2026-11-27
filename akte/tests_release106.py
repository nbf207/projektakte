from pathlib import Path

from django.conf import settings
from django.test import TestCase


class Release106FixesTests(TestCase):
    def test_v106_gallery_thumbs_no_view_transition_and_pdf_csp(self):
        root = Path(settings.BASE_DIR)
        self.assertEqual(settings.APP_VERSION, '1.1.0')

        entry_tpl = (root / 'templates' / 'entry.html').read_text(encoding='utf-8')
        self.assertIn('class="photo-thumb"', entry_tpl)

        style_css = (root / 'static' / 'style.css').read_text(encoding='utf-8')
        self.assertIn('.gallery .photo-thumb{', style_css)
        self.assertIn('aspect-ratio:1/1;overflow:hidden', style_css)
        self.assertIn('object-fit:contain', style_css)
        self.assertNotIn('view-transition', style_css)
        self.assertIn('color-scheme:light', style_css)
        self.assertIn('.row{margin:0;', style_css)
        self.assertIn('.titlecell{display:flex;align-items:baseline;min-width:0}', style_css)

        ws_css = (root / 'static' / 'workspace.css').read_text(encoding='utf-8')
        self.assertIn('#theme-toggle{padding:0;width:38px;height:38px;min-height:38px;font-size:0;color:transparent;overflow:hidden}', ws_css)
        self.assertIn('.app-window .row{margin:0;padding-inline:14px}', ws_css)

        login_tpl = (root / 'templates' / 'login.html').read_text(encoding='utf-8')
        self.assertIn("style.css' %}?v=110", login_tpl)
        self.assertNotIn('?v=0710', login_tpl)

        setup_tpl = (root / 'templates' / 'setup.html').read_text(encoding='utf-8')
        self.assertIn("style.css' %}?v=110", setup_tpl)

        shell_js = (root / 'static' / 'instant-shell.js').read_text(encoding='utf-8')
        self.assertIn('neu|bearbeiten|edit', shell_js)
        self.assertIn('if (!cached || Date.now() - cached.ts >= TTL_MS) return;', shell_js)

        review_tpl = (root / 'templates' / 'review.html').read_text(encoding='utf-8')
        self.assertIn('Im neuen Tab öffnen', review_tpl)
