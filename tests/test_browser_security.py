"""Browser response, security-header, and asset-cache regression tests."""

import gzip
import os
from pathlib import Path
import re
import unittest

import brotli

os.environ['APP_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-only-secret-key'
os.environ['DATABASE_URL'] = 'sqlite://'

import app as cards_app
import routes
from extensions import db
from models import User


class BrowserSecurityTests(unittest.TestCase):
    def setUp(self):
        self.app = cards_app.app
        self.app.config.update(TESTING=True, PUBLIC_REGISTRATION_ENABLED=True)
        routes.limiter.reset()
        self.client = cards_app.app.test_client()
        with cards_app.app.app_context():
            db.drop_all()
            db.create_all()

    def tearDown(self):
        with cards_app.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def _login_session(self, user_id):
        with cards_app.app.app_context():
            auth_version = db.session.get(User, user_id).auth_version
        with self.client.session_transaction() as current_session:
            current_session.update({
                'user_id': user_id,
                'auth_version': auth_version,
                'csrf_token': 'csrf-test-token',
            })

    def test_health_and_browser_security_headers_are_enabled(self):
        response = self.client.get('/healthz')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'status': 'ok'})
        self.assertIn('Content-Security-Policy', response.headers)
        self.assertEqual(response.headers['X-Frame-Options'], 'DENY')

    def test_csp_disallows_inline_styles_and_templates_contain_none(self):
        response = self.client.get('/')
        csp = response.headers['Content-Security-Policy']
        self.assertIn("style-src 'self'", csp)
        self.assertNotIn('unsafe-inline', csp)
        root = Path(__file__).resolve().parents[1]
        for template in (root / 'templates').glob('*.html'):
            source = template.read_text(encoding='utf-8')
            self.assertIsNone(re.search(r'<style\b|\bstyle\s*=', source, re.I), template.name)

    def test_rich_text_is_server_escaped_and_rendered_without_raw_html(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user('rich_text_owner', 'password12345')
            deck = cards_app.create_deck(
                owner.user_id, 'Safe formulas', is_public=True,
            )
            cards_app.add_card(
                deck.deck_id,
                '<img src=x onerror=alert(1)> $\\frac{1}{2}$ **bold**',
                ['<script>alert(1)</script>'],
            )
            deck_id = deck.deck_id
            owner_id = owner.user_id

        response = self.client.get(f'/public_deck?deck_id={deck_id}', follow_redirects=True)
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-rich-text', page)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', page)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', page)
        self.assertNotIn('<img src=x', page)

        self._login_session(owner_id)
        tsv = self.client.get(f'/decks/{deck_id}/download.tsv')
        self.assertEqual(tsv.status_code, 200)
        self.assertEqual(tsv.mimetype, 'text/tab-separated-values')
        self.assertIn(b'$\\frac{1}{2}$', tsv.data)

        root = Path(__file__).resolve().parents[1]
        renderer = (root / 'static' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('element.replaceChildren(richTextFragment(value))', renderer)
        self.assertNotIn('element.innerHTML = value', renderer)

    def test_form_lock_preserves_named_submitter_before_disabling_buttons(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / 'static' / 'app.js').read_text(encoding='utf-8')

        preserve_index = script.index('submitIntent.value = submitter.value;')
        disable_index = script.index('button.disabled = true;', preserve_index)
        self.assertLess(preserve_index, disable_index)
        self.assertIn("submitIntent.type = 'hidden';", script)
        self.assertIn("submitIntent.name = submitter.name;", script)

    def test_static_assets_use_content_hash_urls_and_immutable_cache_headers(self):
        response = self.client.get('/')
        page = response.get_data(as_text=True)
        asset_urls = re.findall(r'(/static/[^"\']+\?v=[0-9a-f]{16})', page)

        self.assertEqual(response.headers['Cache-Control'], 'public, max-age=60')
        self.assertEqual(response.headers['X-Cards-Public'], '1')
        self.assertEqual(len(asset_urls), 5)
        self.assertEqual(page.count('defer'), 2)
        self.assertIn("script-src 'self' 'nonce-", response.headers['Content-Security-Policy'])
        response.close()

        unversioned_response = self.client.get('/static/app.css')
        self.assertEqual(unversioned_response.status_code, 200)
        self.assertEqual(unversioned_response.headers['Cache-Control'], 'no-cache, must-revalidate')
        unversioned_response.close()

        for asset_url in asset_urls:
            asset_response = self.client.get(asset_url)
            self.assertEqual(asset_response.status_code, 200, asset_url)
            self.assertEqual(asset_response.headers['Cache-Control'], 'public, max-age=31536000, immutable', asset_url)
            self.assertTrue(asset_response.headers.get('ETag'), asset_url)
            not_modified = self.client.get(
                asset_url,
                headers={'If-None-Match': asset_response.headers['ETag']},
            )
            self.assertEqual(not_modified.status_code, 304, asset_url)
            not_modified.close()
            asset_response.close()

        with cards_app.app.app_context():
            user = cards_app.create_user('private-cache-user', 'password12345')
            user_id = user.user_id
        self._login_session(user_id)
        authenticated_page = self.client.get('/')
        self.assertEqual(
            authenticated_page.headers['Cache-Control'], 'no-store, private',
        )
        self.assertNotIn('X-Cards-Public', authenticated_page.headers)
        authenticated_page.close()

    def test_stale_versioned_static_urls_redirect_to_the_current_hash(self):
        response = self.client.get('/static/app.css?v=0000000000000000')

        self.assertEqual(response.status_code, 302)
        self.assertRegex(response.headers['Location'], r'/static/app\.css\?v=[0-9a-f]{16}$')
        response.close()

    def test_login_rejects_backslash_based_external_next_redirect(self):
        with cards_app.app.app_context():
            cards_app.create_user('redirect_guard', 'password12345')

        with self.client.session_transaction() as current_session:
            current_session['csrf_token'] = 'csrf-test-token'

        response = self.client.post(
            '/login',
            data={
                'username': 'redirect_guard',
                'password': 'password12345',
                'next': '/\\evil.example',
                'csrf_token': 'csrf-test-token',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/')
        response.close()

    def test_authenticated_html_is_never_shared_or_disk_cached(self):
        with cards_app.app.app_context():
            user = cards_app.create_user('cache_policy_user', 'password12345', email='cache-policy@example.test')
            user_id = user.user_id

        self._login_session(user_id)
        response = self.client.get('/account')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store, private')
        self.assertIn('cache_policy_user', response.get_data(as_text=True))
        self.assertRegex(response.get_data(as_text=True), r'nonce="[A-Za-z0-9_-]+"')
        response.close()

    def test_responses_support_brotli_and_gzip_compression(self):
        brotli_response = self.client.get('/', headers={'Accept-Encoding': 'br'})
        gzip_response = self.client.get('/', headers={'Accept-Encoding': 'gzip'})

        self.assertEqual(brotli_response.headers.get('Content-Encoding'), 'br')
        self.assertEqual(gzip_response.headers.get('Content-Encoding'), 'gzip')
        self.assertIn('Accept-Encoding', brotli_response.headers.get('Vary', ''))
        self.assertIn(b'CARDS', brotli.decompress(brotli_response.data))
        self.assertIn(b'CARDS', gzip.decompress(gzip_response.data))
        brotli_response.close()
        gzip_response.close()


if __name__ == '__main__':
    unittest.main()
