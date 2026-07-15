"""Focused JSON/API contract tests, independent of the browser readiness suite."""

from services import create_custom_quiz
from tests.support import CardsTestCase


class ApiContractTests(CardsTestCase):
    def test_auth_required_json_mutation_has_safe_401(self):
        response = self.client.post(
            '/create_deck',
            json={'description': 'Private'},
            headers=self.csrf(),
        )

        self.assert_json_error(response, 401)
        self.assertEqual(response.get_json(), {'error': 'Login required'})

        api_endpoint_response = self.client.post('/get_decks', data={}, headers=self.csrf())
        self.assert_json_error(api_endpoint_response, 401)

    def test_json_deck_and_card_mutations_preserve_contract_fields(self):
        user_id = self.user_session()
        create_response = self.client.post(
            '/create_deck',
            json={'description': 'Contract deck'},
            headers=self.csrf(),
        )
        self.assertEqual(create_response.status_code, 200)
        self.assertEqual(create_response.mimetype, 'application/json')
        deck_id = create_response.get_json()['deck_id']

        card_response = self.client.post(
            '/add_card',
            json={'deck_id': deck_id, 'question': 'Question', 'answers': ['Answer']},
            headers=self.csrf(),
        )
        self.assertEqual(card_response.status_code, 200)
        self.assertIn('card_id', card_response.get_json())
        self.assertEqual(card_response.get_json()['success'], True)
        self.assertIsNotNone(user_id)

    def test_non_text_card_answers_and_quiz_options_are_client_errors(self):
        user_id = self.user_session('input-contract-user')
        create_response = self.client.post(
            '/create_deck', json={'description': 'Input validation deck'}, headers=self.csrf(),
        )
        deck_id = create_response.get_json()['deck_id']

        card_response = self.client.post(
            '/add_card',
            json={'deck_id': deck_id, 'question': 'Question', 'answers': [42]},
            headers=self.csrf(),
        )
        self.assert_json_error(card_response, 400)
        self.assertIn('text', card_response.get_json()['error'].lower())

        with self.app.app_context():
            quiz = create_custom_quiz(user_id, 'Input validation quiz')
            quiz_id = quiz.quiz_id
        quiz_response = self.client.post(
            '/add_quiz_question',
            json={
                'quiz_id': quiz_id,
                'question': 'Question',
                'q_type': 'dynamic',
                'option_1': 42,
            },
            headers=self.csrf(),
        )
        self.assert_json_error(quiz_response, 400)
        self.assertIn('text', quiz_response.get_json()['error'].lower())

    def test_malformed_json_is_not_reinterpreted_as_form_data(self):
        self.user_session()
        response = self.client.post(
            '/create_deck',
            data='{"description":',
            content_type='application/json',
            headers=self.csrf(),
        )

        self.assert_json_error(response, 400)
        self.assertEqual(response.get_json()['error'], 'Invalid request.')

    def test_json_only_endpoint_rejects_wrong_content_type(self):
        self.user_session()
        response = self.client.post(
            '/swap_cards',
            data='not-json',
            content_type='text/plain',
            headers=self.csrf(),
        )

        self.assert_json_error(response, 415)

    def test_json_csrf_failure_is_safe(self):
        self.user_session()
        response = self.client.post(
            '/create_deck',
            json={'description': 'No token'},
            headers={'X-CSRFToken': 'wrong-token'},
        )

        self.assert_json_error(response, 400)
        self.assertEqual(response.get_json()['error'], 'Invalid or missing CSRF token')

    def test_api_rate_limit_uses_json_contract_and_retry_after(self):
        previous_limit = self.app.config['RATE_LIMITS']['api']
        self.app.config['RATE_LIMITS']['api'] = '1 per minute'
        limiter = self.app.extensions['cards_limiter']
        limiter.reset()
        try:
            first = self.client.post('/get_card', json={}, headers=self.csrf())
            limited = self.client.post('/get_card', json={}, headers=self.csrf())
        finally:
            self.app.config['RATE_LIMITS']['api'] = previous_limit
            limiter.reset()

        self.assert_json_error(first, 400)
        self.assert_json_error(limited, 429)
        self.assertIn('Retry-After', limited.headers)

    def test_json_404_405_and_413_are_safe(self):
        not_found = self.client.get('/missing-api-resource', headers={'Accept': 'application/json'})
        self.assert_json_error(not_found, 404)

        invalid_identifier = self.client.post('/get_card', json={'card_id': '9' * 20})
        self.assert_json_error(invalid_identifier, 400)

        method_not_allowed = self.client.get('/create_deck')
        self.assert_json_error(method_not_allowed, 405)

        self.user_session()
        self.app.config['MAX_CONTENT_LENGTH'] = 8
        too_large = self.client.post(
            '/create_deck',
            json={'description': 'too large'},
            headers=self.csrf(),
        )
        self.assert_json_error(too_large, 413)

    def test_unexpected_api_exception_does_not_leak_internals(self):
        def raise_private_error():
            raise RuntimeError('private implementation detail')

        with self.app.app_context():
            self.app.add_url_rule(
                '/contract-error',
                endpoint='contract_error',
                view_func=raise_private_error,
            )
        self.app.config['PROPAGATE_EXCEPTIONS'] = False
        with self.assertLogs('app', level='ERROR'):
            response = self.client.get('/contract-error', headers={'Accept': 'application/json'})

        self.assert_json_error(response, 500)
        self.assertNotIn('private implementation detail', response.get_data(as_text=True))


if __name__ == '__main__':
    import unittest

    unittest.main()
