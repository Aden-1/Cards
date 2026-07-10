import random
import re
import unicodedata
import os
import csv
import io
import json
import logging
import secrets
import smtplib
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import click
from flask import Flask
from flask_compress import Compress
from flask_migrate import Migrate
from itsdangerous import BadSignature, BadTimeSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from werkzeug.security import generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from models import db, User, Deck, Card, CardAnswer, Quiz, QuizQuestion, QuizOption, QuizAttempt, CardMasteryProgress, MatchPairProgress
from routes import register_routes

app = Flask(__name__, instance_relative_config=True)

# Application environment and runtime configuration.
environment_name = os.environ.get('APP_ENV', os.environ.get('FLASK_ENV', 'development')).lower()
is_production = environment_name == 'production'


def _env_bool(name, default=False):
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in ('1', 'true', 'yes', 'on')


def _env_int(name, default):
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f'{name} must be an integer.') from exc


def _env_str(name, default=None):
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    value = raw_value.strip()
    return value or default


def _env_list(name):
    raw_value = os.environ.get(name)
    if not raw_value:
        return None
    values = [value.strip() for value in raw_value.split(',') if value.strip()]
    return values or None


def _normalize_database_url(url, require_ssl=False):
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql+psycopg://', 1)
    elif url.startswith('postgresql://'):
        url = url.replace('postgresql://', 'postgresql+psycopg://', 1)

    if require_ssl and url.startswith('postgresql+psycopg://') and 'sslmode=' not in url:
        separator = '&' if '?' in url else '?'
        url = f'{url}{separator}sslmode=require'
    return url


def _build_engine_options(database_url):
    if database_url.startswith('sqlite'):
        return {}

    return {
        'pool_pre_ping': True,
        'pool_recycle': _env_int('DB_POOL_RECYCLE', 300),
        'pool_size': _env_int('DB_POOL_SIZE', 5),
        'max_overflow': _env_int('DB_MAX_OVERFLOW', 2),
        'pool_timeout': _env_int('DB_POOL_TIMEOUT', 10),
    }


class JsonLogFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        if record.exc_info:
            payload['exception'] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def _configure_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.INFO if is_production else logging.WARNING)
    app.logger.handlers = []
    app.logger.propagate = True


_configure_logging()

# Session and cookie security.
# Deployed applications must never use a known key.
secret_key = os.environ.get('SECRET_KEY')
if is_production and not secret_key:
    raise RuntimeError('SECRET_KEY must be set in production.')
app.config['SECRET_KEY'] = secret_key or 'dev-only-change-me'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = is_production or _env_bool('SESSION_COOKIE_SECURE', default=False)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=_env_int('SESSION_LIFETIME_DAYS', 7))
app.config['IS_PRODUCTION'] = is_production
app.config['PREFERRED_URL_SCHEME'] = 'https' if is_production else 'http'
app.config['MAX_CONTENT_LENGTH'] = _env_int('MAX_CONTENT_LENGTH', 2 * 1024 * 1024)
app.config['COMPRESS_ALGORITHM'] = ['br', 'gzip']
app.config['COMPRESS_ALGORITHM_STREAMING'] = ['br', 'gzip']
app.config['COMPRESS_BR_LEVEL'] = _env_int('COMPRESS_BR_LEVEL', 6)
app.config['COMPRESS_MIN_SIZE'] = _env_int('COMPRESS_MIN_SIZE', 500)
app.config['PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS'] = _env_int('PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS', 3600)
app.config['QUIZ_ATTEMPT_MAX_AGE_SECONDS'] = _env_int('QUIZ_ATTEMPT_MAX_AGE_SECONDS', 7200)
app.config['MAX_ACTIVE_QUIZ_ATTEMPTS'] = _env_int('MAX_ACTIVE_QUIZ_ATTEMPTS', 5)
app.config['MAX_QUIZ_QUESTIONS'] = _env_int('MAX_QUIZ_QUESTIONS', 50)
for quiz_limit_name in (
    'QUIZ_ATTEMPT_MAX_AGE_SECONDS',
    'MAX_ACTIVE_QUIZ_ATTEMPTS',
    'MAX_QUIZ_QUESTIONS',
):
    if app.config[quiz_limit_name] < 1:
        raise RuntimeError(f'{quiz_limit_name} must be greater than zero.')
