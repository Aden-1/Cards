import random
import re
import unicodedata
import os

from flask import Flask
from flask_migrate import Migrate
from sqlalchemy import text
from models import db, User, Deck, Card, CardAnswer, Quiz, QuizQuestion, QuizOption
from routes import register_routes

app = Flask(__name__, instance_relative_config=True)

# Session and cookie security. Set SECRET_KEY in production.
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-only-change-me')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', '').lower() in ('1', 'true', 'yes')

# Local SQLite config.
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///cards.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
migrate = Migrate(app, db)


# Normalize answer input into a clean list.
def _normalize_answers(answers):
    if answers is None:
        return []
    if isinstance(answers, str):
        answers = [part.strip() for part in answers.split(',')]
    return [answer.strip() for answer in answers if str(answer).strip()]


# Serialize one card for JSON responses.
def _serialize_card(card, detailed=False):
    answer_objects = [{'answer_id': answer.answer_id, 'answer': answer.answer} for answer in card.answers]
    payload = {
        'card_id': card.card_id,
        'question': card.question,
        'answers': [answer['answer'] for answer in answer_objects],
        'position': card.position,
    }
    if detailed:
        payload['answer_objects'] = answer_objects
    return payload


# Serialize a deck and its cards.
def _serialize_deck(deck, detailed_cards=False, shuffle_cards=False, shuffle_answers=False):
    cards = list(deck.cards)
    cards.sort(key=lambda card: card.position)
    if shuffle_cards:
        random.shuffle(cards)

    serialized_cards = []
    flattened_answers = []
    for card in cards:
        serialized_card = _serialize_card(card, detailed=detailed_cards or shuffle_answers)
        if shuffle_answers:
            serialized_card['answer_objects'] = [
                {'answer_id': answer.answer_id, 'answer': answer.answer}
                for answer in card.answers
            ]
        serialized_cards.append(serialized_card)
        for answer in card.answers:
            flattened_answers.append({
                'answer_id': answer.answer_id,
                'answer': answer.answer,
                'card_id': card.card_id,
                'question': card.question,
            })

    if shuffle_answers:
        random.shuffle(flattened_answers)

    return {
        'deck_id': deck.deck_id,
        'description': deck.description,
        'detailed_description': deck.detailed_description,
        'tags': deck.tags,
        'sortable': deck.sortable,
        'is_public': deck.is_public,
        'card_count': len(cards),
        'answer_count': len(flattened_answers),
        'cards': serialized_cards,
        'answers': flattened_answers,
    }


# Custom quiz helpers.

# Return public and owned custom quizzes.
def get_accessible_custom_quizzes(user_id=None):
    if user_id is None:
        return Quiz.query.filter(Quiz.is_public == True).all()
    return Quiz.query.filter((Quiz.owned_by == user_id) | (Quiz.is_public == True)).all()

# Return quizzes owned by one user.
def get_user_custom_quizzes(user_id):
    return Quiz.query.filter_by(owned_by=user_id).all()

# Create and index a custom quiz.
def create_custom_quiz(user_id, title, is_public=False, description=None, tags=None):
    quiz = Quiz(
        owned_by=user_id,
        title=title,
        is_public=is_public,
        description=description,
        tags=tags,
    )
    db.session.add(quiz)
    db.session.commit()
    _sync_content_fts_index_for_quiz(quiz)
    return quiz

# Update a custom quiz and refresh search.
def edit_custom_quiz(quiz_id, title, is_public=False, description=None, tags=None):
    quiz = Quiz.query.get(quiz_id)
    if quiz:
        quiz.title = title
        quiz.is_public = is_public
        quiz.description = description
        quiz.tags = tags
        db.session.commit()
        _sync_content_fts_index_for_quiz(quiz)
        return quiz
    return None

