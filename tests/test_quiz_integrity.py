"""Production-oriented quiz integrity regression coverage."""

# The shared fixture intentionally exports the production test dependency surface.
# ruff: noqa: F403, F405
from tests.production_support import *


class QuizIntegrityTests(ProductionTestCase):
    def test_quiz_scoring_ignores_client_claimed_correctness(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user(
                "quiz_owner", "password12345", email="quiz-owner@example.test"
            )
            deck = cards_app.create_deck(owner.user_id, "Public quiz deck", is_public=True)
            card = Card(deck_id=deck.deck_id, question="Capital of France?", position=1)
            db.session.add(card)
            db.session.flush()
            db.session.add(CardAnswer(card_id=card.card_id, answer="Paris"))
            db.session.commit()
            deck_id = deck.deck_id
            card_id = card.card_id

        page = self.client.get(f"/quiz?deck_id={deck_id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Start Quiz", page.get_data(as_text=True))
        with cards_app.app.app_context():
            self.assertEqual(QuizAttempt.query.count(), 0)

        page = self._start_quiz(f"deck:{deck_id}")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn('"is_correct":', page.get_data(as_text=True))
        with self.client.session_transaction() as current_session:
            attempt_token = current_session["quiz_attempt_tokens"][-1]
            current_session["csrf_token"] = "csrf-test-token"

        response = self.client.post(
            "/score_quiz",
            json={
                "attempt_token": attempt_token,
                "answers": {str(card_id): ["Forged"]},
                "quiz_data": [{"id": card_id, "options": [{"text": "Forged", "is_correct": True}]}],
            },
            headers={"X-CSRFToken": "csrf-test-token"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["score"], 0)
        with cards_app.app.app_context():
            self.assertIsNone(db.session.get(QuizAttempt, attempt_token))

    def test_match_progress_ignores_client_claimed_correctness(self):
        with cards_app.app.app_context():
            user = cards_app.create_user("matcher", "password12345", email="matcher@example.test")
            deck = Deck(owned_by=user.user_id, description="Matching")
            db.session.add(deck)
            db.session.flush()
            first = Card(deck_id=deck.deck_id, question="First?", position=1)
            second = Card(deck_id=deck.deck_id, question="Second?", position=2)
            db.session.add_all([first, second])
            db.session.flush()
            answer = CardAnswer(card_id=first.card_id, answer="First")
            db.session.add(answer)
            db.session.commit()
            user_id = user.user_id
            answer_id = answer.answer_id
            wrong_question_id = second.card_id

        self._login_session(user_id)
        response = self.client.post(
            "/match_attempt",
            json={
                "answer_id": answer_id,
                "selected_question_id": wrong_question_id,
                "is_correct": True,
            },
            headers={"X-CSRFToken": "csrf-test-token"},
        )

        self.assertEqual(response.status_code, 200)
        with cards_app.app.app_context():
            progress = MatchPairProgress.query.filter_by(user_id=user_id, answer_id=answer_id).one()
            self.assertEqual(progress.correct_count, 0)
            self.assertEqual(progress.incorrect_count, 1)

    def test_deck_quiz_page_renders_answer_options(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user(
                "deck_quiz_owner", "password12345", email="deck-quiz-owner@example.test"
            )
            deck = cards_app.create_deck(owner.user_id, "Deck Quiz", is_public=True)
            card = Card(deck_id=deck.deck_id, question="Largest ocean?", position=1)
            db.session.add(card)
            db.session.flush()
            db.session.add_all(
                [
                    CardAnswer(card_id=card.card_id, answer="Pacific Ocean"),
                    CardAnswer(card_id=card.card_id, answer="The Pacific"),
                ]
            )
            db.session.commit()
            deck_id = deck.deck_id

        response = self._start_quiz(f"deck:{deck_id}")
        page = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Largest ocean?", page)
        self.assertIn("submitQuiz", page)
        self.assertTrue("Pacific Ocean" in page or "The Pacific" in page)

    def test_custom_quiz_page_renders_dynamic_question_options(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user(
                "custom_quiz_owner", "password12345", email="custom-quiz-owner@example.test"
            )
            quiz = Quiz(owned_by=owner.user_id, title="World Capitals", is_public=True)
            db.session.add(quiz)
            db.session.flush()

            dynamic_question = QuizQuestion(
                quiz_id=quiz.quiz_id, question="Capital of Japan?", type="dynamic"
            )
            other_question = QuizQuestion(
                quiz_id=quiz.quiz_id, question="Capital of Italy?", type="dynamic"
            )
            db.session.add_all([dynamic_question, other_question])
            db.session.flush()

            db.session.add_all(
                [
                    QuizOption(
                        question_id=dynamic_question.question_id, text="Tokyo", is_correct=True
                    ),
                    QuizOption(
                        question_id=dynamic_question.question_id, text="Tokio", is_correct=True
                    ),
                    QuizOption(
                        question_id=other_question.question_id, text="Rome", is_correct=True
                    ),
                    QuizOption(
                        question_id=other_question.question_id, text="Milan", is_correct=True
                    ),
                    QuizOption(
                        question_id=other_question.question_id, text="Naples", is_correct=True
                    ),
                ]
            )
            db.session.commit()
            quiz_id = quiz.quiz_id

        response = self._start_quiz(f"custom:{quiz_id}")
        page = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Capital of Japan?", page)
        self.assertIn("submitQuiz", page)
        self.assertIn("Tokyo", page)

    def test_quiz_attempts_are_capped_and_displaced_rows_are_deleted(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user("attempt_cap_owner", "password12345")
            deck = cards_app.create_deck(owner.user_id, "Attempt Cap Deck", is_public=True)
            card = Card(deck_id=deck.deck_id, question="Bounded?", position=1)
            db.session.add(card)
            db.session.flush()
            db.session.add(CardAnswer(card_id=card.card_id, answer="Yes"))
            db.session.commit()
            deck_id = deck.deck_id

        previous_limit = cards_app.app.config["MAX_ACTIVE_QUIZ_ATTEMPTS"]
        cards_app.app.config["MAX_ACTIVE_QUIZ_ATTEMPTS"] = 3
        try:
            for _ in range(5):
                self.assertEqual(self._start_quiz(f"deck:{deck_id}").status_code, 200)

            with self.client.session_transaction() as current_session:
                active_tokens = list(current_session["quiz_attempt_tokens"])
                quiz_session_id = current_session["quiz_session_id"]
            self.assertEqual(len(active_tokens), 3)

            with cards_app.app.app_context():
                attempts = QuizAttempt.query.filter_by(session_id=quiz_session_id).all()
                self.assertEqual(len(attempts), 3)
                self.assertEqual(
                    {attempt.attempt_token for attempt in attempts},
                    set(active_tokens),
                )
        finally:
            cards_app.app.config["MAX_ACTIVE_QUIZ_ATTEMPTS"] = previous_limit

    def test_quiz_attempt_question_count_is_bounded(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user("question_cap_owner", "password12345")
            deck = cards_app.create_deck(owner.user_id, "Question Cap Deck", is_public=True)
            for index in range(5):
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

        previous_limit = cards_app.app.config["MAX_QUIZ_QUESTIONS"]
        cards_app.app.config["MAX_QUIZ_QUESTIONS"] = 2
        try:
            response = self._start_quiz(f"deck:{deck_id}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.get_data(as_text=True).count('class="quiz-question-shell'),
                2,
            )
            with self.client.session_transaction() as current_session:
                attempt_token = current_session["quiz_attempt_tokens"][-1]
            with cards_app.app.app_context():
                self.assertEqual(db.session.get(QuizAttempt, attempt_token).question_count, 2)
        finally:
            cards_app.app.config["MAX_QUIZ_QUESTIONS"] = previous_limit

    def test_expired_quiz_attempt_is_rejected_and_deleted(self):
        with cards_app.app.app_context():
            owner = cards_app.create_user("expired_attempt_owner", "password12345")
            deck = cards_app.create_deck(owner.user_id, "Expired Attempt Deck", is_public=True)
            card = Card(deck_id=deck.deck_id, question="Still valid?", position=1)
            db.session.add(card)
            db.session.flush()
            db.session.add(CardAnswer(card_id=card.card_id, answer="No"))
            db.session.commit()
            deck_id = deck.deck_id

        self.assertEqual(self._start_quiz(f"deck:{deck_id}").status_code, 200)
        with self.client.session_transaction() as current_session:
            attempt_token = current_session["quiz_attempt_tokens"][-1]

        with cards_app.app.app_context():
            attempt = db.session.get(QuizAttempt, attempt_token)
            attempt.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
                seconds=cards_app.app.config["QUIZ_ATTEMPT_MAX_AGE_SECONDS"] + 1
            )
            db.session.commit()

        response = self.client.post(
            "/score_quiz",
            json={"attempt_token": attempt_token, "answers": {}},
            headers={"X-CSRFToken": "csrf-test-token"},
        )
        self.assertEqual(response.status_code, 400)
        with cards_app.app.app_context():
            self.assertIsNone(db.session.get(QuizAttempt, attempt_token))