app.config['PUBLIC_REGISTRATION_ENABLED'] = _env_bool(
    'PUBLIC_REGISTRATION_ENABLED',
    default=not is_production,
)
app.config['MAIL_SERVER'] = _env_str('MAIL_SERVER')
app.config['MAIL_PORT'] = _env_int('MAIL_PORT', 587)
app.config['MAIL_USERNAME'] = _env_str('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = _env_str('MAIL_PASSWORD')
app.config['MAIL_USE_TLS'] = _env_bool('MAIL_USE_TLS', default=True)
app.config['MAIL_USE_SSL'] = _env_bool('MAIL_USE_SSL', default=False)
app.config['MAIL_DEFAULT_SENDER'] = _env_str('MAIL_DEFAULT_SENDER')
app.config['PASSWORD_RESET_URL_BASE'] = _env_str('PASSWORD_RESET_URL_BASE')
app.config['PASSWORD_RESET_EMAILS_ENABLED'] = bool(
    app.config['MAIL_SERVER'] and app.config['MAIL_DEFAULT_SENDER']
)
trusted_hosts = _env_list('TRUSTED_HOSTS')
if is_production and not trusted_hosts:
    raise RuntimeError('TRUSTED_HOSTS must be set in production.')
app.config['TRUSTED_HOSTS'] = trusted_hosts
if is_production:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

compress = Compress(app)

# Database configuration.
# Use DATABASE_URL in deployed environments with local SQLite fallback.
database_url = os.environ.get('DATABASE_URL')
if is_production and not database_url:
    raise RuntimeError('DATABASE_URL must be set in production.')
database_url = database_url or 'sqlite:///cards.db'
database_url = _normalize_database_url(database_url, require_ssl=is_production)
if is_production and not database_url.startswith('postgresql+psycopg://'):
    raise RuntimeError('DATABASE_URL must use PostgreSQL in production.')
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = _build_engine_options(database_url)
app.config['DATABASE_SSL_REQUIRED'] = is_production and database_url.startswith('postgresql+psycopg://')

db.init_app(app)
migrate = Migrate(app, db)

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


# Text validation helpers.
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


def _log_search_index_failure(action, exc, item_type=None, item_id=None):
    app.logger.exception(
        'public_search_index_failure action=%s item_type=%s item_id=%s',
        action,
        item_type,
        item_id,
    )


# Lightweight startup migrations for older local SQLite databases.
def _ensure_user_theme_preference_column():
    """Add User.theme_preference for existing SQLite databases if missing."""
    if not _is_sqlite_backend():
        return
    inspector = db.inspect(db.engine)
    if not inspector.has_table('user'):
        return
    columns = {column['name'] for column in inspector.get_columns('user')}
    if 'theme_preference' in columns:
        return
    db.session.execute(
        text("ALTER TABLE user ADD COLUMN theme_preference VARCHAR(10) NOT NULL DEFAULT 'dark'")
    )
    db.session.commit()


def _ensure_user_mastery_strategy_preference_column():
    """Add User.mastery_strategy_preference for existing SQLite databases if missing."""
    if not _is_sqlite_backend():
        return
    inspector = db.inspect(db.engine)
    if not inspector.has_table('user'):
        return
    columns = {column['name'] for column in inspector.get_columns('user')}
    if 'mastery_strategy_preference' in columns:
        return
    db.session.execute(
        text("ALTER TABLE user ADD COLUMN mastery_strategy_preference VARCHAR(30) NOT NULL DEFAULT 'spaced'")
    )
    db.session.commit()


def _ensure_user_match_strategy_preference_column():
    """Add User.match_strategy_preference for existing SQLite databases if missing."""
    if not _is_sqlite_backend():
        return
    inspector = db.inspect(db.engine)
    if not inspector.has_table('user'):
        return
    columns = {column['name'] for column in inspector.get_columns('user')}
    if 'match_strategy_preference' in columns:
        return
    db.session.execute(
        text("ALTER TABLE user ADD COLUMN match_strategy_preference VARCHAR(30) NOT NULL DEFAULT 'standard_shuffle'")
    )
    db.session.commit()


def _ensure_deck_is_featured_column():
    """Add Deck.is_featured for existing SQLite databases if missing."""
    if not _is_sqlite_backend():
        return
    inspector = db.inspect(db.engine)
    if not inspector.has_table('deck'):
        return
    columns = {column['name'] for column in inspector.get_columns('deck')}
    if 'is_featured' not in columns:
        db.session.execute(
            text("ALTER TABLE deck ADD COLUMN is_featured BOOLEAN NOT NULL DEFAULT 0")
        )
        db.session.commit()
    index_names = {index['name'] for index in inspector.get_indexes('deck')}
    if 'ix_deck_is_featured' not in index_names:
        db.session.execute(text("CREATE INDEX IF NOT EXISTS ix_deck_is_featured ON deck (is_featured)"))
        db.session.commit()


def _ensure_match_pair_progress_table():
    """Create MatchPairProgress table for existing databases if missing."""
    if not _is_sqlite_backend():
        return
    inspector = db.inspect(db.engine)
    if not inspector.has_table('user'):
        return
    MatchPairProgress.__table__.create(bind=db.engine, checkfirst=True)


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
    text = (raw_text or '').replace('\r\n', '\n').replace('\r', '\n').strip('\ufeff \n\t')
    if not text:
        raise ValueError('Paste deck content to import.')

    parsed_rows = []
    invalid_lines = 0

    for line in text.split('\n'):
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
        parsed_rows.append((question, answer))

    if not parsed_rows:
        raise ValueError('No valid cards found. Expected one card per line like "question,answer".')

    card_map = {}
    card_order = []
    for question, answer in parsed_rows:
        cleaned_question = _validate_text_length('Question', question, MAX_CARD_QUESTION_LENGTH, required=True)
        cleaned_answer = _validate_text_length('Answer', answer, MAX_CARD_ANSWER_LENGTH, required=True)
        if cleaned_question not in card_map:
            card_map[cleaned_question] = []
            card_order.append(cleaned_question)
        if cleaned_answer not in card_map[cleaned_question]:
            if len(card_map[cleaned_question]) >= MAX_IMPORT_ANSWERS_PER_CARD:
                raise ValueError(f'Cards may have at most {MAX_IMPORT_ANSWERS_PER_CARD} answers.')
            card_map[cleaned_question].append(cleaned_answer)

    cards = [{'question': question, 'answers': card_map[question]} for question in card_order if card_map[question]]
    if not cards:
        raise ValueError('No valid cards found after parsing.')
    if len(cards) > MAX_IMPORT_CARD_COUNT:
        raise ValueError(f'Imported decks may contain at most {MAX_IMPORT_CARD_COUNT} cards.')

    return {
        'cards': cards,
        'invalid_lines': invalid_lines,
        'line_count': len(parsed_rows) + invalid_lines,
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
    query = _quiz_query_with_question_counts()
    if user_id is None:
        return _attach_quiz_question_counts(query.filter(Quiz.is_public == True).all())
    return _attach_quiz_question_counts(
        query.filter((Quiz.owned_by == user_id) | (Quiz.is_public == True)).all()
    )

def get_user_custom_quizzes(user_id):
    return _attach_quiz_question_counts(
        _quiz_query_with_question_counts().filter(Quiz.owned_by == user_id).all()
    )

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
    db.session.commit()
    _sync_content_fts_index_for_quiz(quiz)
    return quiz

def edit_custom_quiz(quiz_id, title, is_public=False, description=None, tags=None):
    quiz = Quiz.query.get(quiz_id)
    if quiz:
        title, description, tags = _validate_quiz_metadata(title, description, tags)
        quiz.title = title
        quiz.is_public = is_public
        quiz.description = description
        quiz.tags = tags
        db.session.commit()
        _sync_content_fts_index_for_quiz(quiz)
        return quiz
    return None

def delete_custom_quiz(quiz_id):
    quiz = Quiz.query.get(quiz_id)
    if quiz:
        _delete_content_fts_index_row('quiz', quiz.quiz_id)
        db.session.delete(quiz)
        db.session.commit()
        return True
    return False


def copy_public_quiz_to_user(source_quiz_id, user_id):
    source_quiz = get_quiz_with_content(source_quiz_id)
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


def copy_public_deck_to_user(source_deck_id, user_id):
    source_deck = get_deck_with_content(source_deck_id)
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
    db.session.commit()
    return q


def edit_quiz_question(question_id, question_text, q_type, options_data):
    q = QuizQuestion.query.get(question_id)
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
def create_user(username, password, email=None, role='standard'):
    user = User(username=username, email=email or None, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def set_user_role(user, role):
    if role not in ('standard', 'moderator', 'admin'):
        raise ValueError('Role must be standard, moderator, or admin.')
    user.role = role
    db.session.commit()
    return user


@app.cli.command('provision-admin')
@click.option('--username', required=True, help='Username for the new administrator.')
@click.option('--email', default=None, help='Optional email address for the administrator.')
@click.password_option(confirmation_prompt=True)
def provision_admin(username, email, password):
    """Create the initial administrator outside the public registration flow."""
    username = username.strip()
    email = email.strip().lower() if email else None
    if not re.fullmatch(r'[A-Za-z0-9_.-]{3,40}', username):
        raise click.ClickException('Username must be 3-40 letters, numbers, dots, dashes, or underscores.')
    if len(password) < 12 or not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
        raise click.ClickException('Password must be at least 12 characters and contain a letter and a number.')
    if get_user(username) or (email and get_user_by_email(email)):
        raise click.ClickException('An account already exists with that username or email.')

    try:
        user = create_user(username=username, password=password, email=email, role='admin')
    except IntegrityError as exc:
        db.session.rollback()
        raise click.ClickException('An account already exists with that username or email.') from exc
    app.logger.info('administrator_provisioned username=%s user_id=%s', user.username, user.user_id)
    click.echo(f'Created administrator account: {user.username}')


@app.cli.command('set-user-role')
@click.option('--username', default=None, help='Username of the existing account.')
@click.option('--email', default=None, help='Email of the existing account.')
@click.option('--role', required=True, type=click.Choice(['standard', 'moderator', 'admin'], case_sensitive=False))
def set_user_role_command(username, email, role):
    """Change an existing user's role through a controlled CLI workflow."""
    username = username.strip() if username else None
    email = email.strip().lower() if email else None
    if not username and not email:
        raise click.ClickException('Provide --username or --email.')

    user = get_user(username) if username else get_user_by_email(email)
    if not user:
        raise click.ClickException('User not found.')

    role = role.lower()
    set_user_role(user, role)
    app.logger.info('user_role_changed username=%s user_id=%s role=%s', user.username, user.user_id, role)
    click.echo(f'Updated {user.username} to role {role}.')


@app.cli.command('rebuild-public-search-index')
def rebuild_public_search_index_command():
    """Rebuild the public full-text search index."""
    _rebuild_content_fts_index()
    app.logger.info('public_search_index_rebuilt backend=%s', _search_backend_name())
    click.echo(f"Rebuilt public search index for {_search_backend_name()}.")


@app.cli.command('cleanup-quiz-attempts')
def cleanup_quiz_attempts_command():
    """Delete expired server-side quiz attempts."""
    deleted_rows = delete_expired_quiz_attempts()
    click.echo(f'Deleted {deleted_rows} expired quiz attempt(s).')

def get_user(username):
    return User.query.filter_by(username=username).first()


def get_user_by_id(user_id):
    return User.query.get(user_id)


def get_user_by_email(email):
    if not email:
        return None
    return User.query.filter_by(email=email).first()


def update_user_account(user_id, username, email=None, password=None):
    user = db.session.get(User, user_id)
    if not user:
        return None
    user.username = username
    user.email = email or None
    if password:
        user.set_password(password)
        user.auth_version += 1
    db.session.commit()
    return user


def _account_token_serializer():
    return URLSafeTimedSerializer(app.config['SECRET_KEY'], salt='cards-account-lifecycle')


def generate_password_reset_token(user):
    serializer = _account_token_serializer()
    return serializer.dumps({
        'user_id': user.user_id,
        'email': user.email,
        'auth_version': user.auth_version,
        'purpose': 'password_reset',
    })


def _load_password_reset_token_payload(token, max_age_seconds=None):
    serializer = _account_token_serializer()
    if max_age_seconds is None:
        max_age_seconds = app.config['PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS']
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
        or user.email != payload['email']
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
        User.email == payload['email'],
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
    configured_base = app.config.get('PASSWORD_RESET_URL_BASE')
    if configured_base:
        return f"{configured_base.rstrip('/')}?token={token}"
    return None


def send_password_reset_email(user, reset_url):
    message = EmailMessage()
    message['Subject'] = 'Reset your Cards password'
    message['From'] = app.config['MAIL_DEFAULT_SENDER']
    message['To'] = user.email
    message.set_content(
        (
            f"Hello {user.username},\n\n"
            "We received a request to reset your Cards password.\n"
            f"Use this link within {app.config['PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS'] // 60} minutes:\n\n"
            f"{reset_url}\n\n"
            "If you did not request this, you can ignore this email."
        )
    )

    smtp_host = app.config['MAIL_SERVER']
    smtp_port = app.config['MAIL_PORT']
    smtp_username = app.config.get('MAIL_USERNAME')
    smtp_password = app.config.get('MAIL_PASSWORD')
    use_ssl = app.config.get('MAIL_USE_SSL')
    use_tls = app.config.get('MAIL_USE_TLS')

    smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_class(smtp_host, smtp_port, timeout=10) as smtp:
        if not use_ssl and use_tls:
            smtp.starttls()
        if smtp_username:
            smtp.login(smtp_username, smtp_password or '')
        smtp.send_message(message)


def delete_user_account(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return False

    owned_deck_ids = [deck_id for (deck_id,) in db.session.query(Deck.deck_id).filter_by(owned_by=user.user_id).all()]
    owned_quiz_ids = [quiz_id for (quiz_id,) in db.session.query(Quiz.quiz_id).filter_by(owned_by=user.user_id).all()]
    for deck_id in owned_deck_ids:
        _delete_content_fts_index_row('deck', deck_id)
    for quiz_id in owned_quiz_ids:
        _delete_content_fts_index_row('quiz', quiz_id)

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
    db.session.commit()
    _sync_content_fts_index_for_deck(deck)
    return deck


def import_deck(user_id, description, raw_text, sortable=False, is_public=False, is_featured=False, detailed_description=None, tags=None):
    description, detailed_description, tags = _validate_deck_metadata(description, detailed_description, tags)
    parsed = parse_imported_deck_text(raw_text)
    cards = parsed['cards']

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

    next_position = 1
    for card_data in cards:
        card = Card(deck_id=deck.deck_id, question=card_data['question'], position=next_position)
        db.session.add(card)
        db.session.flush()
        for answer_text in card_data['answers']:
            db.session.add(CardAnswer(card_id=card.card_id, answer=answer_text))
        next_position += 1

    db.session.commit()
    _sync_content_fts_index_for_deck(deck)
    return {
        'deck': deck,
        'card_count': len(cards),
        'invalid_lines': parsed['invalid_lines'],
        'line_count': parsed['line_count'],
    }

def _deck_query_with_content():
    return Deck.query.options(
        selectinload(Deck.cards).selectinload(Card.answers)
    )


def get_deck_with_content(deck_id):
    return _deck_query_with_content().filter(Deck.deck_id == deck_id).first()


def get_user_decks(user_id):
    return _attach_deck_card_counts(
        _deck_query_with_card_counts()
        .filter(Deck.owned_by == user_id)
        .all()
    )


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
        return _attach_deck_card_counts(query.filter(Deck.is_public == True).all())
    return _attach_deck_card_counts(
        query.filter((Deck.owned_by == user_id) | (Deck.is_public == True)).all()
    )


def get_homepage_public_data(featured_limit=3, tag_limit=5):
    public_decks = get_accessible_decks(None)
    eligible_featured_decks = [deck for deck in public_decks if deck.is_featured]
    featured_limit = max(0, int(featured_limit))
    tag_limit = max(0, int(tag_limit))

    featured_decks = []
    if eligible_featured_decks and featured_limit > 0:
        day_seed = datetime.now().date().isoformat()
        daily_rng = random.Random(day_seed)
        featured_pool = sorted(eligible_featured_decks, key=lambda deck: deck.deck_id)
        featured_decks = daily_rng.sample(featured_pool, min(featured_limit, len(featured_pool)))

    tag_counter = Counter()
    for deck in public_decks:
        seen_tags = set()
        for raw_tag in (deck.tags or '').split(','):
            cleaned_tag = raw_tag.strip()
            normalized_tag = cleaned_tag.lower()
            if not cleaned_tag or normalized_tag in seen_tags:
                continue
            seen_tags.add(normalized_tag)
            tag_counter[cleaned_tag] += 1

    featured_tags = [
        {'tag': tag, 'count': count}
        for tag, count in sorted(tag_counter.items(), key=lambda item: (-item[1], item[0].lower()))[:tag_limit]
    ]

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
    deck = Deck.query.get(deck_id)
    if deck:
        _delete_content_fts_index_row('deck', deck.deck_id)
        db.session.delete(deck)
        db.session.commit()
        return True
    return False

def edit_deck(deck_id, description, sortable=False, is_public=False, is_featured=False, detailed_description=None, tags=None):
    deck = Deck.query.get(deck_id)
    if deck:
        description, detailed_description, tags = _validate_deck_metadata(description, detailed_description, tags)
        deck.description = description
        deck.sortable = sortable
        deck.is_public = is_public
        deck.is_featured = is_featured
        deck.detailed_description = detailed_description
        deck.tags = tags
        db.session.commit()
        _sync_content_fts_index_for_deck(deck)
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
    ).all()
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
        ).all()
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
    # Create backend-specific full-text structures only when needed.
    if _is_sqlite_backend():
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


def _postgres_search_vector_expression():
    # Weight fields so title and tags rank above long descriptions.
    return (
        "setweight(to_tsvector('english', coalesce(:title, '')), 'A') || "
        "setweight(to_tsvector('english', coalesce(:tags, '')), 'B') || "
        "setweight(to_tsvector('english', coalesce(:description, '')), 'C')"
    )


def _delete_content_fts_index_row(item_type, item_id):
    try:
        _ensure_content_fts_index()
        if _is_sqlite_backend():
            db.session.execute(
                text("DELETE FROM public_content_fts WHERE item_type = :item_type AND item_id = :item_id"),
                {'item_type': item_type, 'item_id': str(item_id)}
            )
        else:
            db.session.execute(
                text("DELETE FROM public_content_search WHERE item_type = :item_type AND item_id = :item_id"),
                {'item_type': item_type, 'item_id': int(item_id)}
            )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        _log_search_index_failure('delete', exc, item_type=item_type, item_id=item_id)


def _sync_content_fts_index_row(item_type, item_id, title, description, tags, is_public):
    try:
        _ensure_content_fts_index()
        if _is_sqlite_backend():
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
        else:
            db.session.execute(
                text("DELETE FROM public_content_search WHERE item_type = :item_type AND item_id = :item_id"),
                {'item_type': item_type, 'item_id': int(item_id)}
            )
            if is_public:
                db.session.execute(
                    text(f"""
                        INSERT INTO public_content_search (
                            item_type, item_id, title, description, tags, search_vector
                        )
                        VALUES (
                            :item_type, :item_id, :title, :description, :tags,
                            {_postgres_search_vector_expression()}
                        )
                    """),
                    {
                        'item_type': item_type,
                        'item_id': int(item_id),
                        'title': title or '',
                        'description': description or '',
                        'tags': tags or '',
                    }
                )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        _log_search_index_failure('sync', exc, item_type=item_type, item_id=item_id)


def _sync_content_fts_index_for_deck(deck):
    _sync_content_fts_index_row(
        item_type='deck',
        item_id=deck.deck_id,
        title=deck.description,
        description=deck.detailed_description,
        tags=deck.tags,
        is_public=deck.is_public,
    )


def _sync_content_fts_index_for_quiz(quiz):
    _sync_content_fts_index_row(
        item_type='quiz',
        item_id=quiz.quiz_id,
        title=quiz.title,
        description=quiz.description,
        tags=quiz.tags,
        is_public=quiz.is_public,
    )


def _rebuild_content_fts_index():
    _ensure_content_fts_index()
    if _is_sqlite_backend():
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
    else:
        db.session.execute(text("DELETE FROM public_content_search"))
        for deck in Deck.query.filter(Deck.is_public == True).all():
            db.session.execute(
                text(f"""
                    INSERT INTO public_content_search (
                        item_type, item_id, title, description, tags, search_vector
                    )
                    VALUES (
                        :item_type, :item_id, :title, :description, :tags,
                        {_postgres_search_vector_expression()}
                    )
                """),
                {
                    'item_type': 'deck',
                    'item_id': int(deck.deck_id),
                    'title': deck.description or '',
                    'description': deck.detailed_description or '',
                    'tags': deck.tags or '',
                }
            )
        for quiz in Quiz.query.filter(Quiz.is_public == True).all():
            db.session.execute(
                text(f"""
                    INSERT INTO public_content_search (
                        item_type, item_id, title, description, tags, search_vector
                    )
                    VALUES (
                        :item_type, :item_id, :title, :description, :tags,
                        {_postgres_search_vector_expression()}
                    )
                """),
                {
                    'item_type': 'quiz',
                    'item_id': int(quiz.quiz_id),
                    'title': quiz.title or '',
                    'description': quiz.description or '',
                    'tags': quiz.tags or '',
                }
            )

    db.session.commit()


def _fallback_search_public_content(query_text):
    search_term = f"%{query_text}%"
    decks = _attach_deck_card_counts(
        _deck_query_with_card_counts().filter(
            Deck.is_public == True,
            db.or_(
                Deck.description.ilike(search_term),
                Deck.detailed_description.ilike(search_term),
                Deck.tags.ilike(search_term)
            )
        ).all()
    )
    quizzes = _attach_quiz_question_counts(
        _quiz_query_with_question_counts().filter(
            Quiz.is_public == True,
            db.or_(
                Quiz.title.ilike(search_term),
                Quiz.description.ilike(search_term),
                Quiz.tags.ilike(search_term)
            )
        ).all()
    )
    return decks, quizzes


def search_public_content(query_text, limit=50, user_id=None):
    query_text = (query_text or '').strip()
    if not query_text:
        return {'decks': [], 'quizzes': [], 'has_exact_match': False, 'query_tokens': [], 'expanded_tokens': []}

    fts_query, query_tokens = _build_fts_query(query_text)
    if not query_tokens:
        return {'decks': [], 'quizzes': [], 'has_exact_match': False, 'query_tokens': [], 'expanded_tokens': []}

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
                    ORDER BY rank
                    LIMIT :limit
                """),
                {'match_query': fts_query, 'limit': int(limit)}
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
                    ORDER BY rank DESC
                    LIMIT :limit
                """),
                {'query_text': query_text, 'limit': int(limit)}
            ).fetchall()

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
        app.logger.exception('public_search_query_failed query=%s', query_text, exc_info=exc)
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
    }

