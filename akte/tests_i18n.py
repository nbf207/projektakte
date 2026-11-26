"""Tests for bilingual (DE/EN) UI, compile_mo.py, currency, timezone, Gemini language, demo, and exports."""
from __future__ import annotations

import gettext
import io
import json
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import translation

from .adapters import GeminiAI, demo_proposal
from .definitions import ensure_project_definitions
from .i18n import currency_symbol, effective_currency, effective_language, format_money, format_number
from .models import Allocation, CostSource, Entry, Preferences, Project


class CompileMoTests(SimpleTestCase):
    def test_compile_mo_supports_multiline_escapes_context_plurals_and_skips_fuzzy(self):
        scripts_dir = str(Path(settings.BASE_DIR) / 'scripts')
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import compile_mo

        po_text = (
            'msgid ""\n'
            'msgstr ""\n'
            '"Content-Type: text/plain; charset=UTF-8\\n"\n'
            '"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n\n'
            'msgid "Hallo\\n"\n'
            '"Welt \\"Projektakte\\""\n'
            'msgstr "Hello\\n"\n'
            '"World \\"Projektakte\\""\n\n'
            'msgctxt "nav"\n'
            'msgid "Stand"\n'
            'msgstr "Updated"\n\n'
            'msgid "%(n)s Beleg"\n'
            'msgid_plural "%(n)s Belege"\n'
            'msgstr[0] "%(n)s receipt"\n'
            'msgstr[1] "%(n)s receipts"\n\n'
            '#, fuzzy\n'
            'msgid "Unscharf"\n'
            'msgstr "Should be ignored"\n'
        )
        mo_bytes = compile_mo.compile_mo_bytes(compile_mo.parse_po(po_text))
        catalog = gettext.GNUTranslations(io.BytesIO(mo_bytes))

        self.assertEqual(catalog.gettext('Hallo\nWelt "Projektakte"'), 'Hello\nWorld "Projektakte"')
        self.assertEqual(catalog.pgettext('nav', 'Stand'), 'Updated')
        self.assertEqual(catalog.ngettext('%(n)s Beleg', '%(n)s Belege', 1), '%(n)s receipt')
        self.assertEqual(catalog.ngettext('%(n)s Beleg', '%(n)s Belege', 3), '%(n)s receipts')
        self.assertEqual(catalog.gettext('Unscharf'), 'Unscharf')

    def test_compiled_english_mo_catalog_loads_and_translates(self):
        mo_path = Path(settings.BASE_DIR) / 'locale' / 'en' / 'LC_MESSAGES' / 'django.mo'
        self.assertTrue(mo_path.exists(), 'locale/en/LC_MESSAGES/django.mo must exist')
        with mo_path.open('rb') as fp:
            catalog = gettext.GNUTranslations(fp)
        self.assertEqual(catalog.gettext('Belegeingang'), 'Receipt Inbox')
        self.assertEqual(catalog.gettext('Wartungsplan'), 'Maintenance')
        self.assertEqual(catalog.ngettext('%(counter)s Beleg im Eingang →', '%(counter)s Belege im Eingang →', 1), '%(counter)s receipt in inbox →')
        self.assertEqual(catalog.ngettext('%(counter)s Beleg im Eingang →', '%(counter)s Belege im Eingang →', 2), '%(counter)s receipts in inbox →')


class BilingualUIAndSetupTests(TestCase):
    def test_accept_language_english_renders_login_and_setup_and_persists_preference(self):
        User = get_user_model()
        User.objects.all().delete()
        with override_settings(DEMO_MODE=False):
            client = Client()
            setup_page = client.get('/setup/', HTTP_ACCEPT_LANGUAGE='en-US,en;q=0.9')
            self.assertEqual(setup_page.status_code, 200)
            self.assertContains(setup_page, '<html lang="en">')
            self.assertContains(setup_page, 'Create your first account.')

            res = client.post(
                '/setup/',
                {
                    'username': 'english-owner',
                    'password1': 'Strong-Projektakte-Password-2026!',
                    'password2': 'Strong-Projektakte-Password-2026!',
                },
                HTTP_ACCEPT_LANGUAGE='en-US,en;q=0.9',
            )
            self.assertEqual(res.status_code, 302)
            user = User.objects.get(username='english-owner')
            pref = Preferences.objects.get(owner=user)
            self.assertEqual(pref.language, 'en')
            self.assertEqual(effective_language(user), 'en')

            home = client.get('/')
            self.assertEqual(home.status_code, 200)
            self.assertContains(home, '<html lang="en">')
            self.assertContains(home, 'Receipt Inbox')
            self.assertContains(home, 'Maintenance')

    def test_language_switcher_updates_cookie_and_user_preference(self):
        user = get_user_model().objects.create_user('lang-user', password='Strong-Projektakte-Password-2026!')
        self.client.force_login(user)

        res = self.client.post(reverse('set_language'), {'language': 'en', 'next': '/einstellungen/anbindungen/'})
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.cookies[settings.LANGUAGE_COOKIE_NAME].value, 'en')
        self.assertEqual(Preferences.objects.get(owner=user).language, 'en')

        page = self.client.get('/einstellungen/anbindungen/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '<html lang="en">')
        self.assertContains(page, 'Integration status')

        # Switch back to German
        res_de = self.client.post(reverse('set_language'), {'language': 'de', 'next': '/einstellungen/anbindungen/'})
        self.assertEqual(res_de.status_code, 302)
        page_de = self.client.get('/einstellungen/anbindungen/')
        self.assertContains(page_de, '<html lang="de">')
        self.assertContains(page_de, 'Status der Anbindungen')

    def test_handover_report_respects_english_language(self):
        user = get_user_model().objects.create_user('handover-en', password='Strong-Projektakte-Password-2026!')
        Preferences.objects.create(owner=user, language='en')
        project = Project.objects.create(owner=user, name='Touring Wagon', kind='auto')
        self.client.force_login(user)
        res = self.client.post(reverse('handover'), {'project': project.pk})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, '<html lang="en">')
        self.assertContains(res, 'Project handover report')


