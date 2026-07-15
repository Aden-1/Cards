"""Production-oriented access performance regression coverage."""

# The shared fixture intentionally exports the production test dependency surface.
# ruff: noqa: F403, F405
from tests.production_support import *


class AccessPerformanceTests(ProductionTestCase):
    def test_learn_pages_list_only_owned_decks_but_allow_direct_public_links(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user(
                "learn_owner", "password12345", email="learn-owner@example.test"
            )
            other = cards_app.create_user(
                "learn_other", "password12345", email="learn-other@example.test"
            )
            cards_app.create_deck(owner.user_id, "Owned Learn Deck", sortable=True)
            public = cards_app.create_deck(
                other.user_id, "Public Direct Deck", sortable=True, is_public=True
            )
            private = cards_app.create_deck(other.user_id, "Foreign Private Deck", sortable=True)
            public_card = Card(
                deck_id=public.deck_id, question="Public direct question?", position=1
            )
            db.session.add(public_card)
            db.session.flush()
            db.session.add(CardAnswer(card_id=public_card.card_id, answer="Public answer"))
            db.session.commit()
            owner_id = owner.user_id
            public_id = public.deck_id
            private_id = private.deck_id

        self._login_session(owner_id)
        for path in ("/view", "/match", "/reorder", "/master", "/quiz"):
            page = self.client.get(path).get_data(as_text=True)
            self.assertIn("Owned Learn Deck", page, path)
            self.assertNotIn("Public Direct Deck", page, path)
            self.assertNotIn("Foreign Private Deck", page, path)

        direct_public_page = self.client.get(f"/view?deck_id={public_id}").get_data(as_text=True)
        blocked_private_page = self.client.get(f"/view?deck_id={private_id}").get_data(as_text=True)
        legacy_public_detail = self.client.get(
            f"/public_deck?deck_id={public_id}", follow_redirects=False
        )
        public_detail_page = self.client.get(legacy_public_detail.headers["Location"]).get_data(
            as_text=True
        )

        self.assertIn("Public Direct Deck", direct_public_page)
        self.assertNotIn("Foreign Private Deck", blocked_private_page)
        self.assertEqual(legacy_public_detail.status_code, 301)
        self.assertIn("Public Direct Deck", public_detail_page)

        with self.client.session_transaction() as current_session:
            current_session.clear()
        guest_learn_page = self.client.get("/view").get_data(as_text=True)
        self.assertNotIn("Public Direct Deck", guest_learn_page)

    def test_deck_page_is_capped_stable_and_uses_a_constant_query_count(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user(
                "paged_owner", "password12345", email="paged-owner@example.test"
            )
            db.session.add_all(
                [
                    Deck(owned_by=owner.user_id, description=f"Deck {index:03d}")
                    for index in range(61)
                ]
            )
            db.session.commit()
            owner_id = owner.user_id
            statements = []

            def record_statement(*args):
                statements.append(args[2].strip().upper())

            event.listen(db.engine, "before_cursor_execute", record_statement)
            try:
                page = cards_app.get_user_decks_page(owner_id, page=2, per_page=500)
            finally:
                event.remove(db.engine, "before_cursor_execute", record_statement)

            self.assertEqual(page["per_page"], 50)
            self.assertEqual(
                [deck.description for deck in page["items"]],
                [f"Deck {index:03d}" for index in range(50, 61)],
            )
            self.assertTrue(page["has_prev"])
            self.assertFalse(page["has_next"])
            self.assertLessEqual(len(statements), 2)
            self.assertIn("LIMIT", statements[0])

            self._login_session(owner_id)
            response_text = self.client.get("/edit?page=2&page_size=500").get_data(as_text=True)
            self.assertIn("Deck 050", response_text)
            self.assertNotIn("Deck 000", response_text)
            self.assertIn('aria-label="Collection pages"', response_text)
            self.assertIn("Previous page", response_text)

    def test_homepage_uses_bounded_feature_queries_and_normalized_tag_aggregate(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user(
                "homepage_owner", "password12345", email="homepage-owner@example.test"
            )
            decks = [
                Deck(
                    owned_by=owner.user_id,
                    description=f"Featured {index:03d}",
                    is_public=True,
                    is_featured=True,
                )
                for index in range(80)
            ]
            db.session.add_all(decks)
            db.session.flush()
            db.session.add_all(
                [
                    DeckTag(deck_id=deck.deck_id, tag_normalized="science", tag_display="Science")
                    for deck in decks
                ]
                + [DeckTag(deck_id=decks[0].deck_id, tag_normalized="math", tag_display="Math")]
            )
            db.session.commit()
            statements = []

            def record_statement(*args):
                statements.append(args[2].strip().upper())

            event.listen(db.engine, "before_cursor_execute", record_statement)
            try:
                homepage = cards_app.get_homepage_public_data(featured_limit=3, tag_limit=5)
            finally:
                event.remove(db.engine, "before_cursor_execute", record_statement)

            self.assertEqual(len(homepage["featured_decks"]), 3)
            self.assertEqual(homepage["featured_tags"][0], {"tag": "Science", "count": 80})
            self.assertLessEqual(len(statements), 5)
            self.assertTrue(
                any("DECK_TAG" in statement and "GROUP BY" in statement for statement in statements)
            )
            self.assertTrue(any("LIMIT" in statement for statement in statements))

    def test_fallback_search_enforces_page_limit_and_navigation(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user(
                "fallback_owner", "password12345", email="fallback-owner@example.test"
            )
            db.session.add_all(
                [
                    Deck(
                        owned_by=owner.user_id,
                        description=f"Fallback topic {index:03d}",
                        is_public=True,
                    )
                    for index in range(60)
                ]
            )
            db.session.commit()
            db.session.execute(text("DROP TABLE IF EXISTS public_content_fts"))
            db.session.commit()
            statements = []

            def record_statement(*args):
                statements.append(args[2].strip().upper())

            event.listen(db.engine, "before_cursor_execute", record_statement)
            try:
                with mock.patch.object(cards_app.app.logger, "exception"):
                    first_page = cards_app.search_public_content(
                        "Fallback topic", limit=500, page=1
                    )
                    second_page = cards_app.search_public_content(
                        "Fallback topic", limit=500, page=2
                    )
            finally:
                event.remove(db.engine, "before_cursor_execute", record_statement)

            self.assertEqual(len(first_page["decks"]), 50)
            self.assertEqual(first_page["pagination"]["per_page"], 50)
            self.assertTrue(first_page["pagination"]["has_next"])
            self.assertEqual(len(second_page["decks"]), 10)
            self.assertTrue(second_page["pagination"]["has_prev"])
            self.assertFalse(second_page["pagination"]["has_next"])
            self.assertEqual(
                [deck["description"] for deck in first_page["decks"][:2]],
                ["Fallback topic 000", "Fallback topic 001"],
            )
            self.assertLessEqual(len(statements), 9)

    def test_zero_result_search_does_not_rebuild_or_write(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user("search_miss_owner", "password12345")
            cards_app.create_deck(
                owner.user_id,
                "Indexed Search Deck",
                is_public=True,
                tags="indexed",
            )
            indexed_before = db.session.execute(
                text("SELECT COUNT(*) FROM public_content_fts")
            ).scalar_one()

            rebuild_calls = []
            original_rebuild = cards_app._rebuild_content_fts_index
            statements = []

            def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
                statements.append(statement.strip().upper())

            cards_app._rebuild_content_fts_index = lambda: rebuild_calls.append(True)
            event.listen(db.engine, "before_cursor_execute", record_statement)
            try:
                results = cards_app.search_public_content("zzzzzzzznomatch")
            finally:
                event.remove(db.engine, "before_cursor_execute", record_statement)
                cards_app._rebuild_content_fts_index = original_rebuild

            indexed_after = db.session.execute(
                text("SELECT COUNT(*) FROM public_content_fts")
            ).scalar_one()

        self.assertEqual(results["decks"], [])
        self.assertEqual(results["quizzes"], [])
        self.assertEqual(rebuild_calls, [])
        self.assertEqual(indexed_after, indexed_before)
        write_prefixes = ("INSERT ", "UPDATE ", "DELETE ", "CREATE ", "ALTER ", "DROP ")
        self.assertFalse(any(statement.startswith(write_prefixes) for statement in statements))

    def test_deck_summaries_use_one_query_without_lazy_card_loads(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user("deck_query_owner", "password12345")
            for deck_index in range(10):
                deck = Deck(owned_by=owner.user_id, description=f"Deck {deck_index}")
                db.session.add(deck)
                db.session.flush()
                card = Card(deck_id=deck.deck_id, question="Question?", position=1)
                db.session.add(card)
                db.session.flush()
                db.session.add(CardAnswer(card_id=card.card_id, answer="Answer"))
            db.session.commit()
            owner_id = owner.user_id
            db.session.remove()

            statements = []

            def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
                statements.append(statement.strip().upper())

            event.listen(db.engine, "before_cursor_execute", record_statement)
            try:
                decks = cards_app.get_user_decks(owner_id)
                summaries = [routes._deck_summary_payload(deck, owner_id) for deck in decks]
            finally:
                event.remove(db.engine, "before_cursor_execute", record_statement)

        select_statements = [
            statement for statement in statements if statement.startswith("SELECT ")
        ]
        self.assertEqual(len(summaries), 10)
        self.assertTrue(all(summary["card_count"] == 1 for summary in summaries))
        self.assertEqual(len(select_statements), 1)

    def test_deck_content_query_count_is_constant(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user("deck_content_owner", "password12345")
            deck = Deck(owned_by=owner.user_id, description="Large Deck")
            db.session.add(deck)
            db.session.flush()
            for index in range(25):
                card = Card(
                    deck_id=deck.deck_id,
                    question=f"Question {index}?",
                    position=index + 1,
                )
                db.session.add(card)
                db.session.flush()
                db.session.add(CardAnswer(card_id=card.card_id, answer=f"Answer {index}"))
            db.session.commit()
            deck_id = deck.deck_id
            db.session.remove()

            statements = []

            def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
                statements.append(statement.strip().upper())

            event.listen(db.engine, "before_cursor_execute", record_statement)
            try:
                payload = cards_app.get_deck_details(deck_id)
            finally:
                event.remove(db.engine, "before_cursor_execute", record_statement)

        select_statements = [
            statement for statement in statements if statement.startswith("SELECT ")
        ]
        self.assertEqual(len(payload["cards"]), 25)
        self.assertEqual(len(select_statements), 3)

    def test_quiz_counts_and_content_have_constant_query_budgets(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user("quiz_query_owner", "password12345")
            selected_quiz_id = None
            for quiz_index in range(10):
                quiz = Quiz(owned_by=owner.user_id, title=f"Quiz {quiz_index}")
                db.session.add(quiz)
                db.session.flush()
                question = QuizQuestion(
                    quiz_id=quiz.quiz_id,
                    question=f"Question {quiz_index}?",
                    type="static",
                )
                db.session.add(question)
                db.session.flush()
                db.session.add(
                    QuizOption(
                        question_id=question.question_id,
                        text="Answer",
                        is_correct=True,
                    )
                )
                selected_quiz_id = quiz.quiz_id
            db.session.commit()
            owner_id = owner.user_id
            db.session.remove()

            count_statements = []

            def record_count_statement(
                _conn, _cursor, statement, _parameters, _context, _executemany
            ):
                count_statements.append(statement.strip().upper())

            event.listen(db.engine, "before_cursor_execute", record_count_statement)
            try:
                quizzes = cards_app.get_user_custom_quizzes(owner_id)
                question_counts = [quiz.question_count for quiz in quizzes]
            finally:
                event.remove(db.engine, "before_cursor_execute", record_count_statement)

            db.session.remove()
            content_statements = []

            def record_content_statement(
                _conn, _cursor, statement, _parameters, _context, _executemany
            ):
                content_statements.append(statement.strip().upper())

            event.listen(db.engine, "before_cursor_execute", record_content_statement)
            try:
                selected_quiz = cards_app.get_quiz_with_content(selected_quiz_id)
                option_text = selected_quiz.questions[0].options[0].text
            finally:
                event.remove(db.engine, "before_cursor_execute", record_content_statement)

        count_selects = [
            statement for statement in count_statements if statement.startswith("SELECT ")
        ]
        content_selects = [
            statement for statement in content_statements if statement.startswith("SELECT ")
        ]
        self.assertEqual(question_counts, [1] * 10)
        self.assertEqual(option_text, "Answer")
        self.assertEqual(len(count_selects), 1)
        self.assertEqual(len(content_selects), 3)