# Delete a custom quiz and its index row.
def delete_custom_quiz(quiz_id):
    quiz = Quiz.query.get(quiz_id)
    if quiz:
        _delete_content_fts_index_row('quiz', quiz.quiz_id)
        db.session.delete(quiz)
        db.session.commit()
        return True
    return False


# Duplicate a public quiz into one user's account.
def copy_public_quiz_to_user(source_quiz_id, user_id):
    source_quiz = Quiz.query.get(source_quiz_id)
    if not source_quiz or not source_quiz.is_public:
        return None

    copied_quiz = Quiz(
        owned_by=user_id,
        title=f"{source_quiz.title} (Copy)",
        description=source_quiz.description,
        tags=source_quiz.tags,
        is_public=False,
    )
    db.session.add(copied_quiz)
    db.session.flush()

    for source_question in source_quiz.questions:
        copied_question = QuizQuestion(
            quiz_id=copied_quiz.quiz_id,
            question=source_question.question,
            type=source_question.type,
        )
        db.session.add(copied_question)
        db.session.flush()

        for source_option in source_question.options:
            copied_option = QuizOption(
                question_id=copied_question.question_id,
                text=source_option.text,
                is_correct=source_option.is_correct,
            )
            db.session.add(copied_option)

    db.session.commit()
    return copied_quiz


# Duplicate a public deck into one user's account.
def copy_public_deck_to_user(source_deck_id, user_id):
    source_deck = Deck.query.get(source_deck_id)
    if not source_deck or not source_deck.is_public:
        return None

    copied_deck = Deck(
        owned_by=user_id,
        description=f"{source_deck.description} (Copy)",
        detailed_description=source_deck.detailed_description,
        tags=source_deck.tags,
        sortable=source_deck.sortable,
        is_public=False,
    )
    db.session.add(copied_deck)
    db.session.flush()

    ordered_cards = sorted(list(source_deck.cards), key=lambda c: c.position)
    for source_card in ordered_cards:
        copied_card = Card(
            deck_id=copied_deck.deck_id,
            question=source_card.question,
            position=source_card.position,
        )
        db.session.add(copied_card)
        db.session.flush()

        for source_answer in source_card.answers:
            copied_answer = CardAnswer(
                card_id=copied_card.card_id,
                answer=source_answer.answer,
            )
            db.session.add(copied_answer)

    db.session.commit()
    _sync_content_fts_index_for_deck(copied_deck)
    return copied_deck

# Insert a quiz question and its options.
def add_quiz_question(quiz_id, question_text, q_type, options_data):
    q = QuizQuestion(quiz_id=quiz_id, question=question_text, type=q_type)
    db.session.add(q)
    db.session.flush()
    
    for opt in options_data:
        if opt['text'].strip():
            qo = QuizOption(question_id=q.question_id, text=opt['text'].strip(), is_correct=opt.get('is_correct', False))
            db.session.add(qo)
    db.session.commit()
    return q

# Delete a quiz question and child options.
def delete_quiz_question(question_id):
    q = QuizQuestion.query.get(question_id)
    if q:
        db.session.delete(q)
        db.session.commit()
        return True
    return False


