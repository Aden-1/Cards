import csv
import hashlib
import hmac
import io
import json
import random
import re
import secrets
import smtplib
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import click
from flask import current_app
from itsdangerous import BadSignature, BadTimeSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, insert, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import selectinload
from werkzeug.security import generate_password_hash

from identity import canonical_email, canonical_username, display_username
from services.authorization import audit_event
from models import (
    Card,
    CardAnswer,
    CardMasteryProgress,
    Deck,
    DeckTag,
    MatchPairProgress,
    Quiz,
    QuizAttempt,
    QuizOption,
    QuizQuestion,
    User,
    db,
)
from search_index import install_search_schema

# Service modules contain request-independent domain operations. They use
# current_app only for configuration and logging, so multiple factories can
# coexist in one process without sharing mutable application state.
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50


def _legacy_override(name, implementation):
    """Honor old module-level monkeypatches without importing the WSGI app."""
    compatibility_module = sys.modules.get('app')
    override = getattr(compatibility_module, '__dict__', {}).get(name)
    return override if override is not None and override is not implementation else implementation

MAX_DECK_DESCRIPTION_LENGTH = 255
MAX_DECK_DETAILED_DESCRIPTION_LENGTH = 5000
MAX_DECK_TAGS_LENGTH = 255
MAX_QUIZ_TITLE_LENGTH = 255
MAX_QUIZ_DESCRIPTION_LENGTH = 5000
MAX_QUIZ_TAGS_LENGTH = 255
MAX_CARD_QUESTION_LENGTH = 5000
MAX_CARD_ANSWER_LENGTH = 2000
MAX_IMPORT_CARD_COUNT = 500
MAX_IMPORT_ANSWERS_PER_CARD = 10
MAX_QUIZ_OPTIONS_PER_QUESTION = 5
MAX_IMPORT_RAW_TEXT_BYTES = 2 * 1024 * 1024
ORDER_MUTATION_RETRIES = 3


def _commit_domain_error(message):
    """Commit one service transaction and turn races into safe domain errors."""
    try:
        db.session.commit()
    except (IntegrityError, OperationalError) as exc:
        db.session.rollback()
        raise ValueError(message) from exc


def _locked_deck(deck_id):
    """Lock the deck on PostgreSQL; SQLite serializes the eventual writer."""
    return Deck.query.filter(Deck.deck_id == deck_id).with_for_update().first()


def _renumber_deck_cards(deck_id):
    """Restore dense 1-based positions without violating the unique index."""
    cards = Card.query.filter_by(deck_id=deck_id).order_by(Card.position, Card.card_id).all()
    if not cards:
        return
    temporary_base = max([card.position for card in cards] + [len(cards)]) + len(cards) + 1
    for offset, card in enumerate(cards):
        card.position = temporary_base + offset
    db.session.flush()
    for position, card in enumerate(cards, start=1):
        card.position = position
    db.session.flush()


def _swap_positions(first_card, second_card):
    first_position = first_card.position
    second_position = second_card.position
    temporary_position = max(first_position, second_position) + 1
    first_card.position = temporary_position
    db.session.flush()
    second_card.position = first_position
    db.session.flush()
    first_card.position = second_position
    db.session.flush()


# Text validation helpers.
def normalize_password_reset_email(email):
    return canonical_email(email) or ''