# Card and answer helpers.
def add_card(deck_id, question, answers):
    # Positions are 1-based within each deck.
    max_position = db.session.query(db.func.max(Card.position)).filter_by(deck_id=deck_id).scalar() or 0
    next_position = max_position + 1
    question, answers = _validate_card_payload(question, answers)

    card = Card(deck_id=deck_id, question=question, position=next_position)
    db.session.add(card)
    db.session.flush()

    for answer_text in answers:
        card_answer = CardAnswer(card_id=card.card_id, answer=answer_text)
        db.session.add(card_answer)

    db.session.commit()
    return card


def add_answer_to_card(card_id, answer):
    card = Card.query.get(card_id)
    if card:
        card_answer = CardAnswer(card_id=card_id, answer=answer)
        db.session.add(card_answer)
        db.session.commit()
        return card_answer
    return None


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


def delete_card(card_id):
    card = Card.query.get(card_id)
    if card:
        db.session.delete(card)
        db.session.commit()
        return True
    return False


def edit_card(card_id, question, answers):
    card = Card.query.get(card_id)
    if card:
        answers = _normalize_answers(answers)
        if not answers:
            deck_id = card.deck_id
            db.session.delete(card)
            db.session.commit()
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

    app.logger.warning('match_progress_update_skipped user_id=%s answer_id=%s', user_id, answer_id)


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
    card = Card.query.get(card_id)
    if not card:
        return {'success': False, 'error': 'Card not found'}
    if not card.deck.sortable:
        return {'success': False, 'error': 'Card order can only be changed in sorted decks'}

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