# User helpers.
# Create a user record.
def create_user(username, password, email=None, role='standard'):
    user = User(username=username, email=email or None, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user

# Look up a user by username.
def get_user(username):
    return User.query.filter_by(username=username).first()


def get_user_by_id(user_id):
    return User.query.get(user_id)


def get_user_by_email(email):
    if not email:
        return None
    return User.query.filter_by(email=email).first()


def update_user_account(user_id, username, email=None, password=None):
    user = User.query.get(user_id)
    if not user:
        return None
    user.username = username
    user.email = email or None
    if password:
        user.set_password(password)
    db.session.commit()
    return user


# Deck helpers.
# Create a deck and mirror it into search.
def create_deck(user_id, description, sortable=False, is_public=False, detailed_description=None, tags=None):
    deck = Deck(
        owned_by=user_id,
        description=description,
        sortable=sortable,
        is_public=is_public,
        detailed_description=detailed_description,
        tags=tags
    )
    db.session.add(deck)
    db.session.commit()
    _sync_content_fts_index_for_deck(deck)
    return deck

# Return decks owned by one user.
def get_user_decks(user_id):
    return Deck.query.filter_by(owned_by=user_id).all()


# Owned or public decks.
# Return owned and public decks.
def get_accessible_decks(user_id=None):
    if user_id is None:
        return Deck.query.filter(Deck.is_public == True).all()
    return Deck.query.filter((Deck.owned_by == user_id) | (Deck.is_public == True)).all()


# Fetch a deck by id.
def get_deck(deck_id):
    return Deck.query.get(deck_id)


# Delete a deck and its search row.
def delete_deck(deck_id):
    deck = Deck.query.get(deck_id)
    if deck:
        _delete_content_fts_index_row('deck', deck.deck_id)
        db.session.delete(deck)
        db.session.commit()
        return True
    return False

# Update deck metadata and search.
def edit_deck(deck_id, description, sortable=False, is_public=False, detailed_description=None, tags=None):
    deck = Deck.query.get(deck_id)
    if deck:
        deck.description = description
        deck.sortable = sortable
        deck.is_public = is_public
        deck.detailed_description = detailed_description
        deck.tags = tags
        db.session.commit()
        _sync_content_fts_index_for_deck(deck)
        return deck
    return None

# Simple fallback search for public decks.
def search_public_decks(query_text):
    if not query_text:
        return []

    # Plain-text fallback search.
    search_term = f"%{query_text}%"
    decks = Deck.query.filter(
        Deck.is_public == True,
        db.or_(
            Deck.description.ilike(search_term),
            Deck.detailed_description.ilike(search_term),
            Deck.tags.ilike(search_term)
        )
    ).all()
    return decks


# Simple fallback search for public quizzes.
def search_public_quizzes(query_text):
    if not query_text:
        return []

    search_term = f"%{query_text}%"
    quizzes = Quiz.query.filter(
        Quiz.is_public == True,
        db.or_(
            Quiz.title.ilike(search_term),
            Quiz.description.ilike(search_term),
            Quiz.tags.ilike(search_term)
        )
    ).all()
    return quizzes


# Normalize text for search matching.
def _normalize_search_text(text):
    text = (text or '').lower()
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# Split normalized search text into tokens.
def _tokenize_search_text(text):
    normalized = _normalize_search_text(text)
    if not normalized:
        return []
    return [token for token in normalized.split() if token]


# Build a loose FTS query from user input.
def _build_fts_query(query_text):
    tokens = _tokenize_search_text(query_text)
    if not tokens:
        return '', []

    # Use OR plus prefix matching for broader recall.
    parts = []
    for token in tokens:
        safe_token = token.replace('"', '""')
        parts.append(f'"{safe_token}"')
        if len(safe_token) >= 3:
            parts.append(f'"{safe_token}"*')
    return ' OR '.join(parts), tokens


# Public search index helpers.
# Create the public content FTS table if needed.
def _ensure_content_fts_index():
    db.session.execute(text("""
        CREATE VIRTUAL TABLE IF NOT EXISTS public_content_fts USING fts5(
            item_type UNINDEXED,
            item_id UNINDEXED,
            title,
            description,
            tags,
            tokenize = 'porter unicode61 remove_diacritics 2'
        )
    """))
    db.session.commit()


# Remove one row from the public search index.
def _delete_content_fts_index_row(item_type, item_id):
    try:
        _ensure_content_fts_index()
        db.session.execute(
            text("DELETE FROM public_content_fts WHERE item_type = :item_type AND item_id = :item_id"),
            {'item_type': item_type, 'item_id': str(item_id)}
        )
        db.session.commit()
    except Exception:
        db.session.rollback()


# Write one row into the public search index.
def _sync_content_fts_index_row(item_type, item_id, title, description, tags, is_public):
    try:
        _ensure_content_fts_index()
        db.session.execute(
            text("DELETE FROM public_content_fts WHERE item_type = :item_type AND item_id = :item_id"),
            {'item_type': item_type, 'item_id': str(item_id)}
        )
        if is_public:
            db.session.execute(
                text("""
                    INSERT INTO public_content_fts (item_type, item_id, title, description, tags)
                    VALUES (:item_type, :item_id, :title, :description, :tags)
                """),
                {
                    'item_type': item_type,
                    'item_id': str(item_id),
                    'title': title or '',
                    'description': description or '',
                    'tags': tags or '',
                }
            )
        db.session.commit()
    except Exception:
        db.session.rollback()


# Sync one deck into the search index.
def _sync_content_fts_index_for_deck(deck):
    _sync_content_fts_index_row(
        item_type='deck',
        item_id=deck.deck_id,
        title=deck.description,
        description=deck.detailed_description,
        tags=deck.tags,
        is_public=deck.is_public,
    )


# Sync one quiz into the search index.
def _sync_content_fts_index_for_quiz(quiz):
    _sync_content_fts_index_row(
        item_type='quiz',
        item_id=quiz.quiz_id,
        title=quiz.title,
        description=quiz.description,
        tags=quiz.tags,
        is_public=quiz.is_public,
    )


# Rebuild the full public search index.
def _rebuild_content_fts_index():
    _ensure_content_fts_index()
    db.session.execute(text("DELETE FROM public_content_fts"))

    for deck in Deck.query.filter(Deck.is_public == True).all():
        db.session.execute(
            text("""
                INSERT INTO public_content_fts (item_type, item_id, title, description, tags)
                VALUES (:item_type, :item_id, :title, :description, :tags)
            """),
            {
                'item_type': 'deck',
                'item_id': str(deck.deck_id),
                'title': deck.description or '',
                'description': deck.detailed_description or '',
                'tags': deck.tags or '',
            }
        )

    for quiz in Quiz.query.filter(Quiz.is_public == True).all():
        db.session.execute(
            text("""
                INSERT INTO public_content_fts (item_type, item_id, title, description, tags)
                VALUES (:item_type, :item_id, :title, :description, :tags)
            """),
            {
                'item_type': 'quiz',
                'item_id': str(quiz.quiz_id),
                'title': quiz.title or '',
                'description': quiz.description or '',
                'tags': quiz.tags or '',
            }
        )

    db.session.commit()


# Fallback search when FTS is unavailable.
def _fallback_search_public_content(query_text):
    search_term = f"%{query_text}%"
    decks = Deck.query.filter(
        Deck.is_public == True,
        db.or_(
            Deck.description.ilike(search_term),
            Deck.detailed_description.ilike(search_term),
            Deck.tags.ilike(search_term)
        )
    ).all()
    quizzes = Quiz.query.filter(
        Quiz.is_public == True,
        db.or_(
            Quiz.title.ilike(search_term),
            Quiz.description.ilike(search_term),
            Quiz.tags.ilike(search_term)
        )
    ).all()
    return decks, quizzes


# Search public decks and quizzes.
def search_public_content(query_text, limit=50, user_id=None):
    query_text = (query_text or '').strip()
    if not query_text:
        return {'decks': [], 'quizzes': [], 'has_exact_match': False, 'query_tokens': [], 'expanded_tokens': []}

    fts_query, query_tokens = _build_fts_query(query_text)
    if not fts_query:
        return {'decks': [], 'quizzes': [], 'has_exact_match': False, 'query_tokens': [], 'expanded_tokens': []}

    deck_results = []
    quiz_results = []
    has_exact_match = False

    try:
        _ensure_content_fts_index()
        results = db.session.execute(
            text("""
                SELECT
                    item_type,
                    item_id,
                    bm25(public_content_fts, 1.0, 0.7, 0.9) AS rank,
                    snippet(public_content_fts, 0, '[', ']', '...', 10) AS title_snippet,
                    snippet(public_content_fts, 1, '[', ']', '...', 12) AS description_snippet,
                    snippet(public_content_fts, 2, '[', ']', '...', 10) AS tags_snippet
                FROM public_content_fts
                WHERE public_content_fts MATCH :match_query
                ORDER BY rank
                LIMIT :limit
            """),
            {'match_query': fts_query, 'limit': int(limit)}
        ).fetchall()

        if not results:
            # Rebuild if the index is empty or stale.
            _rebuild_content_fts_index()
            results = db.session.execute(
                text("""
                    SELECT
                        item_type,
                        item_id,
                        bm25(public_content_fts, 1.0, 0.7, 0.9) AS rank,
                        snippet(public_content_fts, 0, '[', ']', '...', 10) AS title_snippet,
                        snippet(public_content_fts, 1, '[', ']', '...', 12) AS description_snippet,
                        snippet(public_content_fts, 2, '[', ']', '...', 10) AS tags_snippet
                    FROM public_content_fts
                    WHERE public_content_fts MATCH :match_query
                    ORDER BY rank
                    LIMIT :limit
                """),
                {'match_query': fts_query, 'limit': int(limit)}
            ).fetchall()

        for row in results:
            item_type = row[0]
            item_id = int(row[1])
            rank_value = float(row[2]) if row[2] is not None else 0.0
            score = round(-rank_value, 4)
            reasons = []
            if row[3]:
                reasons.append(f"title: {row[3]}")
            if row[4]:
                reasons.append(f"description: {row[4]}")
            if row[5]:
                reasons.append(f"tags: {row[5]}")

            if item_type == 'deck':
                deck = Deck.query.get(item_id)
                if not deck or not deck.is_public:
                    continue
                if _normalize_search_text(query_text) in _normalize_search_text(deck.description or ''):
                    has_exact_match = True
                deck_results.append({
                    'deck_id': deck.deck_id,
                    'owned_by': deck.owned_by,
                    'is_owned': bool(user_id is not None and deck.owned_by == user_id),
                    'description': deck.description,
                    'detailed_description': deck.detailed_description,
                    'tags': deck.tags,
                    'sortable': deck.sortable,
                    'is_public': deck.is_public,
                    'card_count': len(deck.cards),
                    'score': score,
                    'match_reasons': reasons,
                })
            elif item_type == 'quiz':
                quiz = Quiz.query.get(item_id)
                if not quiz or not quiz.is_public:
                    continue
                if _normalize_search_text(query_text) in _normalize_search_text(quiz.title or ''):
                    has_exact_match = True
                quiz_results.append({
                    'quiz_id': quiz.quiz_id,
                    'owned_by': quiz.owned_by,
                    'is_owned': bool(user_id is not None and quiz.owned_by == user_id),
                    'title': quiz.title,
                    'description': quiz.description,
                    'tags': quiz.tags,
                    'is_public': quiz.is_public,
                    'question_count': len(quiz.questions),
                    'score': score,
                    'match_reasons': reasons,
                })

    except Exception:
        db.session.rollback()
        # Fall back to simple LIKE search if FTS fails.
        decks, quizzes = _fallback_search_public_content(query_text)
        deck_results = [{
            'deck_id': deck.deck_id,
            'owned_by': deck.owned_by,
            'is_owned': bool(user_id is not None and deck.owned_by == user_id),
            'description': deck.description,
            'detailed_description': deck.detailed_description,
            'tags': deck.tags,
            'sortable': deck.sortable,
            'is_public': deck.is_public,
            'card_count': len(deck.cards),
            'score': 0.0,
            'match_reasons': ['fallback match'],
        } for deck in decks]
        quiz_results = [{
            'quiz_id': quiz.quiz_id,
            'owned_by': quiz.owned_by,
            'is_owned': bool(user_id is not None and quiz.owned_by == user_id),
            'title': quiz.title,
            'description': quiz.description,
            'tags': quiz.tags,
            'is_public': quiz.is_public,
            'question_count': len(quiz.questions),
            'score': 0.0,
            'match_reasons': ['fallback match'],
        } for quiz in quizzes]
        has_exact_match = True if (deck_results or quiz_results) else False

    return {
        'decks': deck_results,
        'quizzes': quiz_results,
        'has_exact_match': has_exact_match,
        'query_tokens': query_tokens,
        'expanded_tokens': [],
    }

# Card and answer helpers.

# Add a card with one or more answers.
def add_card(deck_id, question, answers):
    # Positions are 1-based within each deck.
    max_position = db.session.query(db.func.max(Card.position)).filter_by(deck_id=deck_id).scalar() or 0
    next_position = max_position + 1

    card = Card(deck_id=deck_id, question=question, position=next_position)
    db.session.add(card)
    db.session.flush()
    
    answers = _normalize_answers(answers)
    if not answers:
        raise ValueError('At least one answer is required')

    for answer_text in answers:
        card_answer = CardAnswer(card_id=card.card_id, answer=answer_text)
        db.session.add(card_answer)

    db.session.commit()
    return card


# Add one answer to an existing card.
def add_answer_to_card(card_id, answer):
    card = Card.query.get(card_id)
    if card:
        card_answer = CardAnswer(card_id=card_id, answer=answer)
        db.session.add(card_answer)
        db.session.commit()
        return card_answer
    return None


# Delete an answer and maybe its card.
def delete_answer(answer_id):
    # Remove the card if its last answer disappears.
    answer = CardAnswer.query.get(answer_id)
    if not answer:
        return None

    card = answer.card
    deck_id = card.deck_id if card else None
    card_id = card.card_id if card else None

    db.session.delete(answer)
    db.session.flush()

    card_deleted = False
    remaining_answers = CardAnswer.query.filter_by(card_id=card_id).count() if card_id else 0
    if card and remaining_answers == 0:
        db.session.delete(card)
        card_deleted = True

    db.session.commit()
    return {'answer_deleted': True, 'card_deleted': card_deleted, 'card_id': card_id, 'deck_id': deck_id}


# Delete one card and its answers.
def delete_card(card_id):
    card = Card.query.get(card_id)
    if card:
        db.session.delete(card)
        db.session.commit()
        return True
    return False


# Replace a card question and answers.
def edit_card(card_id, question, answers):
    card = Card.query.get(card_id)
    if card:
        card.question = question
        answers = _normalize_answers(answers)
        if not answers:
            deck_id = card.deck_id
            db.session.delete(card)
            db.session.commit()
            return {'deleted': True, 'card_id': card_id, 'deck_id': deck_id}

        # Replace the answer set in one pass.
        CardAnswer.query.filter_by(card_id=card_id).delete()
        for answer_text in answers:
            card_answer = CardAnswer(card_id=card_id, answer=answer_text)
            db.session.add(card_answer)
        db.session.commit()
        return card
    return None


# Card fetch helper.
# Fetch one card in API-friendly form.
def get_card_from_deck(card_id, detailed=False):
    card = Card.query.get(card_id)
    if card:
        if detailed:
            return _serialize_card(card, detailed=True)
        return {
            'card_id': card.card_id,
            'question': card.question,
            'answers': [answer.answer for answer in card.answers],
            'deck_id': card.deck_id,
            'position': card.position,
        }
    return None


# Build deck data for study and games.
def get_deck_study_data(deck_id, shuffle=True):
    deck = Deck.query.get(deck_id)
    if not deck:
        return None

    return _serialize_deck(deck, detailed_cards=True, shuffle_cards=shuffle, shuffle_answers=shuffle)


# Compare submitted order against saved positions.
def check_deck_order(deck_id, ordered_card_ids):
    """Validate a user-submitted card order against stored card positions."""
    deck = Deck.query.get(deck_id)
    if not deck:
        return {'valid': False, 'error': 'Deck not found'}
    if not deck.sortable:
        return {'valid': False, 'error': 'Deck is not sortable'}

    # Stored positions define the correct order.
    cards = sorted(list(deck.cards), key=lambda card: card.position)
    expected_order = [card.card_id for card in cards]

    if len(expected_order) == 0:
        return {'valid': True, 'is_correct': True, 'incorrect_card_ids': [], 'expected_order': [], 'received_order': []}

    if len(ordered_card_ids) != len(expected_order):
        return {'valid': False, 'error': 'Submitted order does not include all cards'}

    if set(ordered_card_ids) != set(expected_order):
        return {'valid': False, 'error': 'Submitted order contains unknown cards'}

    incorrect_card_ids = []
    for index, card_id in enumerate(ordered_card_ids):
        if card_id != expected_order[index]:
            incorrect_card_ids.append(card_id)

    return {
        'valid': True,
        'is_correct': len(incorrect_card_ids) == 0,
        'incorrect_card_ids': incorrect_card_ids,
        'expected_order': expected_order,
        'received_order': ordered_card_ids,
    }


# Move one card up or down.
def move_card_in_deck(card_id, direction):
    """Move a card up or down within its deck by swapping position with a neighbor."""
    card = Card.query.get(card_id)
    if not card:
        return {'success': False, 'error': 'Card not found'}
    if not card.deck.sortable:
        return {'success': False, 'error': 'Card order can only be changed in sortable decks'}

    if direction not in ('up', 'down'):
        return {'success': False, 'error': 'Invalid direction'}

    deck_cards = Card.query.filter_by(deck_id=card.deck_id).order_by(Card.position).all()
    current_index = next((index for index, deck_card in enumerate(deck_cards) if deck_card.card_id == card_id), None)
    if current_index is None:
        return {'success': False, 'error': 'Card not found in deck'}

    target_index = current_index - 1 if direction == 'up' else current_index + 1
    if target_index < 0 or target_index >= len(deck_cards):
        return {'success': True, 'moved': False, 'deck_id': card.deck_id}

    target_card = deck_cards[target_index]
    card.position, target_card.position = target_card.position, card.position
    db.session.commit()

    return {'success': True, 'moved': True, 'deck_id': card.deck_id}


# Swap two cards inside one deck.
def swap_cards_in_deck(card_id, target_card_id):
    """Swap two cards in the same sortable deck."""
    first_card = Card.query.get(card_id)
    second_card = Card.query.get(target_card_id)

    if not first_card or not second_card:
        return {'success': False, 'error': 'One or more cards were not found'}
    if first_card.deck_id != second_card.deck_id:
        return {'success': False, 'error': 'Cards must be in the same deck'}
    if not first_card.deck.sortable:
        return {'success': False, 'error': 'Card order can only be changed in sortable decks'}
    if first_card.card_id == second_card.card_id:
        return {'success': True, 'swapped': False, 'deck_id': first_card.deck_id}

    first_card.position, second_card.position = second_card.position, first_card.position
    db.session.commit()

    return {'success': True, 'swapped': True, 'deck_id': first_card.deck_id}


# Get all cards from a deck ordered by position
# Return cards from one deck.
def list_cards_from_deck(deck_id, detailed=False, shuffle=False):
    deck = Deck.query.get(deck_id)
    if not deck:
        return []
    return _serialize_deck(deck, detailed_cards=detailed, shuffle_cards=shuffle, shuffle_answers=False)['cards']


# Return full deck details for the UI.
def get_deck_details(deck_id, shuffle_cards=False, shuffle_answers=False):
    deck = Deck.query.get(deck_id)
    if not deck:
        return None
    return _serialize_deck(deck, detailed_cards=True, shuffle_cards=shuffle_cards, shuffle_answers=shuffle_answers)


# Generate quiz questions from a deck or custom quiz.
def generate_quiz_data(deck_id=None, custom_quiz_id=None):
    from models import Quiz, Card, CardAnswer
    quiz_questions = []
    
    if deck_id:
        deck = Deck.query.get(deck_id)
        cards = list(deck.cards) if deck else []

        # Build distractors from the selected deck only.
        all_distractors = []
        other_answers = CardAnswer.query.join(Card).filter(Card.deck_id == deck_id).all()
        all_distractors = [a.answer for a in other_answers]
        
        if not all_distractors:
            all_distractors = ["Option A", "Option B", "Option C", "Option D", "No other answers available"]

        for i, c in enumerate(cards):
            is_obj = hasattr(c, 'question')
            question_text = c.question if is_obj else c.get('question')
            
            if is_obj:
                correct_pool = [a.answer for a in c.answers]
            else:
                correct_pool = c.get('correct_answers', [])
                
            num_correct = random.randint(1, len(correct_pool)) if correct_pool else 0
            chosen_correct = random.sample(correct_pool, num_correct) if correct_pool else []
            
            num_wrong = 4 - len(chosen_correct)
            if num_wrong < 0:
                chosen_correct = random.sample(chosen_correct, 4)
                num_wrong = 0
                
            chosen_wrong = []
            if num_wrong > 0:
                safe_distractors = [d for d in all_distractors if d not in correct_pool]
                if len(safe_distractors) >= num_wrong:
                    chosen_wrong = random.sample(safe_distractors, num_wrong)
                else:
                    chosen_wrong = safe_distractors + [f"Generic Distractor {x}" for x in range(num_wrong - len(safe_distractors))]
                    
            options = [{'text': ans, 'is_correct': True} for ans in chosen_correct] + \
                      [{'text': ans, 'is_correct': False} for ans in chosen_wrong]
            random.shuffle(options)
            
            quiz_questions.append({
                'id': c.card_id if is_obj else f"custom_{i}",
                'question': question_text,
                'options': options
            })
            
    elif custom_quiz_id:
        quiz = Quiz.query.get(custom_quiz_id)
        if not quiz: return []
        
        all_quiz_options_by_q = {q.question_id: [opt.text for opt in q.options] for q in quiz.questions}
        
        for q in quiz.questions:
            if q.type == 'static':
                options = [{'text': opt.text, 'is_correct': opt.is_correct} for opt in q.options]
                random.shuffle(options)
                quiz_questions.append({
                    'id': f"q_{q.question_id}",
                    'question': q.question,
                    'options': options
                })
            elif q.type == 'dynamic':
                # Dynamic quiz questions draw one correct option plus distractors.
                correct_pool = [opt.text for opt in q.options]
                chosen_correct = [random.choice(correct_pool)] if correct_pool else []
                
                distractor_pool = []
                for other_q_id, opts in all_quiz_options_by_q.items():
                    if other_q_id != q.question_id:
                        distractor_pool.extend(opts)
                
                chosen_wrong = []
                # Ensure distractors are not accidentally correct for this question
                safe_distractors = list(set([d for d in distractor_pool if d not in correct_pool]))
                if len(safe_distractors) >= 3:
                    chosen_wrong = random.sample(safe_distractors, 3)
                else:
                    chosen_wrong = safe_distractors + [f"Distractor {x}" for x in range(3 - len(safe_distractors))]
                    
                options = [{'text': ans, 'is_correct': True} for ans in chosen_correct] + \
                          [{'text': ans, 'is_correct': False} for ans in chosen_wrong]
                random.shuffle(options)
                
                quiz_questions.append({
                    'id': f"q_{q.question_id}",
                    'question': q.question,
                    'options': options
                })
        
    return quiz_questions


# Register all application routes
register_routes(app)


if __name__ == '__main__':
    app.run(debug=True)