def password_reset_target_digest(email):
    """Return a non-reversible, deployment-keyed recovery lookup value."""
    normalized = normalize_password_reset_email(email)
    return hmac.new(
        current_app.config['PASSWORD_RESET_LOOKUP_KEY'].encode('utf-8'),
        normalized.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def _clean_text(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _validate_text_length(label, value, max_length, required=False):
    value = _clean_text(value)
    if required and not value:
        raise ValueError(f'{label} is required.')
    if value and len(value) > max_length:
        raise ValueError(f'{label} must be {max_length} characters or fewer.')
    return value


def _validate_deck_metadata(description, detailed_description=None, tags=None):
    return (
        _validate_text_length('Deck name', description, MAX_DECK_DESCRIPTION_LENGTH, required=True),
        _validate_text_length('Detailed description', detailed_description, MAX_DECK_DETAILED_DESCRIPTION_LENGTH),
        _validate_text_length('Tags', tags, MAX_DECK_TAGS_LENGTH),
    )


def _validate_quiz_metadata(title, description=None, tags=None):
    return (
        _validate_text_length('Quiz title', title, MAX_QUIZ_TITLE_LENGTH, required=True),
        _validate_text_length('Quiz description', description, MAX_QUIZ_DESCRIPTION_LENGTH),
        _validate_text_length('Quiz tags', tags, MAX_QUIZ_TAGS_LENGTH),
    )


def _validate_card_payload(question, answers):
    question = _validate_text_length('Question', question, MAX_CARD_QUESTION_LENGTH, required=True)
    normalized_answers = _normalize_answers(answers)
    if not normalized_answers:
        raise ValueError('At least one answer is required.')
    if len(normalized_answers) > MAX_IMPORT_ANSWERS_PER_CARD:
        raise ValueError(f'Cards may have at most {MAX_IMPORT_ANSWERS_PER_CARD} answers.')

    cleaned_answers = []
    for answer_text in normalized_answers:
        cleaned_answers.append(
            _validate_text_length('Answer', answer_text, MAX_CARD_ANSWER_LENGTH, required=True)
        )
    return question, cleaned_answers


# Serialization and import helpers.
def _normalize_answers(answers):
    if answers is None:
        return []
    if isinstance(answers, str):
        answers = [part.strip() for part in answers.split(',')]
    return [answer.strip() for answer in answers if str(answer).strip()]


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
        'is_featured': deck.is_featured,
        'card_count': len(cards),
        'answer_count': len(flattened_answers),
        'cards': serialized_cards,
        'answers': flattened_answers,
    }


def parse_imported_deck_text(raw_text):
    """Parse pasted deck text in common external formats (CSV or tab-delimited)."""
    if raw_text is None:
        raw_text = ''
    if not isinstance(raw_text, str):
        raw_text = str(raw_text)
    if len(raw_text) > MAX_IMPORT_RAW_TEXT_BYTES:
        raise ValueError(f'Imported deck text must be {MAX_IMPORT_RAW_TEXT_BYTES} bytes or fewer.')
    if len(raw_text.encode('utf-8')) > MAX_IMPORT_RAW_TEXT_BYTES:
        raise ValueError(f'Imported deck text must be {MAX_IMPORT_RAW_TEXT_BYTES} bytes or fewer.')
    if not raw_text.strip('\ufeff \n\t'):
        raise ValueError('Paste deck content to import.')

    invalid_lines = 0
    valid_line_count = 0
    card_map = {}
    card_order = []

    for line in io.StringIO(raw_text.lstrip('\ufeff \n\t')):
        line = line.rstrip('\r\n')
        if not line.strip():
            continue
        delimiter = '\t' if '\t' in line else ','
        columns = next(csv.reader([line], delimiter=delimiter, quotechar='"', skipinitialspace=True), [])
        if len(columns) < 2:
            invalid_lines += 1
            continue
        question = (columns[0] or '').strip()
        answer = delimiter.join(columns[1:]).strip()
        if not question or not answer:
            invalid_lines += 1
            continue
        cleaned_question = _validate_text_length('Question', question, MAX_CARD_QUESTION_LENGTH, required=True)
        cleaned_answer = _validate_text_length('Answer', answer, MAX_CARD_ANSWER_LENGTH, required=True)
        if cleaned_question not in card_map:
            if len(card_map) >= MAX_IMPORT_CARD_COUNT:
                raise ValueError(f'Imported decks may contain at most {MAX_IMPORT_CARD_COUNT} cards.')
            card_map[cleaned_question] = []
            card_order.append(cleaned_question)
        if cleaned_answer not in card_map[cleaned_question]:
            if len(card_map[cleaned_question]) >= MAX_IMPORT_ANSWERS_PER_CARD:
                raise ValueError(f'Cards may have at most {MAX_IMPORT_ANSWERS_PER_CARD} answers.')
            card_map[cleaned_question].append(cleaned_answer)
        valid_line_count += 1

    cards = [{'question': question, 'answers': card_map[question]} for question in card_order if card_map[question]]
    if not cards:
        raise ValueError('No valid cards found after parsing.')

    return {
        'cards': cards,
        'invalid_lines': invalid_lines,
        'line_count': valid_line_count + invalid_lines,
    }


def export_deck_as_text(deck):
    """Export a deck as line-based CSV text for copy/paste."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\n')
    cards = sorted(list(deck.cards), key=lambda card: card.position)
    for card in cards:
        for answer in card.answers:
            writer.writerow([card.question or '', answer.answer or ''])
    return buffer.getvalue().strip('\n')


# Custom quiz helpers.
def _attach_quiz_question_counts(quiz_rows):
    quizzes = []
    for quiz, question_count in quiz_rows:
        quiz.question_count = int(question_count or 0)
        quizzes.append(quiz)
    return quizzes


def _bounded_page(page=1, per_page=DEFAULT_PAGE_SIZE):
    """Normalize page input for all collection queries, including API callers."""
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = max(1, min(int(per_page), MAX_PAGE_SIZE))
    except (TypeError, ValueError):
        per_page = DEFAULT_PAGE_SIZE
    return page, per_page


def _page_rows(query, attach_counts, page=1, per_page=DEFAULT_PAGE_SIZE):
    """Fetch at most one page plus a look-ahead row, with stable metadata."""
    page, per_page = _bounded_page(page, per_page)
    rows = query.limit(per_page + 1).offset((page - 1) * per_page).all()
    has_next = len(rows) > per_page
    if has_next:
        rows = rows[:per_page]
    return {
        'items': attach_counts(rows),
        'page': page,
        'per_page': per_page,
        'has_prev': page > 1,
        'has_next': has_next,
        'prev_page': page - 1 if page > 1 else None,
        'next_page': page + 1 if has_next else None,
    }


def _quiz_query_with_question_counts():
    return (
        db.session.query(Quiz)
        .outerjoin(QuizQuestion, QuizQuestion.quiz_id == Quiz.quiz_id)
        .group_by(Quiz.quiz_id)
        .add_columns(db.func.count(QuizQuestion.question_id).label('question_count'))
    )


def _quiz_query_with_content():
    return Quiz.query.options(
        selectinload(Quiz.questions).selectinload(QuizQuestion.options)
    )


def get_quiz_with_content(quiz_id):
    return _quiz_query_with_content().filter(Quiz.quiz_id == quiz_id).first()


def get_accessible_custom_quizzes(user_id=None):
    # Kept for non-page callers; it is still capped to avoid accidental wide reads.
    return get_accessible_custom_quizzes_page(user_id)['items']


def get_user_custom_quizzes(user_id):
    return get_user_custom_quizzes_page(user_id)['items']


def get_accessible_custom_quizzes_page(user_id=None, page=1, per_page=DEFAULT_PAGE_SIZE):
    query = _quiz_query_with_question_counts()
    if user_id is None:
        query = query.filter(Quiz.is_public == True)
    else:
        query = query.filter((Quiz.owned_by == user_id) | (Quiz.is_public == True))
    return _page_rows(query.order_by(Quiz.quiz_id.asc()), _attach_quiz_question_counts, page, per_page)


def get_user_custom_quizzes_page(user_id, page=1, per_page=DEFAULT_PAGE_SIZE):
    query = _quiz_query_with_question_counts().filter(Quiz.owned_by == user_id).order_by(Quiz.quiz_id.asc())
    return _page_rows(query, _attach_quiz_question_counts, page, per_page)

def create_custom_quiz(user_id, title, is_public=False, description=None, tags=None):
    title, description, tags = _validate_quiz_metadata(title, description, tags)
    quiz = Quiz(
        owned_by=user_id,
        title=title,
        is_public=is_public,
        description=description,
        tags=tags,
    )
    db.session.add(quiz)
    _commit_domain_error('That quiz could not be saved. Please try again.')
    return quiz

def edit_custom_quiz(quiz_id, title, is_public=False, description=None, tags=None):
    quiz = db.session.get(Quiz, quiz_id)
    if quiz:
        title, description, tags = _validate_quiz_metadata(title, description, tags)
        quiz.title = title
        quiz.is_public = is_public
        quiz.description = description
        quiz.tags = tags
        _commit_domain_error('That quiz could not be saved. Please try again.')
        return quiz
    return None

def delete_custom_quiz(quiz_id):
    quiz = db.session.get(Quiz, quiz_id)
    if quiz:
        db.session.delete(quiz)
        db.session.commit()
        return True
    return False


def _max_quiz_questions():
    try:
        return max(1, int(current_app.config.get('MAX_QUIZ_QUESTIONS', 50)))
    except (AttributeError, TypeError, ValueError):
        return 50


def _insert_deck_graph(user_id, description, detailed_description, tags, sortable, is_public, cards, is_featured=False):
    """Insert one validated deck graph with a constant number of SQL batches."""
    if len(cards) > MAX_IMPORT_CARD_COUNT:
        raise ValueError(f'Decks may contain at most {MAX_IMPORT_CARD_COUNT} cards.')
    positions = [card['position'] for card in cards]
    if any(not isinstance(position, int) or position < 1 for position in positions):
        raise ValueError('Card positions must be positive integers.')
    if len(set(positions)) != len(positions):
        raise ValueError('Card positions must be unique within a deck.')

    deck_id = db.session.execute(
        insert(Deck).values(
            owned_by=user_id,
            description=description,
            detailed_description=detailed_description,
            tags=tags,
            sortable=sortable,
            is_public=is_public,
            is_featured=is_featured,
        ).returning(Deck.deck_id)
    ).scalar_one()

    tag_rows = [
        {'deck_id': deck_id, 'tag_normalized': normalized, 'tag_display': display}
        for normalized, display in _normalized_tag_rows(tags)
    ]
    if tag_rows:
        db.session.execute(insert(DeckTag), tag_rows)

    card_rows = [
        {'deck_id': deck_id, 'question': card['question'], 'position': card['position']}
        for card in cards
    ]
    if card_rows:
        db.session.execute(insert(Card), card_rows)
        position_to_id = dict(
            db.session.query(Card.position, Card.card_id)
            .filter(Card.deck_id == deck_id)
            .order_by(Card.position)
            .all()
        )
        if len(position_to_id) != len(cards):
            raise RuntimeError('Inserted card graph could not be correlated.')
        answer_rows = [
            {'card_id': position_to_id[card['position']], 'answer': answer}
            for card in cards
            for answer in card['answers']
        ]
        if answer_rows:
            db.session.execute(insert(CardAnswer), answer_rows)
    return deck_id


def _insert_quiz_graph(user_id, title, description, tags, questions):
    """Insert one bounded quiz graph with explicitly ordered ID correlation."""
    if len(questions) > _max_quiz_questions():
        raise ValueError(f'Quizzes may contain at most {_max_quiz_questions()} questions.')
    quiz_id = db.session.execute(
        insert(Quiz).values(
            owned_by=user_id,
            title=title,
            description=description,
            tags=tags,
            is_public=False,
        ).returning(Quiz.quiz_id)
    ).scalar_one()

    question_rows = [
        {'quiz_id': quiz_id, 'question': question['question'], 'type': question['type']}
        for question in questions
    ]
    question_ids = []
    if question_rows:
        question_ids = list(db.session.execute(
            insert(QuizQuestion).returning(
                QuizQuestion.question_id,
                sort_by_parameter_order=True,
            ),
            question_rows,
        ).scalars())
    option_rows = [
        {'question_id': question_id, 'text': option['text'], 'is_correct': option['is_correct']}
        for question_id, question in zip(question_ids, questions)
        for option in question['options']
    ]
    if option_rows:
        db.session.execute(insert(QuizOption), option_rows)
    return quiz_id


def _load_copyable_quiz(source_quiz_id):
    source_quiz = Quiz.query.filter(
        Quiz.quiz_id == source_quiz_id,
        Quiz.is_public == True,
    ).first()
    if not source_quiz:
        return None
    question_count = db.session.query(func.count(QuizQuestion.question_id)).filter(
        QuizQuestion.quiz_id == source_quiz_id,
    ).scalar()
    if int(question_count or 0) > _max_quiz_questions():
        raise ValueError(f'Public quizzes may contain at most {_max_quiz_questions()} questions.')
    option_counts = (
        db.session.query(QuizQuestion.question_id, func.count(QuizOption.option_id))
        .outerjoin(QuizOption, QuizOption.question_id == QuizQuestion.question_id)
        .filter(QuizQuestion.quiz_id == source_quiz_id)
        .group_by(QuizQuestion.question_id)
        .all()
    )
    if any(int(count or 0) > MAX_QUIZ_OPTIONS_PER_QUESTION for _, count in option_counts):
        raise ValueError(f'Quiz questions may have at most {MAX_QUIZ_OPTIONS_PER_QUESTION} options.')
    return get_quiz_with_content(source_quiz_id)


def _load_copyable_deck(source_deck_id):
    source_deck = Deck.query.filter(
        Deck.deck_id == source_deck_id,
        Deck.is_public == True,
    ).first()
    if not source_deck:
        return None
    card_count = db.session.query(func.count(Card.card_id)).filter(
        Card.deck_id == source_deck_id,
    ).scalar()
    if int(card_count or 0) > MAX_IMPORT_CARD_COUNT:
        raise ValueError(f'Public decks may contain at most {MAX_IMPORT_CARD_COUNT} cards.')
    answer_counts = (
        db.session.query(Card.card_id, func.count(CardAnswer.answer_id))
        .outerjoin(CardAnswer, CardAnswer.card_id == Card.card_id)
        .filter(Card.deck_id == source_deck_id)
        .group_by(Card.card_id)
        .all()
    )
    if any(int(count or 0) > MAX_IMPORT_ANSWERS_PER_CARD for _, count in answer_counts):
        raise ValueError(f'Cards may have at most {MAX_IMPORT_ANSWERS_PER_CARD} answers.')
    return get_deck_with_content(source_deck_id)


def copy_public_quiz_to_user(source_quiz_id, user_id):
    try:
        source_quiz = _load_copyable_quiz(source_quiz_id)
        if not source_quiz:
            return None

        copy_title = f"{source_quiz.title} (Copy)"
        _validate_quiz_metadata(copy_title, source_quiz.description, source_quiz.tags)
        copied_questions = []
        for source_question in sorted(source_quiz.questions, key=lambda row: row.question_id):
            _validate_text_length('Question', source_question.question, MAX_CARD_QUESTION_LENGTH, required=True)
            if source_question.type not in ('dynamic', 'static'):
                raise ValueError('Public quiz contains an unsupported question type.')
            copied_options = []
            for source_option in sorted(source_question.options, key=lambda row: row.option_id):
                _validate_text_length('Option', source_option.text, MAX_CARD_ANSWER_LENGTH, required=True)
                copied_options.append({'text': source_option.text, 'is_correct': source_option.is_correct})
            copied_questions.append({
                'question': source_question.question,
                'type': source_question.type,
                'options': copied_options,
            })

        copied_quiz_id = _insert_quiz_graph(
            user_id, copy_title, source_quiz.description, source_quiz.tags, copied_questions
        )
        _commit_domain_error('That quiz copy could not be saved. Please try again.')
        return db.session.get(Quiz, copied_quiz_id)
    except Exception:
        db.session.rollback()
        raise


def copy_public_deck_to_user(source_deck_id, user_id):
    try:
        source_deck = _load_copyable_deck(source_deck_id)
        if not source_deck:
            return None

        copy_description = f"{source_deck.description} (Copy)"
        _validate_deck_metadata(copy_description, source_deck.detailed_description, source_deck.tags)
        copied_cards = []
        for position, source_card in enumerate(
            sorted(source_deck.cards, key=lambda row: (row.position, row.card_id)),
            start=1,
        ):
            _validate_text_length('Question', source_card.question, MAX_CARD_QUESTION_LENGTH, required=True)
            copied_answers = []
            for source_answer in sorted(source_card.answers, key=lambda row: row.answer_id):
                _validate_text_length('Answer', source_answer.answer, MAX_CARD_ANSWER_LENGTH, required=True)
                copied_answers.append(source_answer.answer)
            copied_cards.append({
                'question': source_card.question,
                'position': position,
                'answers': copied_answers,
            })

        copied_deck_id = _insert_deck_graph(
            user_id, copy_description, source_deck.detailed_description,
            source_deck.tags, source_deck.sortable, False, copied_cards,
        )
        _commit_domain_error('That deck copy could not be saved. Please try again.')
        return db.session.get(Deck, copied_deck_id)
    except Exception:
        db.session.rollback()
        raise

def add_quiz_question(quiz_id, question_text, q_type, options_data):
    if q_type not in ('dynamic', 'static'):
        raise ValueError('Quiz question type must be dynamic or static.')
    if len(options_data) > MAX_QUIZ_OPTIONS_PER_QUESTION:
        raise ValueError(f'Quiz questions may have at most {MAX_QUIZ_OPTIONS_PER_QUESTION} options.')

    question_text = _validate_text_length('Question', question_text, MAX_CARD_QUESTION_LENGTH, required=True)
    cleaned_options = []
    for opt in options_data:
        option_text = _validate_text_length('Option', opt.get('text'), MAX_CARD_ANSWER_LENGTH, required=True)
        cleaned_options.append({'text': option_text, 'is_correct': bool(opt.get('is_correct', False))})

    q = QuizQuestion(quiz_id=quiz_id, question=question_text, type=q_type)
    db.session.add(q)
    db.session.flush()
    
    for opt in cleaned_options:
        qo = QuizOption(question_id=q.question_id, text=opt['text'], is_correct=opt['is_correct'])
        db.session.add(qo)
    _commit_domain_error('That question could not be saved. Please try again.')
    return q


def edit_quiz_question(question_id, question_text, q_type, options_data):
    q = db.session.get(QuizQuestion, question_id)
    if not q:
        return None
    if q_type not in ('dynamic', 'static'):
        raise ValueError('Quiz question type must be dynamic or static.')
    if len(options_data) > MAX_QUIZ_OPTIONS_PER_QUESTION:
        raise ValueError(f'Quiz questions may have at most {MAX_QUIZ_OPTIONS_PER_QUESTION} options.')

    question_text = _validate_text_length('Question', question_text, MAX_CARD_QUESTION_LENGTH, required=True)
    cleaned_options = []
    for opt in options_data:
        option_text = _validate_text_length('Option', opt.get('text'), MAX_CARD_ANSWER_LENGTH, required=True)
        cleaned_options.append({'text': option_text, 'is_correct': bool(opt.get('is_correct', False))})

    q.question = question_text
    q.type = q_type
    for existing_option in list(q.options):
        db.session.delete(existing_option)
    for opt in cleaned_options:
        db.session.add(QuizOption(question_id=q.question_id, text=opt['text'], is_correct=opt['is_correct']))
    _commit_domain_error('That question could not be saved. Please try again.')
    return q


# Delete a quiz question and child options.
def delete_quiz_question(question_id):
    q = db.session.get(QuizQuestion, question_id)
    if q:
        db.session.delete(q)
        db.session.commit()
        return True
    return False


# User helpers.
def create_user(username, password, email=None, role='standard'):
    username = display_username(username)
    email = normalize_password_reset_email(email) or None
    user = User(
        username=username,
        canonical_username=canonical_username(username),
        email=email,
        canonical_email=email,
        recovery_email_digest=password_reset_target_digest(email) if email else None,
        role=role,
    )
    user.set_password(password)
    db.session.add(user)
    _commit_domain_error('That account could not be saved. The username or email may already be in use.')
    return user


def set_user_role(user, role):
    if role not in ('standard', 'moderator', 'admin'):
        raise ValueError('Role must be standard, moderator, or admin.')
    user.role = role
    db.session.commit()
    return user


@click.command('provision-admin')
@click.option('--username', required=True, help='Username for the new administrator.')
@click.option('--email', default=None, help='Optional email address for the administrator.')
@click.password_option(confirmation_prompt=True)
def provision_admin(username, email, password):
    """Create the initial administrator outside the public registration flow."""
    try:
        username = display_username(username)
        email = normalize_password_reset_email(email) if email else None
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if not re.fullmatch(r'[\w.-]{3,40}', username, re.UNICODE):
        raise click.ClickException('Username must be 3-40 letters, numbers, dots, dashes, or underscores.')
    if len(password) < 12 or not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
        raise click.ClickException('Password must be at least 12 characters and contain a letter and a number.')
    if get_user(username) or (email and get_user_by_email(email)):
        raise click.ClickException('An account already exists with that username or email.')

    try:
        user = create_user(username=username, password=password, email=email, role='admin')
    except (IntegrityError, ValueError) as exc:
        db.session.rollback()
        raise click.ClickException('An account already exists with that username or email.') from exc
    audit_event('administrator_provisioned', None, 'success', target_type='user', target_id=user.user_id, role='admin')
    click.echo(f'Created administrator account: {user.username}')


@click.command('set-user-role')
@click.option('--username', default=None, help='Username of the existing account.')
@click.option('--email', default=None, help='Email of the existing account.')
@click.option('--role', required=True, type=click.Choice(['standard', 'moderator', 'admin'], case_sensitive=False))
def set_user_role_command(username, email, role):
    """Change an existing user's role through a controlled CLI workflow."""
    try:
        username = display_username(username) if username else None
        email = normalize_password_reset_email(email) if email else None
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if not username and not email:
        raise click.ClickException('Provide --username or --email.')

    user = get_user(username) if username else get_user_by_email(email)
    if not user:
        raise click.ClickException('User not found.')

    role = role.lower()
    set_user_role(user, role)
    audit_event('role_changed', None, 'success', target_type='user', target_id=user.user_id, role=role, source='cli')
    click.echo(f'Updated {user.username} to role {role}.')


@click.command('rebuild-public-search-index')
def rebuild_public_search_index_command():
    """Rebuild the public full-text search index."""
    try:
        _rebuild_content_fts_index()
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    current_app.logger.info('public_search_index_rebuilt backend=%s', _search_backend_name())
    click.echo(f"Rebuilt public search index for {_search_backend_name()}.")


@click.command('check-public-search-index')
@click.option('--limit', 'sample_limit', type=click.IntRange(1, 500), default=100, show_default=True)
def check_public_search_index_command(sample_limit):
    """Report bounded public search-index drift without repairing it."""
    report = check_public_search_index(sample_limit)
    current_app.logger.info(
        'public_search_index_checked backend=%s missing=%s orphan=%s stale=%s',
        report['backend'], report['missing_count'], report['orphan_count'], report['stale_count'],
    )
    click.echo(json.dumps(report, sort_keys=True))


@click.command('cleanup-quiz-attempts')
def cleanup_quiz_attempts_command():
    """Delete expired server-side quiz attempts."""
    deleted_rows = delete_expired_quiz_attempts()
    click.echo(f'Deleted {deleted_rows} expired quiz attempt(s).')

def get_user(username):
    try:
        canonical = canonical_username(username)
    except ValueError:
        return None
    return User.query.filter_by(canonical_username=canonical).first()


def get_user_by_id(user_id):
    return db.session.get(User, user_id)


def get_user_by_email(email):
    if not email:
        return None
    try:
        canonical = canonical_email(email, allow_none=False)
    except ValueError:
        return None
    return User.query.filter_by(canonical_email=canonical).first()


def update_user_account(user_id, username, email=None, password=None):
    user = db.session.get(User, user_id)
    if not user:
        return None
    user.username = display_username(username)
    user.canonical_username = canonical_username(user.username)
    email = normalize_password_reset_email(email) or None
    user.email = email
    user.canonical_email = email
    user.recovery_email_digest = password_reset_target_digest(email) if email else None
    if password:
        user.set_password(password)
        user.auth_version += 1
    _commit_domain_error('That account could not be saved. The username or email may already be in use.')
    return user


def _account_token_serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='cards-account-lifecycle')


