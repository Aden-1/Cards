"""Contract tests for canonical public URLs and deck collaboration."""

from models import (
    Card,
    CuratedCollection,
    CuratedCollectionDeck,
    Deck,
    DeckCollaborator,
    DeckFavorite,
    DeckRating,
    DeckShareLink,
    Quiz,
    QuizCollaborator,
    QuizFavorite,
    QuizRating,
    QuizReport,
    QuizShareLink,
    User,
    db,
)
from services import add_card, create_deck, create_user, edit_deck, get_match_game_data
from tests.support import CardsTestCase


class SharingAndUrlTests(CardsTestCase):
    def _switch_user(self, user_id):
        with self.app.app_context():
            auth_version = db.session.get(User, user_id).auth_version
        with self.client.session_transaction() as current_session:
            current_session.clear()
            current_session.update(
                user_id=user_id,
                auth_version=auth_version,
                csrf_token='contract-csrf-token',
            )

    def test_public_deck_uses_compact_visual_community_controls(self):
        owner_id = self.user_session('public-layout-owner')
        with self.app.app_context():
            deck = create_deck(
                owner_id, 'Organized Public Deck',
                detailed_description='A clear description for this deck.',
                tags='science, review', is_public=True,
            )
            add_card(deck.deck_id, 'Question', ['Answer'])
            viewer = create_user('public-layout-viewer', 'password12345')
            db.session.add_all([
                DeckFavorite(user_id=viewer.user_id, deck_id=deck.deck_id),
                DeckRating(user_id=viewer.user_id, deck_id=deck.deck_id, rating=3),
            ])
            db.session.commit()
            deck_id = deck.deck_id
            viewer_id = viewer.user_id

        self._switch_user(viewer_id)
        response = self.client.get(f'/decks/organized-public-deck-{deck_id}')
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)

        ordered_rows = [
            'public-deck-heading-row',
            'public-deck-facts-row',
            'public-deck-description-row',
            'public-deck-tags-row',
            'public-deck-actions-row',
        ]
        row_positions = [page.index(row) for row in ordered_rows]
        self.assertEqual(row_positions, sorted(row_positions))
        self.assertIn('bookmark-icon-button is-bookmarked', page)
        self.assertIn('title="Remove this deck from your bookmarks"', page)
        self.assertEqual(page.count('class="star-rating-button'), 5)
        self.assertEqual(page.count('class="star-rating-button is-selected"'), 3)
        self.assertIn('name="rating"', page)
        self.assertIn('value="3"', page)
        self.assertIn('aria-pressed="true"', page)
        self.assertNotIn('id="deckRating"', page)
        self.assertNotIn('<details', page)
        self.assertIn('data-bs-target="#reportDeckModal"', page)
        self.assertIn('id="reportDeckModal"', page)
        self.assertIn('action="/decks/report"', page)
        for label in ('Study', 'Copy Deck', 'Back', 'Report'):
            self.assertIn(f'>{label}<', page)

        rated = self.client.post(
            '/decks/rate', data={'deck_id': deck_id, 'rating': '4'},
            headers=self.csrf(),
        )
        self.assertEqual(rated.status_code, 302)
        with self.app.app_context():
            self.assertEqual(
                db.session.get(DeckRating, (viewer_id, deck_id)).rating, 4,
            )

    def test_public_deck_previews_share_four_row_layout_and_copy_action(self):
        owner_id = self.user_session('preview-layout-owner')
        with self.app.app_context():
            deck = create_deck(
                owner_id, 'Universal Preview Layout',
                detailed_description='The shared public preview description.',
                is_public=True,
            )
            add_card(deck.deck_id, 'First question', ['First answer'])
            add_card(deck.deck_id, 'Second question', ['Second answer'])
            deck = db.session.get(Deck, deck.deck_id)
            deck.is_featured = True
            viewer = create_user('preview-layout-viewer', 'password12345')
            collection = CuratedCollection(
                owned_by=owner_id, title='Preview Collection', is_public=True,
            )
            db.session.add(collection)
            db.session.flush()
            db.session.add_all([
                CuratedCollectionDeck(
                    collection_id=collection.collection_id,
                    deck_id=deck.deck_id,
                    position=1,
                ),
                DeckFavorite(user_id=viewer.user_id, deck_id=deck.deck_id),
                DeckRating(user_id=owner_id, deck_id=deck.deck_id, rating=5),
                DeckRating(user_id=viewer.user_id, deck_id=deck.deck_id, rating=3),
            ])
            db.session.commit()
            deck_id = deck.deck_id
            viewer_id = viewer.user_id
            collection_id = collection.collection_id

        self._switch_user(viewer_id)
        surfaces = (
            '/search?q=Universal+Preview+Layout',
            '/saved',
            '/creators/preview-layout-owner',
            f'/collections/{collection_id}',
            '/',
        )
        for path in surfaces:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                page = response.get_data(as_text=True)
                start = page.index(
                    '<article class="public-card public-deck-preview-card">',
                )
                end = page.index('</article>', start)
                preview = page[start:end]
                rows = [
                    'public-deck-preview-heading',
                    'public-deck-preview-facts',
                    'public-deck-preview-description',
                    'public-deck-preview-actions',
                ]
                positions = [preview.index(row) for row in rows]
                self.assertEqual(positions, sorted(positions))
                self.assertIn('bookmark-icon-button is-bookmarked', preview)
                self.assertIn('Universal Preview Layout', preview)
                self.assertIn('status-chip public', preview)
                self.assertIn('<strong>4.0 / 5</strong>', preview)
                self.assertIn('<strong>2</strong> cards', preview)
                self.assertIn('The shared public preview description.', preview)
                self.assertIn('>View<', preview)
                self.assertIn('action="/copy_public_deck"', preview)
                self.assertIn('>Copy Deck<', preview)
                self.assertNotIn('>Match<', preview)

        copied = self.client.post(
            '/copy_public_deck', data={'deck_id': deck_id}, headers=self.csrf(),
        )
        self.assertEqual(copied.status_code, 302)
        with self.app.app_context():
            self.assertIsNotNone(Deck.query.filter_by(
                owned_by=viewer_id,
                description='Universal Preview Layout (Copy)',
            ).one_or_none())

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

        page = response.get_data(as_text=True)
        expected_copy = (
            'Create, discover, and',
            'master</span> flashcard decks.',
            'Build and organize your own study decks',
            'Find public decks and quizzes by topic',
            'Study, play, quiz, and track your progress',
            'Popular Topics',
            'Find a deck by subject.',
            'Learning Modes',
            'Choose how you want to practice.',
            'Explore decks from the community.',
            'Find something interesting and start studying.',
            'Browse All Decks',
        )
        for text in expected_copy:
            self.assertIn(text, page)

        self.assertEqual(page.count('home-hero-title'), 1)
        self.assertEqual(page.count('home-section-title'), 3)
        self.assertLess(page.index('Popular Topics'), page.index('Learning Modes'))
        self.assertLess(page.index('Learning Modes'), page.index('Study Mode'))
        self.assertNotIn('A redesigned learning hub', page)
        self.assertNotIn('Live public decks worth exploring.', page)

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

    def test_quiz_unlisted_copy_links_and_coauthor_permissions(self):
        owner_id = self.user_session('quiz-sharing-owner')
        with self.app.app_context():
            collaborator = create_user('quiz-sharing-coauthor', 'password12345')
            quiz = Quiz(
                owned_by=owner_id, title='Private Shared Quiz', is_public=False,
            )
            db.session.add(quiz)
            db.session.commit()
            collaborator_id = collaborator.user_id
            quiz_id = quiz.quiz_id

        added = self.client.post(
            '/quizzes/collaborators',
            data={'quiz_id': quiz_id, 'username': 'quiz-sharing-coauthor'},
            headers=self.csrf(),
        )
        self.assertEqual(added.status_code, 302)
        created_link = self.client.post(
            '/quizzes/share',
            data={'quiz_id': quiz_id, 'permission': 'copy'},
            headers=self.csrf(),
        )
        self.assertEqual(created_link.status_code, 302)
        self.assertTrue(created_link.headers['Location'].endswith('#quiz-sharing'))
        owner_editor = self.client.get(f'/edit_quiz?quiz_id={quiz_id}')
        self.assertIn(b'id="quiz-sharing"', owner_editor.data)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(
                QuizCollaborator, (quiz_id, collaborator_id),
            ))
            share_link = QuizShareLink.query.filter_by(quiz_id=quiz_id).one()
            share_token = share_link.token

        self._switch_user(collaborator_id)
        editor = self.client.get(f'/edit_quiz?quiz_id={quiz_id}')
        self.assertEqual(editor.status_code, 200)
        self.assertIn(b'Private Shared Quiz', editor.data)
        self.assertNotIn(b'Sharing and co-authors', editor.data)
        self.assertIn(b'Only the owner can change it.', editor.data)
        forbidden_publish = self.client.post(
            '/edit_custom_quiz',
            data={
                'quiz_id': quiz_id, 'title': 'Private Shared Quiz',
                'is_public': 'yes',
            },
            headers={**self.csrf(), 'Accept': 'application/json'},
        )
        self.assert_json_error(forbidden_publish, 403)
        with self.app.app_context():
            self.assertFalse(db.session.get(Quiz, quiz_id).is_public)
        edited = self.client.post(
            '/edit_custom_quiz',
            data={
                'quiz_id': quiz_id, 'title': 'Co-authored Quiz',
                'description': 'Edited together', 'tags': 'teamwork',
            },
            headers=self.csrf(),
        )
        self.assertEqual(edited.status_code, 302)
        self.assert_json_error(self.client.post(
            '/delete_custom_quiz', data={'quiz_id': quiz_id},
            headers={**self.csrf(), 'Accept': 'application/json'},
        ), 403)
        self.assert_json_error(self.client.post(
            '/quizzes/share', data={'quiz_id': quiz_id, 'permission': 'view'},
            headers={**self.csrf(), 'Accept': 'application/json'},
        ), 403)

        viewer_id = self.user_session('quiz-sharing-viewer')
        shared = self.client.get(f'/sq/{share_token}')
        self.assertEqual(shared.status_code, 200)
        self.assertIn(b'Co-authored Quiz', shared.data)
        copied = self.client.post(
            '/copy_public_quiz',
            data={'quiz_id': quiz_id, 'share_token': share_token},
            headers=self.csrf(),
        )
        self.assertEqual(copied.status_code, 302)
        with self.app.app_context():
            copy = Quiz.query.filter_by(
                owned_by=viewer_id, title='Co-authored Quiz (Copy)',
            ).one()
            self.assertFalse(copy.is_public)

    def test_public_collaborator_metadata_edits_preserve_visibility(self):
        owner_id = self.user_session('public-collaboration-owner')
        with self.app.app_context():
            collaborator = create_user('public-collaboration-editor', 'password12345')
            deck = create_deck(owner_id, 'Public Shared Deck', is_public=True)
            quiz = Quiz(
                owned_by=owner_id, title='Public Shared Quiz', is_public=True,
            )
            db.session.add(quiz)
            db.session.flush()
            db.session.add_all([
                DeckCollaborator(deck_id=deck.deck_id, user_id=collaborator.user_id),
                QuizCollaborator(quiz_id=quiz.quiz_id, user_id=collaborator.user_id),
            ])
            db.session.commit()
            collaborator_id = collaborator.user_id
            deck_id = deck.deck_id
            quiz_id = quiz.quiz_id

        self._switch_user(collaborator_id)
        deck_editor = self.client.get(f'/edit?deck_id={deck_id}')
        quiz_editor = self.client.get(f'/edit_quiz?quiz_id={quiz_id}')
        self.assertIn(b'name="is_public" value="yes"', deck_editor.data)
        self.assertIn(b'name="is_public" value="yes"', quiz_editor.data)

        deck_edit = self.client.post(
            '/edit_deck', data={
                'deck_id': deck_id, 'description': 'Edited Public Shared Deck',
            }, headers=self.csrf(),
        )
        quiz_edit = self.client.post(
            '/edit_custom_quiz', data={
                'quiz_id': quiz_id, 'title': 'Edited Public Shared Quiz',
                'is_public': 'yes',
            }, headers=self.csrf(),
        )
        self.assertEqual(deck_edit.status_code, 302)
        self.assertEqual(quiz_edit.status_code, 302)
        with self.app.app_context():
            self.assertTrue(db.session.get(Deck, deck_id).is_public)
            self.assertTrue(db.session.get(Quiz, quiz_id).is_public)

    def test_quiz_favorites_ratings_reports_and_moderation(self):
        owner_id = self.user_session('quiz-community-owner')
        with self.app.app_context():
            quiz = Quiz(
                owned_by=owner_id, title='Community Quiz', is_public=True,
            )
            db.session.add(quiz)
            db.session.flush()
            db.session.add(QuizShareLink(
                token='moderated-quiz-link', quiz_id=quiz.quiz_id,
                permission='view',
            ))
            db.session.commit()
            quiz_id = quiz.quiz_id

        reader_id = self.user_session('quiz-community-reader')
        self.assertEqual(self.client.post(
            '/quizzes/favorite', data={'quiz_id': quiz_id}, headers=self.csrf(),
        ).status_code, 302)
        self.assertEqual(self.client.post(
            '/quizzes/rate', data={'quiz_id': quiz_id, 'rating': 5},
            headers=self.csrf(),
        ).status_code, 302)
        self.assertEqual(self.client.post(
            '/quizzes/report',
            data={'quiz_id': quiz_id, 'reason': 'inaccurate', 'detail': 'Check answer.'},
            headers=self.csrf(),
        ).status_code, 302)
        self.assertEqual(self.client.post(
            '/quizzes/report',
            data={'quiz_id': quiz_id, 'reason': 'inaccurate'},
            headers=self.csrf(),
        ).status_code, 302)
        saved = self.client.get('/saved-quizzes')
        self.assertEqual(saved.status_code, 200)
        self.assertIn(b'Community Quiz', saved.data)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(QuizFavorite, (reader_id, quiz_id)))
            self.assertEqual(
                db.session.get(QuizRating, (reader_id, quiz_id)).rating, 5,
            )
            report = QuizReport.query.filter_by(
                user_id=reader_id, quiz_id=quiz_id,
            ).one()
            report_id = report.report_id
            moderator = create_user('quiz-community-moderator', 'password12345')
            moderator.role = 'moderator'
            db.session.commit()
            moderator_id = moderator.user_id

        self._switch_user(moderator_id)
        queue = self.client.get('/moderation/quiz-reports')
        self.assertEqual(queue.status_code, 200)
        self.assertIn(b'Community Quiz', queue.data)
        moderated = self.client.post(
            '/moderation/quiz-reports',
            data={
                'report_id': report_id, 'action': 'unpublish',
                'resolution_note': 'Removed pending correction.',
            },
            headers=self.csrf(),
        )
        self.assertEqual(moderated.status_code, 302)
        with self.app.app_context():
            quiz = db.session.get(Quiz, quiz_id)
            self.assertFalse(quiz.is_public)
            self.assertTrue(quiz.is_suspended)
            self.assertIsNone(db.session.get(QuizShareLink, 'moderated-quiz-link'))
            report = db.session.get(QuizReport, report_id)
            self.assertEqual(report.status, 'resolved')
            self.assertEqual(report.resolved_by, moderator_id)
        self.assertEqual(self.client.get('/sq/moderated-quiz-link').status_code, 302)

        self._switch_user(owner_id)
        self.assert_json_error(self.client.post(
            '/edit_custom_quiz',
            json={
                'quiz_id': quiz_id, 'title': 'Community Quiz',
                'is_public': True,
            },
            headers=self.csrf(),
        ), 403)
        self.assert_json_error(self.client.post(
            '/quizzes/share',
            json={'quiz_id': quiz_id, 'permission': 'view'},
            headers=self.csrf(),
        ), 403)

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
        editor = self.client.get(f'/edit?deck_id={deck_id}')
        self.assertIn(b'Only the owner can change it.', editor.data)
        forbidden_publish = self.client.post(
            '/edit_deck',
            data={
                'deck_id': deck_id, 'description': 'Private Collaboration Deck',
                'is_public': 'yes',
            },
            headers={**self.csrf(), 'Accept': 'application/json'},
        )
        self.assert_json_error(forbidden_publish, 403)
        with self.app.app_context():
            self.assertFalse(db.session.get(Deck, deck_id).is_public)
        response = self.client.post(
            '/add_card', data={'deck_id': deck_id, 'question': 'Coauthor question', 'answers': 'Answer'}, headers=self.csrf()
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('#deck-editor', response.headers['Location'])
        with self.app.app_context():
            self.assertIsNotNone(Card.query.filter_by(
                deck_id=deck_id, question='Coauthor question',
            ).one_or_none())

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