class CurrencyAndTimezoneTests(TestCase):
    def test_format_money_and_number_adapt_to_language_and_currency(self):
        self.assertEqual(format_money(Decimal('1234.50'), currency='EUR', lang='de'), '1.234,50 €')
        self.assertEqual(format_money(Decimal('1234.50'), currency='USD', lang='en'), '$1,234.50')
        self.assertEqual(format_money(Decimal('1234.50'), currency='GBP', lang='en'), '£1,234.50')
        self.assertEqual(format_money(Decimal('1234.50'), currency='CHF', lang='en'), '1,234.50 CHF')
        self.assertEqual(format_number(Decimal('1234.5'), lang='de'), '1.234,5')
        self.assertEqual(format_number(Decimal('1234.5'), lang='en'), '1,234.5')
        self.assertEqual(currency_symbol('USD'), '$')

    def test_user_currency_preference_drives_project_and_entry_cost_summation(self):
        user = get_user_model().objects.create_user('usd-user', password='Strong-Projektakte-Password-2026!')
        Preferences.objects.create(owner=user, language='en', currency='USD')
        self.assertEqual(effective_currency(user), 'USD')

        project = Project.objects.create(owner=user, name='US Car', kind='auto')
        entry = Entry.objects.create(project=project, title='Oil change', kind='wartung')
        src_usd = CostSource.objects.create(owner=user, label='Oil', amount=Decimal('65.00'), currency='USD')
        src_eur = CostSource.objects.create(owner=user, label='Euro part', amount=Decimal('20.00'), currency='EUR')
        Allocation.objects.create(source=src_usd, project=project, entry=entry, amount=Decimal('65.00'))
        Allocation.objects.create(source=src_eur, project=project, entry=entry, amount=Decimal('20.00'))

        self.assertEqual(entry.cost, Decimal('65.00'))
        self.assertEqual(project.cost, Decimal('65.00'))

        self.client.force_login(user)
        home = self.client.get('/')
        self.assertContains(home, '$65.00')

    def test_zoneinfo_works_with_configured_time_zone(self):
        tz = ZoneInfo(settings.TIME_ZONE)
        self.assertIsNotNone(tz)
        self.assertIsNotNone(ZoneInfo('America/New_York'))
        self.assertIsNotNone(ZoneInfo('UTC'))


class GeminiLanguageAndProjectDefaultsTests(TestCase):
    def test_gemini_prompt_switches_output_language_without_changing_schema_enums(self):
        expected = demo_proposal('DEMO-OEL-001')
        response = httpx.Response(
            200,
            request=httpx.Request('POST', 'https://generativelanguage.googleapis.com'),
            json={'candidates': [{'content': {'parts': [{'text': json.dumps(expected)}]}}]},
        )
        with override_settings(GEMINI_API_KEY='synthetic-key'), patch('akte.adapters.bounded_request', return_value=response) as req:
            GeminiAI().extract('Oil change invoice', context={'response_language': 'en'})
            body = req.call_args.kwargs['json']
            instruction = body['systemInstruction']['parts'][0]['text']
            self.assertIn('OUTPUT LANGUAGE:', instruction)
            self.assertIn('natural English', instruction)
            # Schema enum values must remain unchanged and language-independent
            entry_props = body['generationConfig']['responseJsonSchema']['properties']['entry']['properties']
            self.assertIn('wartung', entry_props['kind']['enum'])
            self.assertIn('erledigt', entry_props['status']['enum'])

    def test_new_english_project_gets_english_default_categories_and_template(self):
        user = get_user_model().objects.create_user('en-defs-user', password='Strong-Projektakte-Password-2026!')
        Preferences.objects.create(owner=user, language='en')
        project = Project.objects.create(owner=user, name='My Car', kind='auto')
        ensure_project_definitions(project)
        names = set(project.categories.values_list('name', flat=True))
        self.assertIn('Maintenance', names)
        self.assertIn('Repair', names)
        self.assertTrue(project.categories.get(name='Maintenance').title_templates.filter(name='Oil Change').exists())


class BilingualPublicDemoTests(TestCase):
    databases = {'default', 'demo'}

    def test_demo_starts_in_english_and_switches_language_and_content_consistently(self):
        from .security import security_cache
        security_cache().clear()
        self.addCleanup(security_cache().clear)
        client = Client()

        start = client.post('/demo/start/', HTTP_ACCEPT_LANGUAGE='en-US,en;q=0.9')
        self.assertEqual(start.status_code, 302)

        home_en = client.get('/demo/')
        self.assertEqual(home_en.status_code, 200)
        self.assertContains(home_en, '<html lang="en">')
        self.assertContains(home_en, 'Nordlicht · Our Touring Wagon')

        # Switch demo to German via /demo/sprache/
        security_cache().clear()
        sw_de = client.post('/demo/sprache/', {'language': 'de', 'next': '/demo/chronik/'})
        self.assertEqual(sw_de.status_code, 302)

        chronik_de = client.get('/demo/chronik/')
        self.assertEqual(chronik_de.status_code, 200)
        self.assertContains(chronik_de, '<html lang="de">')
        self.assertContains(chronik_de, 'Nordlicht · Unser Reisekombi')