def generate_password_reset_token(user):
    serializer = _account_token_serializer()
    return serializer.dumps({
        'user_id': user.user_id,
        'email': user.canonical_email,
        'auth_version': user.auth_version,
        'purpose': 'password_reset',
    })


def _load_password_reset_token_payload(token, max_age_seconds=None):
    serializer = _account_token_serializer()
    if max_age_seconds is None:
        max_age_seconds = current_app.config['PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS']
    try:
        payload = serializer.loads(token, max_age=max_age_seconds)
    except (BadSignature, BadTimeSignature, SignatureExpired):
        return None

    if payload.get('purpose') != 'password_reset':
        return None
    user_id = payload.get('user_id')
    email = payload.get('email')
    auth_version = payload.get('auth_version')
    if not user_id or not email or type(auth_version) is not int or auth_version < 0:
        return None
    return payload


def get_user_by_password_reset_token(token, max_age_seconds=None):
    payload = _load_password_reset_token_payload(token, max_age_seconds=max_age_seconds)
    if not payload:
        return None
    user = db.session.get(User, payload['user_id'])
    if (
        not user
        or not user.is_active
        or user.canonical_email != payload['email']
        or user.auth_version != payload['auth_version']
    ):
        return None
    return user


def reset_user_password_with_token(token, password, max_age_seconds=None):
    """Atomically consume a reset token and revoke the user's existing sessions."""
    payload = _load_password_reset_token_payload(token, max_age_seconds=max_age_seconds)
    if not payload:
        return None

    updated_rows = User.query.filter(
        User.user_id == payload['user_id'],
        User.canonical_email == payload['email'],
        User.is_active.is_(True),
        User.auth_version == payload['auth_version'],
    ).update(
        {
            User.password_hash: generate_password_hash(password),
            User.auth_version: User.auth_version + 1,
        },
        synchronize_session=False,
    )
    if updated_rows != 1:
        db.session.rollback()
        return None

    db.session.commit()
    return payload['user_id']


def build_password_reset_url(token):
    configured_base = current_app.config.get('PASSWORD_RESET_URL_BASE')
    if configured_base:
        return f"{configured_base.rstrip('/')}?token={token}"
    return None


def enqueue_password_reset_email(target_digest, request_id):
    """Queue reset delivery without serializing a reset token or email address."""
    override = _legacy_override('enqueue_password_reset_email', enqueue_password_reset_email)
    if override is not enqueue_password_reset_email:
        return override(target_digest, request_id)
    from jobs import enqueue_password_reset_email as enqueue_job

    return enqueue_job(target_digest, request_id)


def send_password_reset_email(user, reset_url):
    message = EmailMessage()
    message['Subject'] = 'Reset your Cards password'
    message['From'] = current_app.config['MAIL_DEFAULT_SENDER']
    message['To'] = user.email
    message.set_content(
        (
            f"Hello {user.username},\n\n"
            "We received a request to reset your Cards password.\n"
            f"Use this link within {current_app.config['PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS'] // 60} minutes:\n\n"
            f"{reset_url}\n\n"
            "If you did not request this, you can ignore this email."
        )
    )

    smtp_host = current_app.config['MAIL_SERVER']
    smtp_port = current_app.config['MAIL_PORT']
    smtp_username = current_app.config.get('MAIL_USERNAME')
    smtp_password = current_app.config.get('MAIL_PASSWORD')
    use_ssl = current_app.config.get('MAIL_USE_SSL')
    use_tls = current_app.config.get('MAIL_USE_TLS')

    smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_class(
        smtp_host,
        smtp_port,
        timeout=current_app.config['PASSWORD_RESET_DELIVERY_TIMEOUT_SECONDS'],
    ) as smtp:
        if not use_ssl and use_tls:
            smtp.starttls()
        if smtp_username:
            smtp.login(smtp_username, smtp_password or '')
        smtp.send_message(message)


