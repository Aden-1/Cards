"""Contract tests for canonical public URLs and deck collaboration."""

from models import (
    Card,
    CuratedCollection,
    CuratedCollectionDeck,
    Deck,
    DeckCollaborator,
    DeckFavorite,
    DeckShareLink,
    Quiz,
    User,
    db,
)
from services import add_card, create_deck, create_user, edit_deck, get_match_game_data
from tests.support import CardsTestCase


class SharingAndUrlTests(CardsTestCase):
    def test_bulk_routes_enforce_source_and_destination_edit_access(self):
        owner_id = self.user_session('bulk-route-owner')
        with self.app.app_context():
            source = create_deck(owner_id, 'Bulk Route Source')
            target = create_deck(owner_id, 'Bulk Route Target')
            source_card = add_card(source.deck_id, 'Owned card', ['Owned answer'])
            outsider = create_user('bulk-route-outsider', 'password12345')
            outsider_deck = create_deck(outsider.user_id, 'Outsider Deck')
            outsider_card = add_card(
                outsider_deck.deck_id, 'Outsider card', ['Outsider answer'],
            )
            source_id = source.deck_id
            target_id = target.deck_id
            source_card_id = source_card.card_id
            outsider_deck_id = outsider_deck.deck_id
            outsider_card_id = outsider_card.card_id

        injected = self.client.post(
            '/cards/bulk',
            json={
                'deck_id': source_id,
                'card_ids': [outsider_card_id],
                'action': 'delete',
            },
            headers={**self.csrf(), 'Accept': 'application/json'},
        )
        self.assert_json_error(injected, 400)

        forbidden_target = self.client.post(
            '/cards/bulk',
            json={
                'deck_id': source_id,
                'target_deck_id': outsider_deck_id,
                'card_ids': [source_card_id],
                'action': 'move',
            },
            headers={**self.csrf(), 'Accept': 'application/json'},
        )
        self.assert_json_error(forbidden_target, 403)

        forbidden_copy = self.client.post(
            '/decks/duplicate',
            json={'deck_id': outsider_deck_id},
            headers={**self.csrf(), 'Accept': 'application/json'},
        )
        self.assert_json_error(forbidden_copy, 403)

        editor = self.client.get(f'/edit?deck_id={source_id}')
        self.assertEqual(editor.status_code, 200)
        self.assertIn(b'Bulk card tools', editor.data)
        self.assertIn(b'Duplicate', editor.data)

        form_bulk_copy = self.client.post(
            '/cards/bulk',
            data={
                'csrf_token': 'contract-csrf-token',
                'deck_id': source_id,
                'card_ids': [source_card_id],
                'action': 'duplicate',
            },
            headers={'Accept': 'application/json'},
        )
        self.assertEqual(form_bulk_copy.status_code, 200)
        self.assertEqual(form_bulk_copy.get_json()['count'], 1)

        copied = self.client.post(
            '/decks/duplicate',
            json={'deck_id': source_id},
            headers={**self.csrf(), 'Accept': 'application/json'},
        )
        self.assertEqual(copied.status_code, 200)
        copied_id = copied.get_json()['deck_id']
        with self.app.app_context():
            copied_deck = db.session.get(Deck, copied_id)
            self.assertEqual(copied_deck.owned_by, owner_id)
            self.assertFalse(copied_deck.is_public)
            self.assertEqual(Card.query.filter_by(deck_id=copied_id).count(), 2)

        moved = self.client.post(
            '/cards/bulk',
            json={
                'deck_id': source_id,
                'target_deck_id': target_id,
                'card_ids': [source_card_id],
                'action': 'move',
            },
            headers={**self.csrf(), 'Accept': 'application/json'},
        )
        self.assertEqual(moved.status_code, 200)
        self.assertEqual(moved.get_json()['count'], 1)
        with self.app.app_context():
            self.assertEqual(db.session.get(Card, source_card_id).deck_id, target_id)
            self.assertIsNotNone(db.session.get(Card, outsider_card_id))

    def test_collection_workflow_orders_accessible_decks_and_protects_private_content(self):
        owner_id = self.user_session('collection-owner')
        with self.app.app_context():
            owned_public = create_deck(owner_id, 'Owned Public', is_public=True)
            owned_private = create_deck(owner_id, 'Owned Private')
            external_owner = create_user('external-collection-owner', 'password12345')
            external_public = create_deck(
                external_owner.user_id, 'External Public', is_public=True,
            )
            external_private = create_deck(
                external_owner.user_id, 'External Private', is_public=False,
            )
            db.session.add(DeckFavorite(
                user_id=owner_id, deck_id=external_public.deck_id,
            ))
            db.session.commit()
            deck_ids = {
                'owned_public': owned_public.deck_id,
                'owned_private': owned_private.deck_id,
                'external_public': external_public.deck_id,
                'external_private': external_private.deck_id,
            }

        created = self.client.post(
            '/collections/create',
            data={
                'title': 'Exam Preparation',
                'description': 'An ordered review plan.',
                'is_public': 'yes',
            },
            headers=self.csrf(),
        )
        self.assertEqual(created.status_code, 302)
        with self.app.app_context():
            collection = CuratedCollection.query.filter_by(owned_by=owner_id).one()
            collection_id = collection.collection_id

        for deck_id in (
            deck_ids['owned_public'],
            deck_ids['owned_private'],
            deck_ids['external_public'],
        ):
            added = self.client.post(
                '/collections/decks/add',
                data={'collection_id': collection_id, 'deck_id': deck_id},
                headers=self.csrf(),
            )
            self.assertEqual(added.status_code, 302)

        denied_private = self.client.post(
            '/collections/decks/add',
            data={
                'collection_id': collection_id,
                'deck_id': deck_ids['external_private'],
            },
            headers=self.csrf(),
        )
        self.assertEqual(denied_private.status_code, 302)
        self.assertIn('Choose+an+accessible+deck', denied_private.headers['Location'])

        moved = self.client.post(
            '/collections/decks/move',
            data={
                'collection_id': collection_id,
                'deck_id': deck_ids['external_public'],
                'direction': 'up',
            },
            headers=self.csrf(),
        )
        self.assertEqual(moved.status_code, 302)
        with self.app.app_context():
            ordered_ids = [
                entry.deck_id for entry in CuratedCollectionDeck.query.filter_by(
                    collection_id=collection_id,
                ).order_by(CuratedCollectionDeck.position).all()
            ]
            self.assertEqual(ordered_ids, [
                deck_ids['owned_public'],
                deck_ids['external_public'],
                deck_ids['owned_private'],
            ])

        manager = self.client.get(f'/collections?collection_id={collection_id}')
        self.assertEqual(manager.status_code, 200)
        self.assertIn(b'Owned Private', manager.data)
        self.assertIn(b'External Public', manager.data)

        owner_preview = self.client.get(f'/collections/{collection_id}')
        self.assertEqual(owner_preview.status_code, 200)
        self.assertIn(b'Owned Private', owner_preview.data)

        with self.client.session_transaction() as current_session:
            current_session.clear()
        public_page = self.client.get(f'/collections/{collection_id}')
        self.assertEqual(public_page.status_code, 200)
        self.assertIn(b'Owned Public', public_page.data)
        self.assertIn(b'External Public', public_page.data)
        self.assertNotIn(b'Owned Private', public_page.data)
        self.assertIn(b'Exam Preparation', self.client.get('/creators/collection-owner').data)

        self.user_session('collection-intruder')
        unauthorized = self.client.post(
            '/collections/edit',
            data={
                'collection_id': collection_id,
                'title': 'Hijacked',
                'description': '',
                'is_public': 'yes',
            },
            headers={**self.csrf(), 'Accept': 'application/json'},
        )
        self.assertEqual(unauthorized.status_code, 404)
        with self.app.app_context():
            self.assertEqual(
                db.session.get(CuratedCollection, collection_id).title,
                'Exam Preparation',
            )

        with self.app.app_context():
            owner_auth_version = db.session.get(User, owner_id).auth_version
        with self.client.session_transaction() as current_session:
            current_session.update(
                user_id=owner_id,
                auth_version=owner_auth_version,
                csrf_token='contract-csrf-token',
            )
        removed = self.client.post(
            '/collections/decks/remove',
            data={
                'collection_id': collection_id,
                'deck_id': deck_ids['external_public'],
            },
            headers=self.csrf(),
        )
        self.assertEqual(removed.status_code, 302)
        deleted = self.client.post(
            '/collections/delete',
            data={'collection_id': collection_id},
            headers=self.csrf(),
        )
        self.assertEqual(deleted.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(db.session.get(CuratedCollection, collection_id))
            self.assertEqual(
                CuratedCollectionDeck.query.filter_by(collection_id=collection_id).count(),
                0,
            )

    def test_saved_deck_library_is_paginated_user_scoped_and_public_only(self):
        owner_id = self.user_session('bookmark-owner')
        with self.app.app_context():
            public_decks = [
                Deck(
                    owned_by=owner_id,
                    description=f'Bookmark {index:02d}',
                    is_public=True,
                )
                for index in range(21)
            ]
            private_deck = Deck(
                owned_by=owner_id, description='Hidden Bookmark', is_public=False,
            )
            other_user_deck = Deck(
                owned_by=owner_id, description='Other User Bookmark', is_public=True,
            )
            db.session.add_all([*public_decks, private_deck, other_user_deck])
            db.session.flush()
            public_ids = [deck.deck_id for deck in public_decks]
            private_id = private_deck.deck_id
            other_user_deck_id = other_user_deck.deck_id
            db.session.commit()

        saver_id = self.user_session('bookmark-reader')
        with self.app.app_context():
            db.session.add_all([
                DeckFavorite(user_id=saver_id, deck_id=deck_id)
                for deck_id in [*public_ids, private_id]
            ])
            db.session.add(DeckFavorite(
                user_id=owner_id, deck_id=other_user_deck_id,
            ))
            db.session.commit()

        first_page = self.client.get('/saved')
        self.assertEqual(first_page.status_code, 200)
        first_html = first_page.get_data(as_text=True)
        self.assertIn('Bookmark 20', first_html)
        self.assertNotIn('Bookmark 00</h3>', first_html)
        self.assertNotIn('Hidden Bookmark', first_html)
        self.assertNotIn('Other User Bookmark', first_html)
        self.assertIn('page=2', first_html)

        second_page = self.client.get('/saved?page=2')
        self.assertEqual(second_page.status_code, 200)
        self.assertIn(b'Bookmark 00', second_page.data)

        removed = self.client.post(
            '/decks/favorite',
            data={'deck_id': public_ids[-1]},
            headers={**self.csrf(), 'Referer': 'http://localhost/saved'},
        )
        self.assertEqual(removed.status_code, 302)
        self.assertNotIn(b'Bookmark 20', self.client.get('/saved').data)

        with self.client.session_transaction() as current_session:
            current_session.clear()
        self.assertEqual(self.client.get('/saved').status_code, 302)

    def test_match_payload_omits_explicit_answer_mapping_and_uses_server_validator(self):
        owner_id = self.user_session('match-payload-owner')
        with self.app.app_context():
            deck = create_deck(owner_id, 'Server Validated Match')
            add_card(deck.deck_id, 'First question', ['First answer'])
            add_card(deck.deck_id, 'Second question', ['Second answer'])
            deck_id = deck.deck_id
            payload = get_match_game_data(owner_id, deck_id)

        self.assertNotIn('answers', payload)
        for card in payload['cards']:
            for answer in card['answer_objects']:
                self.assertNotIn('card_id', answer)

        response = self.client.get(f'/match?deck_id={deck_id}')
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("fetch('/match_answer'", page)
        self.assertNotIn('answer.card_id', page)

    def test_invalid_creator_and_collaborator_usernames_do_not_raise_server_errors(self):
        response = self.client.get(f"/creators/{'x' * 41}")
        self.assertEqual(response.status_code, 404)

        owner_id = self.user_session('validation-owner')
        with self.app.app_context():
            deck = create_deck(owner_id, 'Validation Deck')
            deck_id = deck.deck_id
        response = self.client.post(
            '/decks/collaborators',
            data={'deck_id': deck_id, 'username': 'x' * 41},
            headers=self.csrf(),
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('Active+user+not+found', response.headers['Location'])

    def test_creator_profile_paginates_public_decks_and_quizzes_independently(self):
        owner_id = self.user_session('paged-creator')
        with self.app.app_context():
            db.session.add_all([
                Deck(owned_by=owner_id, description=f'Profile Deck {index}', is_public=True)
                for index in range(21)
            ])
            db.session.add_all([
                Quiz(owned_by=owner_id, title=f'Profile Quiz {index}', is_public=True)
                for index in range(21)
            ])
            db.session.commit()

        first_page = self.client.get('/creators/paged-creator')
        self.assertEqual(first_page.status_code, 200)
        first_html = first_page.get_data(as_text=True)
        self.assertIn('Profile Deck 20', first_html)
        self.assertNotIn('Profile Deck 0<', first_html)
        self.assertIn('Profile Quiz 20', first_html)
        self.assertNotIn('Profile Quiz 0<', first_html)
        self.assertIn('deck_page=2', first_html)
        self.assertIn('quiz_page=2', first_html)

        second_deck_page = self.client.get('/creators/paged-creator?deck_page=2')
        second_html = second_deck_page.get_data(as_text=True)
        self.assertIn('Profile Deck 0', second_html)
        self.assertIn('Profile Quiz 20', second_html)

    def test_homepage_handles_featured_deck_summary_urls(self):
        owner_id = self.user_session('featured-url-owner')
        with self.app.app_context():
            create_deck(owner_id, 'Featured URL Deck', is_public=True, is_featured=True)

        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'/decks/featured-url-deck-', response.data)

    def test_public_deck_legacy_url_redirects_to_canonical_title_id_url(self):
        owner_id = self.user_session('url-owner')
        with self.app.app_context():
            deck = create_deck(owner_id, 'Python Fundamentals', is_public=True)
            deck_id = deck.deck_id

        legacy = self.client.get(f'/public_deck?deck_id={deck_id}', follow_redirects=False)
        self.assertEqual(legacy.status_code, 301)
        self.assertIn(f'/decks/python-fundamentals-{deck_id}', legacy.headers['Location'])

        with self.app.app_context():
            edit_deck(deck_id, 'Modern Python Fundamentals', is_public=True)
        renamed = self.client.get(f'/decks/python-fundamentals-{deck_id}', follow_redirects=False)
        self.assertEqual(renamed.status_code, 301)
        self.assertIn(f'/decks/modern-python-fundamentals-{deck_id}', renamed.headers['Location'])

    def test_public_quiz_legacy_url_redirects_to_canonical_title_id_url(self):
        owner_id = self.user_session('quiz-url-owner')
        with self.app.app_context():
            quiz = Quiz(owned_by=owner_id, title='Python Basics Quiz', is_public=True)
            db.session.add(quiz)
            db.session.commit()
            quiz_id = quiz.quiz_id

        response = self.client.get(f'/public_quiz?quiz_id={quiz_id}', follow_redirects=False)
        self.assertEqual(response.status_code, 301)
        self.assertIn(f'/quizzes/python-basics-quiz-{quiz_id}', response.headers['Location'])

    def test_unlisted_copy_link_and_coauthor_access(self):
        owner_id = self.user_session('sharing-owner')
        with self.app.app_context():
            deck = create_deck(owner_id, 'Private Collaboration Deck', sortable=True)
            original_card = add_card(deck.deck_id, 'Question', ['Answer'])
            deck_id = deck.deck_id
            card_id = original_card.card_id
            answer_id = original_card.answers[0].answer_id
            db.session.add(DeckShareLink(token='copy-link-token', deck_id=deck_id, permission='copy'))
            db.session.commit()

        preview = self.client.get('/s/copy-link-token')
        self.assertEqual(preview.status_code, 200)
        self.assertIn(b'Private Collaboration Deck', preview.data)

        self.user_session('sharing-coauthor')
        response = self.client.post(
            '/copy_public_deck',
            data={'deck_id': deck_id, 'share_token': 'copy-link-token'},
            headers=self.csrf(),
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

        coauthor_id = self.user_session('deck-coauthor')
        with self.app.app_context():
            db.session.add(DeckCollaborator(deck_id=deck_id, user_id=coauthor_id))
            db.session.commit()
        response = self.client.post(
            '/add_card', data={'deck_id': deck_id, 'question': 'Coauthor question', 'answers': 'Answer'}, headers=self.csrf()
        )
        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            self.client.post('/list_cards', data={'deck_id': deck_id}, headers=self.csrf()).status_code,
            200,
        )
        self.assertEqual(
            self.client.post('/get_card', data={'card_id': card_id}, headers=self.csrf()).status_code,
            200,
        )
        self.assertEqual(
            self.client.post('/match_attempt', data={
                'answer_id': answer_id,
                'selected_question_id': card_id,
            }, headers=self.csrf()).status_code,
            200,
        )
        self.assertEqual(
            self.client.post('/master/rate', data={
                'deck_id': deck_id,
                'card_id': card_id,
                'rating': 'still_learning',
            }, headers=self.csrf()).status_code,
            302,
        )
        with self.app.app_context():
            ordered_card_ids = [
                card.card_id for card in
                Card.query.filter_by(deck_id=deck_id).order_by(Card.position).all()
            ]
        self.assertEqual(
            self.client.post('/check_reorder', json={
                'deck_id': deck_id,
                'ordered_card_ids': ordered_card_ids,
            }, headers=self.csrf()).status_code,
            200,
        )
        self.assertEqual(self.client.get(f'/public_deck?deck_id={deck_id}').status_code, 200)