def swap_cards_in_deck(card_id, target_card_id):
    """Swap two cards in the same sortable deck."""
    first_card = Card.query.get(card_id)
    second_card = Card.query.get(target_card_id)

    if not first_card or not second_card:
        return {'success': False, 'error': 'One or more cards were not found'}
    if first_card.deck_id != second_card.deck_id:
        return {'success': False, 'error': 'Cards must be in the same deck'}
    if not first_card.deck.sortable:
        return {'success': False, 'error': 'Card order can only be changed in sorted decks'}
    if first_card.card_id == second_card.card_id:
        return {'success': True, 'swapped': False, 'deck_id': first_card.deck_id}

    first_card.position, second_card.position = second_card.position, first_card.position
    db.session.commit()

    return {'success': True, 'swapped': True, 'deck_id': first_card.deck_id}

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
    card = Card.query.get(card_id)
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

    app.logger.warning('mastery_progress_update_skipped user_id=%s card_id=%s', user_id, card_id)
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
        max_age_seconds = app.config['QUIZ_ATTEMPT_MAX_AGE_SECONDS']
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
    max_questions = max(1, app.config['MAX_QUIZ_QUESTIONS'])
    max_active_attempts = max(1, app.config['MAX_ACTIVE_QUIZ_ATTEMPTS'])
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


with app.app_context():
    _ensure_user_theme_preference_column()
    _ensure_user_mastery_strategy_preference_column()
    _ensure_user_match_strategy_preference_column()
    _ensure_deck_is_featured_column()
    _ensure_match_pair_progress_table()

# Route registration lives in routes.py so view handlers stay in one place.
register_routes(app)


if __name__ == '__main__':
    app.run(debug=not is_production)