def delete_user_account(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return False

    db.session.delete(user)
    db.session.commit()
    return True


# Deck helpers.
def create_deck(user_id, description, sortable=False, is_public=False, is_featured=False, detailed_description=None, tags=None):
    description, detailed_description, tags = _validate_deck_metadata(description, detailed_description, tags)
    deck = Deck(
        owned_by=user_id,
        description=description,
        sortable=sortable,
        is_public=is_public,
        is_featured=is_featured,
        detailed_description=detailed_description,
        tags=tags
    )
    db.session.add(deck)
    db.session.flush()
    _replace_deck_tags(deck, tags)
    _commit_domain_error('That deck could not be saved. Please try again.')
    return deck


def import_deck(user_id, description, raw_text, sortable=False, is_public=False, is_featured=False, detailed_description=None, tags=None):
    description, detailed_description, tags = _validate_deck_metadata(description, detailed_description, tags)
    parsed = parse_imported_deck_text(raw_text)
    cards = parsed['cards']
    try:
        deck_id = _insert_deck_graph(
            user_id,
            description,
            detailed_description,
            tags,
            sortable,
            is_public,
            [
                {
                    'question': card_data['question'],
                    'position': position,
                    'answers': card_data['answers'],
                }
                for position, card_data in enumerate(cards, start=1)
            ],
            is_featured=is_featured,
        )
        _commit_domain_error('That deck import could not be saved. Please try again.')
        return {
            'deck': db.session.get(Deck, deck_id),
            'card_count': len(cards),
            'invalid_lines': parsed['invalid_lines'],
            'line_count': parsed['line_count'],
        }
    except Exception:
        db.session.rollback()
        raise

def _deck_query_with_content():
    return Deck.query.options(
        selectinload(Deck.cards).selectinload(Card.answers)
    )


def get_deck_with_content(deck_id):
    return _deck_query_with_content().filter(Deck.deck_id == deck_id).first()


def get_user_decks(user_id):
    return get_user_decks_page(user_id)['items']


def _attach_deck_card_counts(deck_rows):
    decks = []
    for deck, card_count in deck_rows:
        deck.card_count = int(card_count or 0)
        decks.append(deck)
    return decks


def _deck_query_with_card_counts():
    return (
        db.session.query(Deck)
        .outerjoin(Card, Card.deck_id == Deck.deck_id)
        .group_by(Deck.deck_id)
        .add_columns(db.func.count(Card.card_id).label('card_count'))
    )


def get_accessible_decks(user_id=None):
    query = _deck_query_with_card_counts()
    if user_id is None:
        query = query.filter(Deck.is_public == True)
    else:
        query = query.filter((Deck.owned_by == user_id) | (Deck.is_public == True))
    return _page_rows(query.order_by(Deck.deck_id.asc()), _attach_deck_card_counts)['items']


def get_user_decks_page(user_id, page=1, per_page=DEFAULT_PAGE_SIZE, sortable_only=False):
    query = _deck_query_with_card_counts().filter(Deck.owned_by == user_id)
    if sortable_only:
        query = query.filter(Deck.sortable == True)
    return _page_rows(query.order_by(Deck.deck_id.asc()), _attach_deck_card_counts, page, per_page)


def _normalized_tag_rows(tags):
    seen = set()
    for raw_tag in (tags or '').split(','):
        display = raw_tag.strip()
        normalized = display.casefold()
        if display and normalized not in seen:
            seen.add(normalized)
            yield normalized, display


def _replace_deck_tags(deck, tags):
    """Keep normalized tag rows in the same transaction as deck metadata."""
    DeckTag.query.filter_by(deck_id=deck.deck_id).delete(synchronize_session=False)
    for normalized, display in _normalized_tag_rows(tags):
        db.session.add(DeckTag(deck_id=deck.deck_id, tag_normalized=normalized, tag_display=display))


def get_homepage_public_data(featured_limit=3, tag_limit=5):
    try:
        featured_limit = max(0, min(int(featured_limit), MAX_PAGE_SIZE))
    except (TypeError, ValueError):
        featured_limit = 3
    try:
        tag_limit = max(0, min(int(tag_limit), MAX_PAGE_SIZE))
    except (TypeError, ValueError):
        tag_limit = 5
    featured_query = Deck.query.filter(
        Deck.is_public == True, Deck.is_featured == True
    ).order_by(Deck.deck_id.asc())
    featured_count = featured_query.count() if featured_limit else 0
    featured_limit = min(featured_limit, featured_count)
    offset = datetime.now().date().toordinal() % featured_count if featured_count else 0
    featured_decks = featured_query.limit(featured_limit).offset(offset).all() if featured_count else []
    # Wrap around without ever reading more than the requested feature limit.
    if len(featured_decks) < featured_limit and featured_count > len(featured_decks):
        featured_decks += featured_query.limit(featured_limit - len(featured_decks)).all()
    featured_ids = [deck.deck_id for deck in featured_decks]
    if featured_ids:
        counted_decks = _attach_deck_card_counts(
            _deck_query_with_card_counts().filter(Deck.deck_id.in_(featured_ids)).all()
        )
        by_id = {deck.deck_id: deck for deck in counted_decks}
        featured_decks = [by_id[deck_id] for deck_id in featured_ids]

    tag_rows = (
        db.session.query(
            DeckTag.tag_normalized,
            db.func.min(DeckTag.tag_display).label('tag'),
            db.func.count(DeckTag.deck_id).label('count'),
        )
        .join(Deck, Deck.deck_id == DeckTag.deck_id)
        .filter(Deck.is_public == True)
        .group_by(DeckTag.tag_normalized)
        .order_by(db.func.count(DeckTag.deck_id).desc(), DeckTag.tag_normalized.asc())
        .limit(tag_limit)
        .all()
    )
    featured_tags = [{'tag': row.tag, 'count': int(row.count)} for row in tag_rows]

    return {
        'featured_decks': [
            {
                'deck_id': deck.deck_id,
                'description': deck.description,
                'detailed_description': deck.detailed_description,
                'card_count': getattr(deck, 'card_count', 0),
                'is_featured': deck.is_featured,
            }
            for deck in featured_decks
        ],
        'featured_tags': featured_tags,
    }


def get_deck(deck_id):
    return get_deck_with_content(deck_id)


def delete_deck(deck_id):
    deck = db.session.get(Deck, deck_id)
    if deck:
        db.session.delete(deck)
        db.session.commit()
        return True
    return False

def edit_deck(deck_id, description, sortable=False, is_public=False, is_featured=False, detailed_description=None, tags=None):
    deck = db.session.get(Deck, deck_id)
    if deck:
        description, detailed_description, tags = _validate_deck_metadata(description, detailed_description, tags)
        deck.description = description
        deck.sortable = sortable
        deck.is_public = is_public
        deck.is_featured = is_featured
        deck.detailed_description = detailed_description
        deck.tags = tags
        _replace_deck_tags(deck, tags)
        db.session.commit()
        return deck
    return None

# Search helpers.
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
    ).order_by(Deck.deck_id.asc()).limit(MAX_PAGE_SIZE).all()
    return decks


def search_public_quizzes(query_text):
    if not query_text:
        return []

    search_term = f"%{query_text}%"
    quizzes = _attach_quiz_question_counts(
        _quiz_query_with_question_counts().filter(
            Quiz.is_public == True,
            db.or_(
                Quiz.title.ilike(search_term),
                Quiz.description.ilike(search_term),
                Quiz.tags.ilike(search_term)
            )
        ).order_by(Quiz.quiz_id.asc()).limit(MAX_PAGE_SIZE).all()
    )
    return quizzes


def _normalize_search_text(text):
    text = (text or '').lower()
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _tokenize_search_text(text):
    normalized = _normalize_search_text(text)
    if not normalized:
        return []
    return [token for token in normalized.split() if token]


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


def _search_backend_name():
    # Reflect the SQLAlchemy engine dialect (e.g., sqlite, postgresql).
    return db.engine.dialect.name


def _is_sqlite_backend():
    # SQLite keeps the existing FTS5 implementation.
    return _search_backend_name() == 'sqlite'


def _is_postgres_backend():
    # Postgres uses tsvector-based full-text search.
    return _search_backend_name().startswith('postgresql')


def _ensure_content_fts_index():
    # Schema changes are migration-owned.  This is called only by the
    # explicit rebuild/repair command, never by a search request or mutation.
    install_search_schema(db.session.connection())


def _rebuild_content_fts_index():
    _ensure_content_fts_index()
    if _is_sqlite_backend():
        db.session.execute(text("DELETE FROM public_content_fts"))
        db.session.execute(text("""
            INSERT INTO public_content_fts(item_type, item_id, title, description, tags)
            SELECT 'deck', CAST(deck_id AS TEXT), COALESCE(description, ''),
                   COALESCE(detailed_description, ''), COALESCE(tags, '')
            FROM deck WHERE is_public = 1
        """))
        db.session.execute(text("""
            INSERT INTO public_content_fts(item_type, item_id, title, description, tags)
            SELECT 'quiz', CAST(quiz_id AS TEXT), COALESCE(title, ''),
                   COALESCE(description, ''), COALESCE(tags, '')
            FROM quiz WHERE is_public = 1
        """))
    else:
        db.session.execute(text("DELETE FROM public_content_search"))
        db.session.execute(text("""
            INSERT INTO public_content_search
                (item_type, item_id, title, description, tags, search_vector)
            SELECT 'deck', deck_id, COALESCE(description, ''),
                   COALESCE(detailed_description, ''), COALESCE(tags, ''),
                   setweight(to_tsvector('english', COALESCE(description, '')), 'A') ||
                   setweight(to_tsvector('english', COALESCE(tags, '')), 'B') ||
                   setweight(to_tsvector('english', COALESCE(detailed_description, '')), 'C')
            FROM deck WHERE is_public
        """))
        db.session.execute(text("""
            INSERT INTO public_content_search
                (item_type, item_id, title, description, tags, search_vector)
            SELECT 'quiz', quiz_id, COALESCE(title, ''),
                   COALESCE(description, ''), COALESCE(tags, ''),
                   setweight(to_tsvector('english', COALESCE(title, '')), 'A') ||
                   setweight(to_tsvector('english', COALESCE(tags, '')), 'B') ||
                   setweight(to_tsvector('english', COALESCE(description, '')), 'C')
            FROM quiz WHERE is_public
        """))


def check_public_search_index(limit=100):
    """Return a bounded, read-only report of public search-index drift."""
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit = 100

    dialect = _search_backend_name()
    if dialect == 'sqlite':
        table = 'public_content_fts'
        public_sql = 'is_public = 1'
        id_expression = 'CAST(i.item_id AS INTEGER)'
        deck_id_expression = 'CAST(d.deck_id AS TEXT)'
        quiz_id_expression = 'CAST(q.quiz_id AS TEXT)'
    elif dialect.startswith('postgresql'):
        table = 'public_content_search'
        public_sql = 'is_public'
        id_expression = 'i.item_id'
        deck_id_expression = 'd.deck_id'
        quiz_id_expression = 'q.quiz_id'
    else:
        raise RuntimeError(f'Unsupported search index database dialect: {dialect}')

    with db.engine.connect() as connection:
        try:
            expected_count = connection.execute(text(f"""
                SELECT
                    (SELECT COUNT(*) FROM deck WHERE {public_sql}) +
                    (SELECT COUNT(*) FROM quiz WHERE {public_sql})
            """)).scalar_one()
        except Exception:
            return {
                'backend': dialect,
                'content_schema_available': False,
                'index_available': False,
                'expected_count': 0,
                'actual_count': 0,
                'missing_count': 0,
                'orphan_count': 0,
                'stale_count': 0,
                'duplicate_count': 0,
                'sample_limit': limit,
                'missing': [],
                'orphan': [],
                'stale': [],
            }
        try:
            actual_count = connection.execute(
                text(f'SELECT COUNT(*) FROM {table}')
            ).scalar_one()
        except Exception:
            return {
                'backend': dialect,
                'content_schema_available': True,
                'index_available': False,
                'expected_count': int(expected_count),
                'actual_count': 0,
                'missing_count': int(expected_count),
                'orphan_count': 0,
                'stale_count': 0,
                'sample_limit': limit,
                'duplicate_count': 0,
                'missing': [],
                'orphan': [],
                'stale': [],
            }

        duplicate_count = connection.execute(text(f"""
            SELECT COUNT(*) - COUNT(DISTINCT item_type || ':' || item_id)
            FROM {table}
        """)).scalar_one()

        orphan_count = connection.execute(text(f"""
            SELECT COUNT(*) FROM {table} i
            WHERE (i.item_type = 'deck' AND NOT EXISTS (
                SELECT 1 FROM deck d
                WHERE d.{public_sql} AND {id_expression} = {deck_id_expression}
            ))
            OR (i.item_type = 'quiz' AND NOT EXISTS (
                SELECT 1 FROM quiz q
                WHERE q.{public_sql} AND {id_expression} = {quiz_id_expression}
            ))
            OR i.item_type NOT IN ('deck', 'quiz')
        """)).scalar_one()
        missing_count = connection.execute(text(f"""
            SELECT
                (SELECT COUNT(*) FROM deck d
                 WHERE d.{public_sql} AND NOT EXISTS (
                    SELECT 1 FROM {table} i
                    WHERE i.item_type = 'deck' AND i.item_id = {deck_id_expression}
                 )) +
                (SELECT COUNT(*) FROM quiz q
                 WHERE q.{public_sql} AND NOT EXISTS (
                    SELECT 1 FROM {table} i
                    WHERE i.item_type = 'quiz' AND i.item_id = {quiz_id_expression}
                 ))
        """)).scalar_one()
        stale_count = connection.execute(text(f"""
            SELECT
                (SELECT COUNT(*) FROM {table} i JOIN deck d
                 ON i.item_type = 'deck' AND i.item_id = {deck_id_expression}
                 WHERE d.{public_sql} AND (
                    i.title <> COALESCE(d.description, '') OR
                    i.description <> COALESCE(d.detailed_description, '') OR
                    i.tags <> COALESCE(d.tags, '')
                 )) +
                (SELECT COUNT(*) FROM {table} i JOIN quiz q
                 ON i.item_type = 'quiz' AND i.item_id = {quiz_id_expression}
                 WHERE q.{public_sql} AND (
                    i.title <> COALESCE(q.title, '') OR
                    i.description <> COALESCE(q.description, '') OR
                    i.tags <> COALESCE(q.tags, '')
                 ))
        """)).scalar_one()

        missing = [
            dict(row)
            for row in connection.execute(text(f"""
                SELECT 'deck' AS item_type, d.deck_id AS item_id
                FROM deck d
                WHERE d.{public_sql} AND NOT EXISTS (
                    SELECT 1 FROM {table} i
                    WHERE i.item_type = 'deck' AND i.item_id = {deck_id_expression}
                )
                UNION ALL
                SELECT 'quiz' AS item_type, q.quiz_id AS item_id
                FROM quiz q
                WHERE q.{public_sql} AND NOT EXISTS (
                    SELECT 1 FROM {table} i
                    WHERE i.item_type = 'quiz' AND i.item_id = {quiz_id_expression}
                )
                LIMIT :limit
            """), {'limit': limit}).mappings()
        ]
        orphan = [
            dict(row)
            for row in connection.execute(text(f"""
                SELECT i.item_type, i.item_id
                FROM {table} i
                WHERE (i.item_type = 'deck' AND NOT EXISTS (
                    SELECT 1 FROM deck d
                    WHERE d.{public_sql} AND {id_expression} = {deck_id_expression}
                ))
                OR (i.item_type = 'quiz' AND NOT EXISTS (
                    SELECT 1 FROM quiz q
                    WHERE q.{public_sql} AND {id_expression} = {quiz_id_expression}
                ))
                OR i.item_type NOT IN ('deck', 'quiz')
                LIMIT :limit
            """), {'limit': limit}).mappings()
        ]
        stale = [
            dict(row)
            for row in connection.execute(text(f"""
                SELECT i.item_type, i.item_id
                FROM {table} i JOIN deck d
                ON i.item_type = 'deck' AND i.item_id = {deck_id_expression}
                WHERE d.{public_sql} AND (
                    i.title <> COALESCE(d.description, '') OR
                    i.description <> COALESCE(d.detailed_description, '') OR
                    i.tags <> COALESCE(d.tags, '')
                )
                UNION ALL
                SELECT i.item_type, i.item_id
                FROM {table} i JOIN quiz q
                ON i.item_type = 'quiz' AND i.item_id = {quiz_id_expression}
                WHERE q.{public_sql} AND (
                    i.title <> COALESCE(q.title, '') OR
                    i.description <> COALESCE(q.description, '') OR
                    i.tags <> COALESCE(q.tags, '')
                )
                LIMIT :limit
            """), {'limit': limit}).mappings()
        ]

    return {
        'backend': dialect,
        'content_schema_available': True,
        'index_available': True,
        'expected_count': int(expected_count),
        'actual_count': int(actual_count),
        'missing_count': int(missing_count),
        'orphan_count': int(orphan_count),
        'stale_count': int(stale_count),
        'duplicate_count': int(duplicate_count),
        'sample_limit': limit,
        'missing': missing,
        'orphan': orphan,
        'stale': stale,
    }


def _fallback_search_public_content(query_text, limit=DEFAULT_PAGE_SIZE, offset=0):
    search_term = f"%{query_text}%"
    deck_query = _deck_query_with_card_counts().filter(
        Deck.is_public == True,
        db.or_(Deck.description.ilike(search_term), Deck.detailed_description.ilike(search_term), Deck.tags.ilike(search_term)),
    ).order_by(Deck.deck_id.asc())
    quiz_query = _quiz_query_with_question_counts().filter(
        Quiz.is_public == True,
        db.or_(Quiz.title.ilike(search_term), Quiz.description.ilike(search_term), Quiz.tags.ilike(search_term)),
    ).order_by(Quiz.quiz_id.asc())
    deck_count = deck_query.count()
    quiz_count = quiz_query.count()
    total = deck_count + quiz_count
    rows_remaining = limit
    deck_rows = []
    quiz_rows = []
    if offset < deck_count:
        deck_rows = deck_query.limit(rows_remaining).offset(offset).all()
        rows_remaining -= len(deck_rows)
        if rows_remaining:
            quiz_rows = quiz_query.limit(rows_remaining).all()
    else:
        quiz_rows = quiz_query.limit(rows_remaining).offset(offset - deck_count).all()
    return _attach_deck_card_counts(deck_rows), _attach_quiz_question_counts(quiz_rows), total > offset + limit


def search_public_content(query_text, limit=DEFAULT_PAGE_SIZE, page=1, user_id=None):
    query_text = (query_text or '').strip()
    page, limit = _bounded_page(page, limit)
    offset = (page - 1) * limit
    if not query_text:
        return {'decks': [], 'quizzes': [], 'has_exact_match': False, 'query_tokens': [], 'expanded_tokens': [], 'pagination': _search_pagination(page, limit, False)}

    fts_query, query_tokens = _build_fts_query(query_text)
    if not query_tokens:
        return {'decks': [], 'quizzes': [], 'has_exact_match': False, 'query_tokens': [], 'expanded_tokens': [], 'pagination': _search_pagination(page, limit, False)}

    deck_results = []
    quiz_results = []
    has_exact_match = False

    try:
        if _is_sqlite_backend():
            # SQLite FTS5 path with bm25 ranking and snippets.
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
                    ORDER BY rank ASC, item_type ASC, item_id ASC
                    LIMIT :limit
                    OFFSET :offset
                """),
                {'match_query': fts_query, 'limit': limit + 1, 'offset': offset}
            ).fetchall()
        else:
            # Postgres full-text path with weighted ranking and highlighted snippets.
            results = db.session.execute(
                text("""
                    SELECT
                        item_type,
                        item_id,
                        ts_rank_cd(
                            search_vector,
                            websearch_to_tsquery('english', :query_text)
                        ) AS rank,
                        ts_headline('english', title, websearch_to_tsquery('english', :query_text), 'StartSel=[,StopSel=],MaxWords=10,MinWords=2') AS title_snippet,
                        ts_headline('english', description, websearch_to_tsquery('english', :query_text), 'StartSel=[,StopSel=],MaxWords=12,MinWords=3') AS description_snippet,
                        ts_headline('english', tags, websearch_to_tsquery('english', :query_text), 'StartSel=[,StopSel=],MaxWords=10,MinWords=2') AS tags_snippet
                    FROM public_content_search
                    WHERE search_vector @@ websearch_to_tsquery('english', :query_text)
                    ORDER BY rank DESC, item_type ASC, item_id ASC
                    LIMIT :limit
                    OFFSET :offset
                """),
                {'query_text': query_text, 'limit': limit + 1, 'offset': offset}
            ).fetchall()

        has_next = len(results) > limit
        results = results[:limit]

        deck_ids = [int(row[1]) for row in results if row[0] == 'deck']
        quiz_ids = [int(row[1]) for row in results if row[0] == 'quiz']

        deck_rows = {}
        if deck_ids:
            deck_rows = {
                row.deck_id: row
                for row in db.session.query(
                    Deck.deck_id,
                    Deck.owned_by,
                    Deck.description,
                    Deck.detailed_description,
                    Deck.tags,
                    Deck.sortable,
                    Deck.is_public,
                    db.func.count(Card.card_id).label('card_count'),
                )
                .outerjoin(Card, Card.deck_id == Deck.deck_id)
                .filter(Deck.deck_id.in_(deck_ids))
                .group_by(
                    Deck.deck_id,
                    Deck.owned_by,
                    Deck.description,
                    Deck.detailed_description,
                    Deck.tags,
                    Deck.sortable,
                    Deck.is_public,
                )
                .all()
            }

        quiz_rows = {}
        if quiz_ids:
            quiz_rows = {
                row.quiz_id: row
                for row in db.session.query(
                    Quiz.quiz_id,
                    Quiz.owned_by,
                    Quiz.title,
                    Quiz.description,
                    Quiz.tags,
                    Quiz.is_public,
                    db.func.count(QuizQuestion.question_id).label('question_count'),
                )
                .outerjoin(QuizQuestion, QuizQuestion.quiz_id == Quiz.quiz_id)
                .filter(Quiz.quiz_id.in_(quiz_ids))
                .group_by(
                    Quiz.quiz_id,
                    Quiz.owned_by,
                    Quiz.title,
                    Quiz.description,
                    Quiz.tags,
                    Quiz.is_public,
                )
                .all()
            }

        for row in results:
            item_type = row[0]
            item_id = int(row[1])
            rank_value = float(row[2]) if row[2] is not None else 0.0
            score = round((-rank_value if _is_sqlite_backend() else rank_value), 4)
            reasons = []
            if row[3]:
                reasons.append(f"title: {row[3]}")
            if row[4]:
                reasons.append(f"description: {row[4]}")
            if row[5]:
                reasons.append(f"tags: {row[5]}")

            if item_type == 'deck':
                deck = deck_rows.get(item_id)
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
                    'card_count': int(deck.card_count or 0),
                    'score': score,
                    'match_reasons': reasons,
                })
            elif item_type == 'quiz':
                quiz = quiz_rows.get(item_id)
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
                    'question_count': int(quiz.question_count or 0),
                    'score': score,
                    'match_reasons': reasons,
                })

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception('public_search_query_failed query=%s', query_text, exc_info=exc)
        # Fall back to simple LIKE search if FTS fails.
        decks, quizzes, has_next = _fallback_search_public_content(query_text, limit=limit, offset=offset)
        deck_results = [{
            'deck_id': deck.deck_id,
            'owned_by': deck.owned_by,
            'is_owned': bool(user_id is not None and deck.owned_by == user_id),
            'description': deck.description,
            'detailed_description': deck.detailed_description,
            'tags': deck.tags,
            'sortable': deck.sortable,
            'is_public': deck.is_public,
            'card_count': int(getattr(deck, 'card_count', 0) or 0),
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
            'question_count': int(getattr(quiz, 'question_count', 0) or 0),
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
        'pagination': _search_pagination(page, limit, has_next),
    }


def _search_pagination(page, per_page, has_next):
    return {
        'page': page,
        'per_page': per_page,
        'has_prev': page > 1,
        'has_next': has_next,
        'prev_page': page - 1 if page > 1 else None,
        'next_page': page + 1 if has_next else None,
    }

# Card and answer helpers.
def add_card(deck_id, question, answers):
    # Positions are 1-based within each deck.
    question, answers = _validate_card_payload(question, answers)
    last_error = None
    for _ in range(ORDER_MUTATION_RETRIES):
        db.session.rollback()
        deck = _locked_deck(deck_id)
        if not deck:
            return None
        max_position = db.session.query(db.func.max(Card.position)).filter_by(deck_id=deck_id).scalar() or 0
        card = Card(deck_id=deck_id, question=question, position=max_position + 1)
        db.session.add(card)
        try:
            db.session.flush()
            db.session.add_all([CardAnswer(card_id=card.card_id, answer=answer_text) for answer_text in answers])
            db.session.commit()
            return card
        except (IntegrityError, OperationalError) as exc:
            last_error = exc
            db.session.rollback()
            time.sleep(0.01)
    raise ValueError('That card could not be added because another change is in progress.') from last_error


def add_answer_to_card(card_id, answer):
    card = db.session.get(Card, card_id)
    if card:
        card_answer = CardAnswer(card_id=card_id, answer=answer)
        db.session.add(card_answer)
        db.session.commit()
        return card_answer
    return None


def delete_answer(answer_id):
    # Remove the card if its last answer disappears.
    answer = db.session.get(CardAnswer, answer_id)
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


def delete_card(card_id):
    card = db.session.get(Card, card_id)
    if card:
        deck_id = card.deck_id
        _locked_deck(deck_id)
        db.session.delete(card)
        db.session.flush()
        _renumber_deck_cards(deck_id)
        _commit_domain_error('That card could not be deleted. Please try again.')
        return True
    return False


def edit_card(card_id, question, answers):
    card = db.session.get(Card, card_id)
    if card:
        answers = _normalize_answers(answers)
        if not answers:
            deck_id = card.deck_id
            _locked_deck(deck_id)
            db.session.delete(card)
            db.session.flush()
            _renumber_deck_cards(deck_id)
            _commit_domain_error('That card could not be updated. Please try again.')
            return {'deleted': True, 'card_id': card_id, 'deck_id': deck_id}

        question, answers = _validate_card_payload(question, answers)
        card.question = question

        # Replace the answer set in one pass.
        CardAnswer.query.filter_by(card_id=card_id).delete()
        for answer_text in answers:
            card_answer = CardAnswer(card_id=card_id, answer=answer_text)
            db.session.add(card_answer)
        db.session.commit()
        return card
    return None


def get_card_from_deck(card_id, detailed=False):
    card = Card.query.options(selectinload(Card.answers)).filter(
        Card.card_id == card_id
    ).first()
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


def get_deck_study_data(deck_id, shuffle=True):
    deck = get_deck_with_content(deck_id)
    if not deck:
        return None

    return _serialize_deck(deck, detailed_cards=True, shuffle_cards=shuffle, shuffle_answers=shuffle)


def get_match_strategy_catalog(include_account_only=True):
    catalog = [
        {'value': 'standard_shuffle', 'label': 'Standard', 'description': 'Shuffle the deck and clear the board in balanced batches.', 'requires_account': False},
        {'value': 'retry_misses', 'label': 'Retry Misses', 'description': 'Bring missed pairs back for another pass after the current round.', 'requires_account': False},
        {'value': 'progressive_build', 'label': 'Progressive Difficulty', 'description': 'Start with fewer pairs and add more as you clear each batch.', 'requires_account': False},
        {'value': 'reverse_pressure', 'label': 'Reverse Pressure', 'description': 'Show one question at a time with several answer choices, then rotate to the next one.', 'requires_account': False},
        {'value': 'timed_recovery', 'label': 'Recovery Sprint', 'description': 'Play a normal round first, then race through your missed questions one at a time against the clock.', 'requires_account': False},
        {'value': 'weakest_first', 'label': 'Weakest First', 'description': 'Move your hardest pairs to the front based on past mistakes.', 'requires_account': True},
        {'value': 'mastery_mix', 'label': 'Mastery Mix', 'description': 'Blend weak pairs, fresh pairs, and retries into one steady rotation.', 'requires_account': True},
    ]
    if include_account_only:
        return catalog
    return [strategy for strategy in catalog if not strategy.get('requires_account')]


def normalize_match_strategy(strategy, include_account_only=True):
    requested = (strategy or '').strip().lower()
    allowed = {item['value'] for item in get_match_strategy_catalog(include_account_only=include_account_only)}
    return requested if requested in allowed else 'standard_shuffle'


def _get_match_progress_by_answer(user_id, answer_ids):
    if not user_id or not answer_ids:
        return {}
    rows = MatchPairProgress.query.filter(
        MatchPairProgress.user_id == user_id,
        MatchPairProgress.answer_id.in_(answer_ids)
    ).all()
    return {row.answer_id: row for row in rows}


def _match_pair_weight(answer_payload):
    return (
        (answer_payload.get('incorrect_count') or 0) * 3
        - (answer_payload.get('correct_count') or 0)
        + (2 if answer_payload.get('last_outcome') == 'incorrect' else 0)
    )


def _match_card_weight(card_payload):
    if not card_payload.get('answer_objects'):
        return 0
    return max(_match_pair_weight(answer) for answer in card_payload['answer_objects'])


def _interleave_match_groups(groups):
    queues = [list(group) for group in groups if group]
    ordered = []
    while queues:
        next_queues = []
        for queue in queues:
            if queue:
                ordered.append(queue.pop(0))
            if queue:
                next_queues.append(queue)
        queues = next_queues
    return ordered


def _build_match_question_order(cards, strategy):
    if strategy == 'weakest_first':
        return sorted(cards, key=lambda card: (-card.get('match_weight', 0), card.get('position', 0), card.get('card_id', 0)))

    if strategy == 'mastery_mix':
        weak = [card for card in cards if card.get('match_weight', 0) > 0]
        fresh = [card for card in cards if card.get('total_attempts', 0) == 0]
        steady = [card for card in cards if card not in weak and card not in fresh]
        random.shuffle(weak)
        random.shuffle(fresh)
        random.shuffle(steady)
        return _interleave_match_groups([weak, fresh, steady, weak])

    randomized = list(cards)
    random.shuffle(randomized)
    return randomized


def get_match_game_data(user_id, deck_id, strategy='standard_shuffle'):
    deck = get_deck_with_content(deck_id)
    if not deck:
        return None

    serialized = _serialize_deck(deck, detailed_cards=True, shuffle_cards=False, shuffle_answers=False)
    cards = serialized['cards']
    answer_ids = [answer['answer_id'] for card in cards for answer in card.get('answer_objects', [])]
    progress_by_answer_id = _get_match_progress_by_answer(user_id, answer_ids)

    answers = []
    for card in cards:
        enriched_answers = []
        for answer in card.get('answer_objects', []):
            progress = progress_by_answer_id.get(answer['answer_id'])
            enriched_answer = {
                **answer,
                'card_id': card['card_id'],
                'correct_count': progress.correct_count if progress else 0,
                'incorrect_count': progress.incorrect_count if progress else 0,
                'last_outcome': progress.last_outcome if progress else None,
            }
            enriched_answer['attempt_count'] = enriched_answer['correct_count'] + enriched_answer['incorrect_count']
            enriched_answer['match_weight'] = _match_pair_weight(enriched_answer)
            enriched_answers.append(enriched_answer)
            answers.append(enriched_answer)
        card['answer_objects'] = enriched_answers
        card['answer_count'] = len(enriched_answers)
        card['total_attempts'] = sum(answer['attempt_count'] for answer in enriched_answers)
        card['incorrect_count'] = sum(answer['incorrect_count'] for answer in enriched_answers)
        card['match_weight'] = _match_card_weight(card)

    normalized_strategy = normalize_match_strategy(strategy)
    ordered_cards = _build_match_question_order(cards, normalized_strategy)

    return {
        'deck_id': serialized['deck_id'],
        'description': serialized['description'],
        'detailed_description': serialized.get('detailed_description'),
        'tags': serialized.get('tags'),
        'card_count': serialized['card_count'],
        'answer_count': serialized['answer_count'],
        'cards': ordered_cards,
        'answers': answers,
        'strategy': normalized_strategy,
    }


def record_match_attempt(user_id, answer_id, is_correct):
    if not user_id or not answer_id:
        return
    for _ in range(2):
        progress = MatchPairProgress.query.filter_by(user_id=user_id, answer_id=answer_id).first()
        if not progress:
            try:
                progress = MatchPairProgress(user_id=user_id, answer_id=answer_id, correct_count=0, incorrect_count=0)
                db.session.add(progress)
                db.session.flush()
            except IntegrityError:
                db.session.rollback()
                continue
        if is_correct:
            updated = MatchPairProgress.query.filter_by(user_id=user_id, answer_id=answer_id).update(
                {
                    MatchPairProgress.correct_count: MatchPairProgress.correct_count + 1,
                    MatchPairProgress.last_outcome: 'correct',
                },
                synchronize_session=False,
            )
        else:
            updated = MatchPairProgress.query.filter_by(user_id=user_id, answer_id=answer_id).update(
                {
                    MatchPairProgress.incorrect_count: MatchPairProgress.incorrect_count + 1,
                    MatchPairProgress.last_outcome: 'incorrect',
                },
                synchronize_session=False,
            )
        if updated:
            db.session.commit()
            return
        db.session.rollback()

    current_app.logger.warning('match_progress_update_skipped user_id=%s answer_id=%s', user_id, answer_id)


def check_deck_order(deck_id, ordered_card_ids):
    """Validate a user-submitted card order against stored card positions."""
    deck = Deck.query.options(selectinload(Deck.cards)).filter(
        Deck.deck_id == deck_id
    ).first()
    if not deck:
        return {'valid': False, 'error': 'Deck not found'}
    if not deck.sortable:
        return {'valid': False, 'error': 'Deck is not sorted'}

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


def move_card_in_deck(card_id, direction):
    """Move a card up or down within its deck by swapping position with a neighbor."""
    if direction not in ('up', 'down'):
        return {'success': False, 'error': 'Invalid direction'}
    last_error = None
    for _ in range(ORDER_MUTATION_RETRIES):
        db.session.rollback()
        card = db.session.get(Card, card_id)
        if not card:
            return {'success': False, 'error': 'Card not found'}
        deck = _locked_deck(card.deck_id)
        if not deck.sortable:
            return {'success': False, 'error': 'Card order can only be changed in sorted decks'}
        deck_cards = Card.query.filter_by(deck_id=deck.deck_id).order_by(Card.position, Card.card_id).all()
        current_index = next((index for index, deck_card in enumerate(deck_cards) if deck_card.card_id == card_id), None)
        if current_index is None:
            return {'success': False, 'error': 'Card not found in deck'}
        target_index = current_index - 1 if direction == 'up' else current_index + 1
        if target_index < 0 or target_index >= len(deck_cards):
            return {'success': True, 'moved': False, 'deck_id': deck.deck_id}
        try:
            _swap_positions(deck_cards[current_index], deck_cards[target_index])
            db.session.commit()
            return {'success': True, 'moved': True, 'deck_id': deck.deck_id}
        except (IntegrityError, OperationalError) as exc:
            last_error = exc
            db.session.rollback()
            time.sleep(0.01)
    return {'success': False, 'error': 'Card order changed concurrently; please try again.'}


def swap_cards_in_deck(card_id, target_card_id):
    """Swap two cards in the same sortable deck."""
    for _ in range(ORDER_MUTATION_RETRIES):
        db.session.rollback()
        first_card = db.session.get(Card, card_id)
        second_card = db.session.get(Card, target_card_id)
        if not first_card or not second_card:
            return {'success': False, 'error': 'One or more cards were not found'}
        if first_card.deck_id != second_card.deck_id:
            return {'success': False, 'error': 'Cards must be in the same deck'}
        deck = _locked_deck(first_card.deck_id)
        if not deck.sortable:
            return {'success': False, 'error': 'Card order can only be changed in sorted decks'}
        if first_card.card_id == second_card.card_id:
            return {'success': True, 'swapped': False, 'deck_id': deck.deck_id}
        try:
            _swap_positions(first_card, second_card)
            db.session.commit()
            return {'success': True, 'swapped': True, 'deck_id': deck.deck_id}
        except (IntegrityError, OperationalError):
            db.session.rollback()
            time.sleep(0.01)
    return {'success': False, 'error': 'Card order changed concurrently; please try again.'}


def reorder_cards_in_deck(deck_id, ordered_card_ids):
    """Persist a complete order atomically for administrative/import callers."""
    if not isinstance(ordered_card_ids, list):
        return {'success': False, 'error': 'Card order must be a list'}
    for _ in range(ORDER_MUTATION_RETRIES):
        db.session.rollback()
        deck = _locked_deck(deck_id)
        if not deck:
            return {'success': False, 'error': 'Deck not found'}
        cards = Card.query.filter_by(deck_id=deck_id).order_by(Card.position, Card.card_id).all()
        by_id = {card.card_id: card for card in cards}
        if len(ordered_card_ids) != len(cards) or set(ordered_card_ids) != set(by_id):
            return {'success': False, 'error': 'Submitted order does not include all cards'}
        temporary_base = max([card.position for card in cards] + [len(cards)]) + len(cards) + 1
        try:
            for offset, card in enumerate(cards):
                card.position = temporary_base + offset
            db.session.flush()
            for position, card_id in enumerate(ordered_card_ids, start=1):
                by_id[card_id].position = position
            db.session.commit()
            return {'success': True, 'deck_id': deck_id}
        except (IntegrityError, OperationalError):
            db.session.rollback()
            time.sleep(0.01)
    return {'success': False, 'error': 'Card order changed concurrently; please try again.'}

# Read-only deck access helpers.
def list_cards_from_deck(deck_id, detailed=False, shuffle=False):
    deck = get_deck_with_content(deck_id)
    if not deck:
        return []
    return _serialize_deck(deck, detailed_cards=detailed, shuffle_cards=shuffle, shuffle_answers=False)['cards']


def get_deck_details(deck_id, shuffle_cards=False, shuffle_answers=False):
    deck = get_deck_with_content(deck_id)
    if not deck:
        return None
    return _serialize_deck(deck, detailed_cards=True, shuffle_cards=shuffle_cards, shuffle_answers=shuffle_answers)


# Mastery mode helpers.
def _mastery_status_for_rating(rating):
    if rating == 'understood':
        return 'mastered'
    if rating == 'still_learning':
        return 'learning'
    return 'unknown'


def get_mastery_strategy_catalog():
    return [
        {'value': 'linear', 'label': 'Linear', 'requires_sortable': True, 'description': 'Follow the saved deck order from start to finish.'},
        {'value': 'weakest_first', 'label': 'Weakest First', 'requires_sortable': False, 'description': 'Prioritize cards you miss most often or struggle with most.'},
        {'value': 'spaced', 'label': 'Spaced', 'requires_sortable': False, 'description': 'Harder cards come back sooner and stronger cards wait longer.'},
        {'value': 'mastery_mix', 'label': 'Mastery Mix', 'requires_sortable': False, 'description': 'Blend weak, learning, and newer cards into one balanced queue.'},
        {'value': 'random', 'label': 'Random', 'requires_sortable': False, 'description': 'Shuffle the remaining cards into a random order.'},
    ]


def normalize_mastery_strategy(strategy, deck_sortable=False):
    requested = (strategy or '').strip().lower()
    allowed = {'weakest_first', 'spaced', 'mastery_mix', 'random'}
    if deck_sortable:
        allowed.add('linear')
    return requested if requested in allowed else 'spaced'


def _mastery_card_priority_bucket(card_payload):
    if (card_payload.get('dont_know_count') or 0) > 0 or card_payload.get('last_rating') == 'dont_know':
        return 0
    if card_payload.get('status') == 'learning' or (card_payload.get('learning_count') or 0) > 0:
        return 1
    if (card_payload.get('reviewed_count') or 0) == 0:
        return 2
    return 3


def _order_mastery_cards_by_weakest(card_payloads):
    return sorted(
        card_payloads,
        key=lambda card: (
            _mastery_card_priority_bucket(card),
            -(card.get('dont_know_count') or 0),
            -(card.get('learning_count') or 0),
            card.get('understood_count') or 0,
            card.get('reviewed_count') or 0,
            card.get('position') or 0,
            card.get('card_id') or 0,
        ),
    )


def _mastery_spacing_hours(card_payload):
    reviewed_count = card_payload.get('reviewed_count') or 0
    if reviewed_count == 0:
        return 0.0
    if card_payload.get('last_rating') == 'dont_know':
        return 0.25
    if card_payload.get('status') == 'learning' or card_payload.get('last_rating') == 'still_learning':
        return min(72.0, 6.0 * (2 ** min(card_payload.get('learning_count') or 0, 4)))
    return min(96.0, 12.0 * (2 ** min(card_payload.get('understood_count') or 0, 3)))


def _order_mastery_cards_by_spaced(card_payloads):
    now = datetime.now(timezone.utc)

    def due_key(card):
        updated_at = card.get('updated_at')
        if updated_at is not None:
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            elapsed_hours = max(0.0, (now - updated_at).total_seconds() / 3600.0)
        else:
            elapsed_hours = 999999.0

        target_hours = _mastery_spacing_hours(card)
        due_ratio = float('inf') if target_hours == 0 else elapsed_hours / target_hours

        return (
            -due_ratio,
            _mastery_card_priority_bucket(card),
            -(card.get('dont_know_count') or 0),
            -(card.get('learning_count') or 0),
            card.get('position') or 0,
            card.get('card_id') or 0,
        )

    return sorted(card_payloads, key=due_key)


def _order_mastery_cards_by_mix(card_payloads):
    weakest_sorted = _order_mastery_cards_by_weakest(card_payloads)
    weak = []
    learning = []
    fresh = []
    review = []

    for card in weakest_sorted:
        bucket = _mastery_card_priority_bucket(card)
        if bucket == 0:
            weak.append(card)
        elif bucket == 1:
            learning.append(card)
        elif bucket == 2:
            fresh.append(card)
        else:
            review.append(card)

    queue = []
    pattern = [weak, learning, fresh, weak, review]
    while any(pattern_group for pattern_group in (weak, learning, fresh, review)):
        appended = False
        for group in pattern:
            if group:
                queue.append(group.pop(0))
                appended = True
        if not appended:
            break
    return queue


def order_mastery_cards(card_payloads, strategy, deck_sortable=False):
    normalized_strategy = normalize_mastery_strategy(strategy, deck_sortable=deck_sortable)
    cards = list(card_payloads)

    if normalized_strategy == 'linear':
        return sorted(cards, key=lambda card: (card.get('position') or 0, card.get('card_id') or 0))
    if normalized_strategy == 'weakest_first':
        return _order_mastery_cards_by_weakest(cards)
    if normalized_strategy == 'spaced':
        return _order_mastery_cards_by_spaced(cards)
    if normalized_strategy == 'mastery_mix':
        return _order_mastery_cards_by_mix(cards)
    if normalized_strategy == 'random':
        random.shuffle(cards)
        return cards
    return _order_mastery_cards_by_spaced(cards)


def get_mastery_snapshot(user_id, deck_id, strategy='spaced'):
    deck = get_deck_with_content(deck_id)
    if not deck:
        return None

    cards = list(deck.cards)
    cards.sort(key=lambda card: card.position)
    if not cards:
        return {
            'deck': deck,
            'cards': [],
            'queue': [],
            'current_card': None,
            'strategy': normalize_mastery_strategy(strategy, deck_sortable=deck.sortable),
            'stats': {
                'total': 0,
                'mastered': 0,
                'learning': 0,
                'unknown': 0,
                'remaining': 0,
            }
        }

    card_ids = [card.card_id for card in cards]
    progress_rows = CardMasteryProgress.query.filter(
        CardMasteryProgress.user_id == user_id,
        CardMasteryProgress.card_id.in_(card_ids)
    ).all()
    progress_by_card_id = {row.card_id: row for row in progress_rows}

    card_payloads = []
    mastered_count = 0
    learning_count = 0
    unknown_count = 0
    remaining_cards = []
    for card in cards:
        progress = progress_by_card_id.get(card.card_id)
        status = progress.status if progress else 'new'
        if status == 'mastered':
            mastered_count += 1
        elif status == 'learning':
            learning_count += 1
        else:
            unknown_count += 1
        if status != 'mastered':
            remaining_cards.append(card)
        card_payloads.append({
            'card_id': card.card_id,
            'question': card.question,
            'answers': [answer.answer for answer in card.answers],
            'position': card.position,
            'status': status,
            'reviewed_count': progress.reviewed_count if progress else 0,
            'understood_count': progress.understood_count if progress else 0,
            'learning_count': progress.learning_count if progress else 0,
            'dont_know_count': progress.dont_know_count if progress else 0,
            'last_rating': progress.last_rating if progress else None,
            'updated_at': progress.updated_at if progress else None,
        })

    normalized_strategy = normalize_mastery_strategy(strategy, deck_sortable=deck.sortable)
    remaining_payloads = [payload for payload in card_payloads if payload['status'] != 'mastered']
    queue_payloads = order_mastery_cards(remaining_payloads, normalized_strategy, deck_sortable=deck.sortable)
    current_payload = queue_payloads[0] if queue_payloads else None

    return {
        'deck': deck,
        'cards': card_payloads,
        'queue': queue_payloads,
        'current_card': current_payload,
        'strategy': normalized_strategy,
        'stats': {
            'total': len(cards),
            'mastered': mastered_count,
            'learning': learning_count,
            'unknown': unknown_count,
            'remaining': len(remaining_cards),
        }
    }


def record_mastery_rating(user_id, deck_id, card_id, rating):
    card = db.session.get(Card, card_id)
    if not card or card.deck_id != deck_id:
        return {'success': False, 'error': 'Card not found in deck'}

    if rating not in ('understood', 'still_learning', 'dont_know'):
        return {'success': False, 'error': 'Invalid rating'}

    for _ in range(2):
        progress = CardMasteryProgress.query.filter_by(user_id=user_id, card_id=card_id).first()
        if not progress:
            try:
                progress = CardMasteryProgress(
                    user_id=user_id,
                    card_id=card_id,
                    status='new',
                    understood_count=0,
                    learning_count=0,
                    dont_know_count=0,
                    reviewed_count=0,
                )
                db.session.add(progress)
                db.session.flush()
            except IntegrityError:
                db.session.rollback()
                continue

        updates = {
            CardMasteryProgress.reviewed_count: CardMasteryProgress.reviewed_count + 1,
            CardMasteryProgress.last_rating: rating,
            CardMasteryProgress.status: _mastery_status_for_rating(rating),
        }
        if rating == 'understood':
            updates[CardMasteryProgress.understood_count] = CardMasteryProgress.understood_count + 1
        elif rating == 'still_learning':
            updates[CardMasteryProgress.learning_count] = CardMasteryProgress.learning_count + 1
        else:
            updates[CardMasteryProgress.dont_know_count] = CardMasteryProgress.dont_know_count + 1

        updated = CardMasteryProgress.query.filter_by(user_id=user_id, card_id=card_id).update(
            updates,
            synchronize_session=False,
        )
        if updated:
            db.session.commit()
            return {'success': True}
        db.session.rollback()

    current_app.logger.warning('mastery_progress_update_skipped user_id=%s card_id=%s', user_id, card_id)
    return {'success': False, 'error': 'Could not save progress right now.'}


def reset_mastery_progress(user_id, deck_id):
    card_ids_query = db.session.query(Card.card_id).filter(Card.deck_id == deck_id)
    deleted_rows = CardMasteryProgress.query.filter(
        CardMasteryProgress.user_id == user_id,
        CardMasteryProgress.card_id.in_(card_ids_query)
    ).delete(synchronize_session=False)
    db.session.commit()
    return deleted_rows


def _quiz_attempt_cutoff(max_age_seconds=None):
    if max_age_seconds is None:
        max_age_seconds = current_app.config['QUIZ_ATTEMPT_MAX_AGE_SECONDS']
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=max_age_seconds)


def delete_expired_quiz_attempts(max_age_seconds=None):
    cutoff = _quiz_attempt_cutoff(max_age_seconds=max_age_seconds)
    deleted_rows = QuizAttempt.query.filter(
        QuizAttempt.created_at < cutoff
    ).delete(synchronize_session=False)
    db.session.commit()
    return deleted_rows


def create_quiz_attempt(user_id, session_id, quiz_questions):
    """Create one bounded attempt and return browser-safe questions."""
    if user_id is None and not session_id:
        raise ValueError('Anonymous quiz attempts require a session identifier.')
    max_questions = max(1, current_app.config['MAX_QUIZ_QUESTIONS'])
    max_active_attempts = max(1, current_app.config['MAX_ACTIVE_QUIZ_ATTEMPTS'])
    quiz_questions = list(quiz_questions)
    if len(quiz_questions) > max_questions:
        quiz_questions = random.sample(quiz_questions, max_questions)
    if not quiz_questions:
        return None, [], []

    cutoff = _quiz_attempt_cutoff()
    QuizAttempt.query.filter(QuizAttempt.created_at < cutoff).delete(synchronize_session=False)

    if user_id is not None:
        owner_query = QuizAttempt.query.filter(QuizAttempt.user_id == user_id)
    else:
        owner_query = QuizAttempt.query.filter(
            QuizAttempt.user_id.is_(None),
            QuizAttempt.session_id == session_id,
        )

    keep_count = max_active_attempts - 1
    kept_tokens = []
    if keep_count:
        kept_tokens = [
            token
            for (token,) in owner_query.with_entities(QuizAttempt.attempt_token)
            .order_by(QuizAttempt.created_at.desc(), QuizAttempt.attempt_token.desc())
            .limit(keep_count)
            .all()
        ]

    displaced_query = owner_query
    if kept_tokens:
        displaced_query = displaced_query.filter(
            ~QuizAttempt.attempt_token.in_(kept_tokens)
        )
    displaced_query.delete(synchronize_session=False)

    correct_answers = {
        str(question['id']): [
            option['text'] for option in question['options'] if option.get('is_correct')
        ]
        for question in quiz_questions
    }
    attempt = QuizAttempt(
        attempt_token=secrets.token_urlsafe(32),
        user_id=user_id,
        session_id=session_id,
        correct_answers_json=json.dumps(correct_answers),
        question_count=len(quiz_questions),
    )
    db.session.add(attempt)
    db.session.commit()

    display_questions = [
        {
            'id': question['id'],
            'question': question['question'],
            'options': [{'text': option['text']} for option in question['options']],
        }
        for question in quiz_questions
    ]
    active_tokens = list(reversed(kept_tokens)) + [attempt.attempt_token]
    return attempt.attempt_token, display_questions, active_tokens


def score_quiz_attempt(attempt_token, user_id, session_id, submitted_answers):
    """Consume one rendered quiz attempt and calculate its result from server-held data."""
    attempt = db.session.get(QuizAttempt, attempt_token)
    if not attempt or attempt.user_id != user_id:
        return None
    if user_id is None and attempt.session_id != session_id:
        return None
    if attempt.created_at < _quiz_attempt_cutoff():
        db.session.delete(attempt)
        db.session.commit()
        return None

    correct_answers = json.loads(attempt.correct_answers_json)
    results = []
    score = 0
    for question_id, answers in correct_answers.items():
        selected = set(submitted_answers.get(question_id, []))
        correct = set(answers)
        is_correct = bool(selected) and selected == correct
        if is_correct:
            score += 1
        results.append({
            'id': question_id,
            'is_correct': is_correct,
            'correct_answers': list(correct),
        })

    result = {
        'success': True,
        'score': score,
        'total': attempt.question_count,
        'results': results,
    }
    db.session.delete(attempt)
    db.session.commit()
    return result


def _generate_deck_quiz_questions(deck_id):
    """Build multiple-choice questions from one deck's cards and answers."""
    deck = get_deck_with_content(deck_id)
    cards = list(deck.cards) if deck else []
    deck_answers = [answer.answer for card in cards for answer in card.answers]
    if not deck_answers:
        deck_answers = ['Option A', 'Option B', 'Option C', 'Option D', 'No other answers available']

    quiz_questions = []
    for card in cards:
        correct_answers = [answer.answer for answer in card.answers]
        chosen_correct = random.sample(correct_answers, random.randint(1, len(correct_answers))) if correct_answers else []
        wrong_needed = max(0, 4 - len(chosen_correct))
        if wrong_needed < 0:
            chosen_correct = random.sample(chosen_correct, 4)
            wrong_needed = 0

        safe_distractors = [answer for answer in deck_answers if answer not in correct_answers]
        if len(safe_distractors) >= wrong_needed:
            chosen_wrong = random.sample(safe_distractors, wrong_needed)
        else:
            chosen_wrong = safe_distractors + [f'Generic Distractor {index}' for index in range(wrong_needed - len(safe_distractors))]

        options = (
            [{'text': answer, 'is_correct': True} for answer in chosen_correct]
            + [{'text': answer, 'is_correct': False} for answer in chosen_wrong]
        )
        random.shuffle(options)
        quiz_questions.append({
            'id': card.card_id,
            'question': card.question,
            'options': options,
        })
    return quiz_questions


def _generate_custom_quiz_questions(custom_quiz_id):
    """Build either static or dynamic multiple-choice questions from a saved custom quiz."""
    quiz = get_quiz_with_content(custom_quiz_id)
    if not quiz:
        return []

    all_quiz_options = {question.question_id: [option.text for option in question.options] for question in quiz.questions}
    quiz_questions = []
    for question in quiz.questions:
        if question.type == 'static':
            options = [{'text': option.text, 'is_correct': option.is_correct} for option in question.options]
            random.shuffle(options)
        else:
            correct_answers = [option.text for option in question.options]
            distractor_pool = []
            for other_question_id, options_for_question in all_quiz_options.items():
                if other_question_id != question.question_id:
                    distractor_pool.extend(options_for_question)
            chosen_correct = [random.choice(correct_answers)] if correct_answers else []
            safe_distractors = list(set(answer for answer in distractor_pool if answer not in correct_answers))
            if len(safe_distractors) >= 3:
                chosen_wrong = random.sample(safe_distractors, 3)
            else:
                chosen_wrong = safe_distractors + [f'Distractor {index}' for index in range(3 - len(safe_distractors))]

            options = (
                [{'text': answer, 'is_correct': True} for answer in chosen_correct]
                + [{'text': answer, 'is_correct': False} for answer in chosen_wrong]
            )
            random.shuffle(options)

        quiz_questions.append({
            'id': f'q_{question.question_id}',
            'question': question.question,
            'options': options,
        })
    return quiz_questions


def generate_quiz_data(deck_id=None, custom_quiz_id=None):
    """Generate a browser-safe quiz payload from a deck or custom quiz."""
    if deck_id:
        return _generate_deck_quiz_questions(deck_id)
    if custom_quiz_id:
        return _generate_custom_quiz_questions(custom_quiz_id)
    return []


def register_cli_commands(flask_app):
    """Attach application CLI commands exactly once to one app instance."""
    from legacy_repair import repair_legacy_schema_command

    for command in (
        provision_admin,
        set_user_role_command,
        rebuild_public_search_index_command,
        check_public_search_index_command,
        cleanup_quiz_attempts_command,
        repair_legacy_schema_command,
    ):
        if command.name not in flask_app.cli.commands:
            flask_app.cli.add_command(command)



