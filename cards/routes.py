import csv
import hmac
import io
import json
import re
import secrets
import hashlib
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from flask import Response, abort, current_app, g, jsonify, redirect, render_template, request, session, url_for
from flask_limiter.util import get_remote_address
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from sqlalchemy.orm import joinedload, selectinload
from .api_contract import (
    api_error,
    api_response,
    is_api_request,
    request_payload,
    wants_json_success_response,
)
from .static_assets import asset_url, asset_version, is_current_asset_version
from .config import validate_rate_limit
from .csv_safety import spreadsheet_safe_cell
from .extensions import db, limiter
from .identity import canonical_email, canonical_username, display_username
from .services.authorization import audit_event, has_role
from .urls import deck_url_slug, id_from_url_slug, quiz_url_slug


_ACTIVE_LIMITER = ContextVar('active_route_limiter', default=None)


# Request parsing helpers.
def _request_data():
    return request_payload()


def _int_value(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    if isinstance(value, str) and len(value.strip()) > 19:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= 9223372036854775807 else None


def _requested_page():
    return max(1, _int_value(request.args.get('page')) or 1)


def _requested_page_size():
    return min(50, max(1, _int_value(request.args.get('page_size')) or 20))


def _pagination_context(endpoint):
    """Preserve the current selection/search term while changing only page."""
    values = request.args.to_dict(flat=True)
    values.pop('page', None)
    values.pop('page_size', None)
    return {'pagination_endpoint': endpoint, 'pagination_args': values}


def _query_page(query, page, per_page):
    """Evaluate a bounded query page with one look-ahead row."""
    rows = query.limit(per_page + 1).offset((page - 1) * per_page).all()
    has_next = len(rows) > per_page
    return {
        'items': rows[:per_page],
        'page': page,
        'per_page': per_page,
        'has_prev': page > 1,
        'has_next': has_next,
        'prev_page': page - 1 if page > 1 else None,
        'next_page': page + 1 if has_next else None,
    }


def _include_selected_deck(items, selected_deck, user_id):
    """Keep a direct selection available without rendering more than one page."""
    if not selected_deck or any(deck['deck_id'] == selected_deck.deck_id for deck in items):
        return items
    return [_deck_summary_payload(selected_deck, user_id)] + items[:-1]


def _include_selected_quiz(items, selected_quiz):
    if not selected_quiz or any(quiz.quiz_id == selected_quiz.quiz_id for quiz in items):
        return items
    return [selected_quiz] + items[:-1]


def _master_round_state(deck_id, card_ids):
    """Load a compact seen-card bitset bound to the current deck graph."""
    fingerprint = hashlib.sha256(
        ','.join(str(card_id) for card_id in card_ids).encode('ascii')
    ).hexdigest()[:16]
    state = session.get('master_round_state')
    seen_bits = 0
    if (
        isinstance(state, dict)
        and state.get('deck_id') == deck_id
        and state.get('fingerprint') == fingerprint
    ):
        encoded_bits = state.get('seen_bits')
        max_hex_length = max(1, (len(card_ids) + 3) // 4)
        if (
            isinstance(encoded_bits, str)
            and len(encoded_bits) <= max_hex_length
            and re.fullmatch(r'[0-9a-f]+', encoded_bits)
        ):
            seen_bits = int(encoded_bits, 16)
    return seen_bits, fingerprint


def _store_master_round_state(deck_id, fingerprint, seen_bits):
    session.pop('master_seen_cards', None)
    session['master_round_state'] = {
        'deck_id': deck_id,
        'fingerprint': fingerprint,
        'seen_bits': format(seen_bits, 'x'),
    }


def _redirect_with_fragment(endpoint, fragment=None, **values):
    target = url_for(endpoint, **values)
    if fragment:
        target = f'{target}#{fragment}'
    return redirect(target)


def _as_bool(value):
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _deck_card_count(deck):
    card_count = getattr(deck, 'card_count', None)
    return int(card_count) if card_count is not None else len(deck.cards)


def _deck_summary_payload(deck, user_id):
    """Build the common deck summary payload used across deck-oriented pages."""
    return {
        'deck_id': deck.deck_id,
        'description': deck.description,
        'detailed_description': deck.detailed_description,
        'tags': deck.tags,
        'sortable': deck.sortable,
        'is_public': deck.is_public,
        'is_featured': deck.is_featured,
        'is_owned': bool(user_id is not None and deck.owned_by == user_id),
        'card_count': _deck_card_count(deck),
    }


def _quiz_launcher_deck_payload(deck, user_id):
    """Keep the quiz picker payload minimal because it needs less metadata."""
    return {
        'deck_id': deck.deck_id,
        'description': deck.description,
        'is_owned': bool(user_id is not None and deck.owned_by == user_id),
        'card_count': _deck_card_count(deck),
    }


def _parse_quiz_question_options(data, q_type, answer_mode='choice'):
    """Read the repeated quiz option fields from create/edit forms."""
    options_data = []
    correct_count = 0

    for index in range(1, 6):
        raw_option_text = data.get(f'option_{index}', '')
        if not isinstance(raw_option_text, str):
            raise ValueError('Quiz options must be text.')
        option_text = raw_option_text.strip()
        if not option_text:
            continue

        is_correct = (
            answer_mode == 'typed' or q_type == 'dynamic'
            or data.get(f'is_correct_{index}') is not None
        )
        if is_correct:
            correct_count += 1
        options_data.append({'text': option_text, 'is_correct': is_correct})

    return options_data, correct_count


def _validate_quiz_question_option_count(quiz_id, q_type, options_data, correct_count, answer_mode='choice'):
    if answer_mode == 'typed':
        if not 1 <= correct_count <= 5:
            return _redirect_with_fragment(
                'edit_quiz_route', fragment='quiz-editor', quiz_id=quiz_id,
                notice='Typed questions need at least one accepted answer.', level='error',
            )
        return None
    if q_type == 'dynamic' and not (1 <= correct_count <= 2):
        return _redirect_with_fragment(
            'edit_quiz_route',
            fragment='quiz-editor',
            quiz_id=quiz_id,
            notice='Dynamic questions must have 1-2 correct answers.',
            level='error',
        )

    if q_type == 'static':
        if not (1 <= correct_count <= 2):
            return _redirect_with_fragment(
                'edit_quiz_route',
                fragment='quiz-editor',
                quiz_id=quiz_id,
                notice='Static questions must have 1-2 correct answers.',
                level='error',
            )
        if len(options_data) < 2:
            return _redirect_with_fragment(
                'edit_quiz_route',
                fragment='quiz-editor',
                quiz_id=quiz_id,
                notice='Static questions must have at least 2 options.',
                level='error',
            )
    return None


# Shared request-throttling helpers. In production the configured Redis
# backend is required; Flask-Limiter expires fixed-window keys automatically.
def _client_ip_key():
    return f'ip:{get_remote_address() or "unknown"}'


def _rate_limit_key():
    user_id = session.get('user_id')
    if user_id:
        return f'user:{user_id}'
    return _client_ip_key()


def _target_key(field, namespace):
    """Use a stable, non-reversible key for anonymous account targets."""
    value = _request_data().get(field)
    if value is None or not str(value).strip():
        return f'target:{namespace}:missing:{_client_ip_key()}'
    try:
        normalized = (
            canonical_email(value, allow_none=False)
            if field == 'email'
            else canonical_username(value)
        )
    except ValueError:
        normalized = str(value).strip().casefold()[:512]
    digest = hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    return f'target:{namespace}:{digest}'


def _login_target_key():
    return _target_key('username', 'login')


def _two_factor_target_key():
    """Rate-limit a pending challenge by account without exposing its user ID."""
    user_id = session.get('pending_two_factor_user_id')
    if type(user_id) is not int or user_id < 1:
        return f'target:two-factor:missing:{_client_ip_key()}'
    digest = hmac.new(
        current_app.config['SECRET_KEY'].encode('utf-8'),
        str(user_id).encode('ascii'),
        hashlib.sha256,
    ).hexdigest()
    return f'target:two-factor:{digest}'


def _registration_target_key():
    return _target_key('username', 'registration')


def _recovery_target_key():
    return _target_key('email', 'password-recovery')


def _reset_target_key():
    data = _request_data()
    token = (
        data.get('exchange_token') or data.get('token')
        or session.get('password_reset_token')
    )
    if not token:
        return f'target:password-reset:missing:{_client_ip_key()}'
    digest = hashlib.sha256(str(token).encode('utf-8')).hexdigest()
    return f'target:password-reset:{digest}'


def _verification_target_key():
    token = _request_data().get('exchange_token')
    if not token:
        return f'target:email-verification:missing:{_client_ip_key()}'
    digest = hashlib.sha256(str(token).encode('utf-8')).hexdigest()
    return f'target:email-verification:{digest}'


def _configured_limit(policy_name):
    """Resolve a policy at request time so environment-backed config is testable."""
    def configured_limit():
        return validate_rate_limit(
            f'RATE_LIMIT_{policy_name.upper()}',
            current_app.config['RATE_LIMITS'][policy_name],
        )
    return configured_limit


def _limit(view_func, policy_name, methods, key_func=None):
    app_limiter = _ACTIVE_LIMITER.get() or limiter
    return app_limiter.limit(
        _configured_limit(policy_name), methods=methods, key_func=key_func
    )(view_func)


def _anonymous_sensitive_limit(view_func, policy_name, methods, target_key_func):
    """Apply independent IP and account-target budgets to anonymous flows."""
    limited = _limit(view_func, policy_name, methods, key_func=_client_ip_key)
    return _limit(limited, policy_name, methods, key_func=target_key_func)


def verify_limiter_backend():
    """Raise during production startup when the shared limiter backend is down."""
    if not current_app.config.get('IS_PRODUCTION'):
        return
    app_limiter = current_app.extensions.get('cards_limiter', limiter)
    try:
        available = app_limiter.storage.check()
    except Exception as exc:
        raise RuntimeError('Shared Redis rate-limit backend is unavailable.') from exc
    if not available:
        raise RuntimeError('Shared Redis rate-limit backend is unavailable.')


def _rate_limit_response(error):
    message = 'Too many requests. Please try again later.'
    original_response = error.get_response()
    retry_after = original_response.headers.get('Retry-After')
    if is_api_request():
        response = api_error(message, 429)
    else:
        response = current_app.response_class(message, status=429, mimetype='text/plain')
    if retry_after:
        response.headers['Retry-After'] = retry_after
    return response


def _prepare_security_request():
    g.csp_nonce = secrets.token_urlsafe(24)
    if current_app.config.get('IS_PRODUCTION') and not request.is_secure:
        return redirect(request.url.replace('http://', 'https://', 1), code=308)
    return None


def _canonicalize_static_asset():
    """Redirect stale or hand-written versioned asset URLs to the current hash."""
    if request.endpoint != 'static' or request.method not in ('GET', 'HEAD'):
        return None

    filename = (request.view_args or {}).get('filename')
    supplied_version = request.args.get('v')
    if not filename or not supplied_version:
        return None

    try:
        current_version = asset_version(filename)
    except FileNotFoundError:
        return None

    if supplied_version == current_version:
        return None

    return redirect(url_for('static', filename=filename, v=current_version), code=302)


def _set_security_headers(response):
    nonce = getattr(g, 'csp_nonce', '')
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self'; "
        "font-src 'self' data:; "
        "img-src 'self' data:; "
        "connect-src 'self'"
    )
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    if request.endpoint in {'reset_password', 'verify_email'}:
        response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Permissions-Policy'] = 'camera=(), geolocation=(), microphone=()'
    if request.endpoint == 'static':
        filename = (request.view_args or {}).get('filename')
        version = request.args.get('v')
        response.headers.pop('Expires', None)
        try:
            is_versioned_asset = filename and is_current_asset_version(filename, version)
        except FileNotFoundError:
            is_versioned_asset = False
        if is_versioned_asset:
            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        else:
            # Unversioned URLs remain safe during development and can never
            # pin a changed asset in a shared cache.
            response.headers['Cache-Control'] = 'no-cache, must-revalidate'
    elif (
        response.mimetype == 'text/html'
        and not _current_user()
        and request.endpoint in {
            'index', 'search', 'public_deck_detail', 'public_quiz_detail',
            'public_collection', 'creator_profile',
        }
        and response.status_code == 200
    ):
        response.headers['Cache-Control'] = 'public, max-age=60'
        response.headers['X-Cards-Public'] = '1'
    elif response.mimetype == 'text/html':
        # Pages contain a per-response CSP nonce and may contain CSRF tokens or
        # user-specific navigation/data. Do not permit shared or browser disk
        # caches to retain personalized HTML.
        response.headers['Cache-Control'] = 'no-store, private'
    if current_app.config.get('IS_PRODUCTION'):
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response


def _current_user():
    from services import get_user_by_id
    user_id = session.get('user_id')
    if not user_id:
        return None
    user = get_user_by_id(user_id)
    if not user or not user.is_active:
        session.clear()
        return None
    session_auth_version = session.get('auth_version')
    if session_auth_version is None and user.auth_version == 0:
        # Preserve sessions created before auth versioning was deployed. Once
        # the password changes, auth_version increments and these are revoked.
        session['auth_version'] = 0
    elif session_auth_version != user.auth_version:
        session.clear()
        return None
    return user


def _current_user_id():
    user = _current_user()
    return user.user_id if user else None


def _wants_json():
    return wants_json_success_response()


def _login_required_response():
    if _wants_json():
        return jsonify({'error': 'Login required'}), 401
    return redirect(url_for('login', next=request.url, notice='Please log in first.', level='error'))


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not _current_user():
            return _login_required_response()
        return view_func(*args, **kwargs)
    return wrapped


def admin_required(view_func):
    return roles_required('admin')(view_func)


def roles_required(*roles):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            user = _current_user()
            if not user:
                return _login_required_response()
            if not has_role(user, *roles):
                if _wants_json():
                    return jsonify({'error': 'Insufficient permissions'}), 403
                return redirect(url_for('index', notice='Insufficient permissions.', level='error'))
            return view_func(*args, **kwargs)
        return wrapped
    return decorator


def moderator_required(view_func):
    return roles_required('moderator', 'admin')(view_func)


def _csrf_token():
    return session.get('csrf_token')


def _ensure_csrf_token():
    token = session.get('csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['csrf_token'] = token
    return token


def _study_reminder_scope(user):
    if not user:
        return ''
    return hmac.new(
        current_app.config['SECRET_KEY'].encode('utf-8'),
        f'study-reminder:{user.user_id}'.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()[:24]


def _page_needs_csrf_token(user=None):
    if user:
        return True
    return request.endpoint in {
        'login',
        'register',
        'forgot_password',
        'reset_password',
        'verify_email',
        'match',
        'reorder',
        'quiz',
        'start_quiz',
    }


def _validate_csrf():
    if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
        return None
    sent_token = request.headers.get('X-CSRFToken') or request.form.get('csrf_token')
    if not sent_token or not secrets.compare_digest(sent_token, session.get('csrf_token', '')):
        if _wants_json():
            return jsonify({'error': 'Invalid or missing CSRF token'}), 400
        abort(400)
    return None


def _valid_username(username):
    try:
        canonical = canonical_username(username)
    except ValueError:
        return False
    return bool(re.fullmatch(r'[\w.-]{3,40}', username or '', re.UNICODE)) and len(canonical) >= 3


def _display_username_input(value):
    try:
        return display_username(value or '')
    except ValueError:
        return ''


def _canonical_email_input(value):
    if value is None or not str(value).strip():
        return None
    try:
        return canonical_email(value, allow_none=False)
    except ValueError:
        return ''


def _valid_password(password):
    return (
        bool(password)
        and len(password) >= 12
        and bool(re.search(r'[A-Za-z]', password))
        and bool(re.search(r'\d', password))
    )


def _valid_email(email):
    try:
        canonical = canonical_email(email, allow_none=False)
    except ValueError:
        return False
    return bool(re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', canonical or ''))


def _password_requirements_message(prefix='Passwords'):
    return f'{prefix} must be at least 12 characters and contain a letter and a number.'


def _safe_next_url(next_url):
    if next_url and isinstance(next_url, str) and next_url.startswith('/') and not next_url.startswith('//'):
        # Browsers normalize backslashes in redirect targets before resolving
        # them.  Without this guard, /\\evil.example becomes https://evil.example.
        if '\\' not in next_url and not any(ord(character) < 0x20 for character in next_url):
            parsed = urlsplit(next_url)
            if not parsed.scheme and not parsed.netloc:
                return next_url
    return url_for('index')


def _api_error_handler(error):
    """Use a safe JSON envelope for API failures and retain HTML pages for browsers."""
    if not is_api_request():
        return error

    status = getattr(error, 'code', 500) or 500
    messages = {
        400: 'Invalid request.',
        401: 'Authentication required.',
        403: 'Forbidden.',
        404: 'Not found.',
        405: 'Method not allowed.',
        413: 'Request entity too large.',
        415: 'Unsupported request Content-Type.',
        422: 'Invalid request.',
        429: 'Too many requests. Please try again later.',
        500: 'An unexpected error occurred.',
    }
    if status >= 500:
        current_app.logger.error('api_request_failed status=%s', status, exc_info=True)
    return api_error(messages.get(status, 'Request failed.'), status)


def healthz():
    return api_response({'status': 'ok'}, 200)


def readyz():
    from models import db

    try:
        db.session.execute(text('SELECT 1'))
        return api_response({'status': 'ready', 'database': 'ok'}, 200)
    except Exception:
        current_app.logger.exception('database_readiness_check_failed')
        db.session.rollback()
        return api_error('Service not ready.', 503)


def _public_api_response(payload):
    response = api_response(payload)
    response.headers['Cache-Control'] = 'public, max-age=60'
    return response


def api_v1_decks_route():
    from models import Card, Deck

    page = _requested_page()
    per_page = _requested_page_size()
    rows = db.session.query(
        Deck, db.func.count(Card.card_id).label('card_count'),
    ).outerjoin(Card, Card.deck_id == Deck.deck_id).filter(
        Deck.is_public == True,
    ).group_by(Deck.deck_id).order_by(
        Deck.deck_id.desc(),
    ).limit(per_page + 1).offset((page - 1) * per_page).all()
    has_next = len(rows) > per_page
    data = [{
        'id': deck.deck_id,
        'title': deck.description,
        'description': deck.detailed_description,
        'tags': [tag.strip() for tag in (deck.tags or '').split(',') if tag.strip()],
        'sortable': deck.sortable,
        'card_count': int(card_count or 0),
        'url': url_for('public_deck_detail', deck_slug=deck_url_slug(deck)),
    } for deck, card_count in rows[:per_page]]
    return _public_api_response({
        'api_version': 'v1', 'data': data,
        'pagination': {'page': page, 'page_size': per_page, 'has_next': has_next},
    })


def api_v1_deck_detail_route(deck_id):
    from models import Card, Deck

    deck = Deck.query.options(
        selectinload(Deck.cards).selectinload(Card.answers),
    ).filter_by(deck_id=deck_id, is_public=True).first()
    if not deck:
        return api_error('Public deck not found.', 404)
    cards = [{
        'id': card.card_id, 'position': card.position,
        'question': card.question,
        'answers': [answer.answer for answer in sorted(
            card.answers, key=lambda item: item.answer_id,
        )],
    } for card in sorted(deck.cards, key=lambda item: (item.position, item.card_id))]
    return _public_api_response({
        'api_version': 'v1',
        'data': {
            'id': deck.deck_id, 'title': deck.description,
            'description': deck.detailed_description,
            'tags': [tag.strip() for tag in (deck.tags or '').split(',') if tag.strip()],
            'sortable': deck.sortable, 'cards': cards,
        },
    })


def api_v1_quizzes_route():
    from models import Quiz, QuizQuestion

    page = _requested_page()
    per_page = _requested_page_size()
    rows = db.session.query(
        Quiz, db.func.count(QuizQuestion.question_id).label('question_count'),
    ).outerjoin(QuizQuestion, QuizQuestion.quiz_id == Quiz.quiz_id).filter(
        Quiz.is_public == True,
    ).group_by(Quiz.quiz_id).order_by(
        Quiz.quiz_id.desc(),
    ).limit(per_page + 1).offset((page - 1) * per_page).all()
    has_next = len(rows) > per_page
    data = [{
        'id': quiz.quiz_id, 'title': quiz.title,
        'description': quiz.description,
        'tags': [tag.strip() for tag in (quiz.tags or '').split(',') if tag.strip()],
        'question_count': int(question_count or 0),
        'url': url_for('public_quiz_detail', quiz_slug=quiz_url_slug(quiz)),
    } for quiz, question_count in rows[:per_page]]
    return _public_api_response({
        'api_version': 'v1', 'data': data,
        'pagination': {'page': page, 'page_size': per_page, 'has_next': has_next},
    })


def api_v1_quiz_detail_route(quiz_id):
    from models import Quiz

    quiz = Quiz.query.options(
        selectinload(Quiz.questions),
    ).filter_by(quiz_id=quiz_id, is_public=True).first()
    if not quiz:
        return api_error('Public quiz not found.', 404)
    questions = [{
        'id': question.question_id, 'question': question.question,
        'type': question.type, 'answer_mode': question.answer_mode,
        'pool': question.pool,
    } for question in sorted(quiz.questions, key=lambda item: item.question_id)]
    return _public_api_response({
        'api_version': 'v1',
        'data': {
            'id': quiz.quiz_id, 'title': quiz.title,
            'description': quiz.description,
            'tags': [tag.strip() for tag in (quiz.tags or '').split(',') if tag.strip()],
            'questions': questions,
        },
    })


def service_worker_route():
    worker_path = Path(current_app.static_folder) / 'service-worker.js'
    response = Response(worker_path.read_bytes(), mimetype='application/javascript')
    response.headers['Cache-Control'] = 'no-cache, must-revalidate'
    response.headers['Service-Worker-Allowed'] = '/'
    return response


def _complete_login(user):
    session.clear()
    session.permanent = True
    session['user_id'] = user.user_id
    session['auth_version'] = user.auth_version
    session['csrf_token'] = secrets.token_urlsafe(32)


def _pending_two_factor_user():
    """Return a live, version-bound second-factor challenge target."""
    from models import User

    user_id = session.get('pending_two_factor_user_id')
    auth_version = session.get('pending_two_factor_auth_version')
    method = session.get('pending_two_factor_method')
    issued_at = session.get('pending_two_factor_issued_at')
    if (
        type(user_id) is not int
        or type(auth_version) is not int
        or type(issued_at) not in (int, float)
        or method not in ('email', 'totp')
    ):
        return None

    now = time.time()
    max_age = current_app.config['TWO_FACTOR_CHALLENGE_MAX_AGE_SECONDS']
    if issued_at > now + 60 or now - issued_at > max_age:
        return None

    user = db.session.get(User, user_id)
    if (
        not user
        or not user.is_active
        or user.auth_version != auth_version
        or user.two_factor_method != method
    ):
        return None
    return user


def _queue_account_email(user_id, delivery_type):
    if not current_app.config.get('PASSWORD_RESET_EMAILS_ENABLED'):
        return False
    from services import enqueue_account_email
    try:
        enqueue_account_email(user_id, delivery_type, secrets.token_hex(12))
        return True
    except Exception as exc:
        current_app.logger.error(
            'account_email_queue_enqueue_failed user_id=%s type=%s failure_class=%s',
            user_id, delivery_type, type(exc).__name__,
        )
        return False


def register():
    from services import create_user, get_user, get_user_by_email
    from models import db

    if not current_app.config.get('PUBLIC_REGISTRATION_ENABLED', True):
        message = 'Public registration is currently disabled.'
        if _wants_json():
            return jsonify({'error': message}), 403
        return render_template('register.html', registration_disabled=True, error=message), 403

    if request.method == 'GET':
        return render_template('register.html')

    data = _request_data()
    username = _display_username_input(data.get('username'))
    email = _canonical_email_input(data.get('email'))
    password = data.get('password') or ''
    confirm_password = data.get('confirm_password') or ''

    if not _valid_username(username):
        return render_template('register.html', error='Usernames must be 3-40 letters, numbers, dots, dashes, or underscores.'), 400
    if email is not None and not _valid_email(email):
        return render_template('register.html', error='Enter a valid email address.'), 400
    if not _valid_password(password):
        return render_template('register.html', error=_password_requirements_message()), 400
    if password != confirm_password:
        return render_template('register.html', error='Passwords do not match.'), 400
    if get_user(username):
        return render_template('register.html', error='That username is already taken.'), 400
    if email and get_user_by_email(email):
        return render_template('register.html', error='That email is already in use.'), 400

    try:
        user = create_user(username=username, password=password, email=email, role='standard')
    except (IntegrityError, ValueError):
        db.session.rollback()
        return render_template('register.html', error='That username or email is already in use.'), 400
    _complete_login(user)
    if user.email:
        _queue_account_email(user.user_id, 'verification')
    audit_event('account_registered', user, 'success', target_type='user', target_id=user.user_id)
    return redirect(url_for('edit', notice='Account created', level='success'))


def login():
    from services import get_user

    if request.method == 'GET':
        return render_template('login.html', next=request.args.get('next', ''))

    data = _request_data()
    username = _display_username_input(data.get('username'))
    password = data.get('password') or ''
    user = get_user(username)
    if not user or not user.is_active or not user.check_password(password):
        audit_event('login', user, 'failure')
        return render_template('login.html', error='Invalid username or password.', next=data.get('next', '')), 401

    if user.two_factor_method in ('email', 'totp'):
        session.clear()
        session['pending_two_factor_user_id'] = user.user_id
        session['pending_two_factor_auth_version'] = user.auth_version
        session['pending_two_factor_method'] = user.two_factor_method
        session['pending_two_factor_issued_at'] = time.time()
        session['pending_two_factor_next'] = _safe_next_url(data.get('next'))
        session['csrf_token'] = secrets.token_urlsafe(32)
        if user.two_factor_method == 'email':
            _queue_account_email(user.user_id, 'two_factor')
        audit_event('login_password_accepted', user, 'info')
        return redirect(url_for('two_factor_challenge'))

    _complete_login(user)
    audit_event('login', user, 'success')
    return redirect(_safe_next_url(data.get('next')))


def two_factor_challenge():
    from services import (
        _decrypt_two_factor_secret,
        consume_two_factor_recovery_code,
        verify_email_two_factor_code,
        verify_totp_code,
    )

    user = _pending_two_factor_user()
    if not user:
        session.clear()
        return redirect(url_for('login', notice='Your sign-in challenge has expired.', level='error'))
    if request.method == 'GET':
        return render_template('two_factor_challenge.html', method=user.two_factor_method)
    code = (_request_data().get('code') or '').strip()
    if user.two_factor_method == 'email':
        valid = verify_email_two_factor_code(user, code)
    else:
        totp_secret = _decrypt_two_factor_secret(user.two_factor_totp_secret or '')
        valid = bool(totp_secret and verify_totp_code(totp_secret, code))
    recovery_code_used = False
    if not valid:
        recovery_code_used = consume_two_factor_recovery_code(user, code)
        valid = recovery_code_used
    if not valid:
        audit_event('two_factor_challenge', user, 'failure', method=user.two_factor_method)
        return render_template('two_factor_challenge.html', method=user.two_factor_method, error='That verification or recovery code is invalid or expired.'), 401
    next_url = session.get('pending_two_factor_next')
    _complete_login(user)
    audit_event(
        'login', user, 'success',
        method='recovery_code' if recovery_code_used else user.two_factor_method,
    )
    return redirect(_safe_next_url(next_url))


def resend_two_factor_code():
    user = _pending_two_factor_user()
    if not user or user.two_factor_method != 'email':
        session.clear()
        return redirect(url_for('login', notice='Your sign-in challenge has expired.', level='error'))
    _queue_account_email(user.user_id, 'two_factor')
    audit_event('two_factor_code_resent', user, 'info')
    return redirect(url_for('two_factor_challenge', notice='A new sign-in code is on its way.', level='success'))


def verify_email():
    from services import verify_email_with_token

    legacy_token = (request.args.get('token') or '').strip()
    if request.method == 'GET' and legacy_token:
        user = verify_email_with_token(legacy_token)
        session['email_verification_result'] = bool(user)
        if user:
            audit_event(
                'email_verified', user, 'success',
                target_type='user', target_id=user.user_id,
            )
        return redirect(url_for('verify_email'))

    if request.method == 'POST':
        token = (_request_data().get('exchange_token') or '').strip()
        user = verify_email_with_token(token)
        session['email_verification_result'] = bool(user)
        if user:
            audit_event(
                'email_verified', user, 'success',
                target_type='user', target_id=user.user_id,
            )
        return jsonify({
            'success': bool(user),
            'redirect_url': url_for('verify_email'),
        })

    verified = session.pop('email_verification_result', None)
    return render_template('email_verification.html', verified=verified)


def forgot_password():
    from services import enqueue_password_reset_email, password_reset_target_digest

    success_message = 'If that email matches an active account, a password reset link has been sent.'
    email_delivery_available = current_app.config.get('PASSWORD_RESET_EMAILS_ENABLED', False)
    if request.method == 'GET':
        return render_template(
            'forgot_password.html',
            success=None,
            email_delivery_available=email_delivery_available,
        )

    data = _request_data()
    email = _canonical_email_input(data.get('email')) or ''
    if not _valid_email(email):
        return render_template(
            'forgot_password.html',
            error='Enter a valid email address.',
            email_delivery_available=email_delivery_available,
        ), 400

    if email_delivery_available:
        request_id = secrets.token_hex(12)
        try:
            enqueue_password_reset_email(password_reset_target_digest(email), request_id)
        except Exception as exc:
            current_app.logger.error(
                'password_reset_queue_enqueue_failed request_id=%s failure_class=%s',
                request_id,
                type(exc).__name__,
            )
        else:
            current_app.logger.info(
                'password_reset_queued request_id=%s', request_id
            )

    return render_template(
        'forgot_password.html',
        success=success_message,
        email_delivery_available=email_delivery_available,
    )


def reset_password():
    from services import get_user_by_password_reset_token, reset_user_password_with_token

    legacy_token = (request.args.get('token') or '').strip()
    if request.method == 'GET' and legacy_token:
        session.pop('password_reset_token', None)
        if get_user_by_password_reset_token(legacy_token):
            session['password_reset_token'] = legacy_token
        session['password_reset_exchange_attempted'] = True
        return redirect(url_for('reset_password'))

    data = _request_data() if request.method == 'POST' else {}
    if request.method == 'POST' and 'exchange_token' in data:
        exchanged_token = (data.get('exchange_token') or '').strip()
        exchanged_user = get_user_by_password_reset_token(exchanged_token)
        session.pop('password_reset_token', None)
        if exchanged_user:
            session['password_reset_token'] = exchanged_token
        session['password_reset_exchange_attempted'] = True
        return jsonify({
            'success': bool(exchanged_user),
            'redirect_url': url_for('reset_password'),
        })

    token = (
        data.get('token') or session.get('password_reset_token') or ''
    ).strip()
    user = get_user_by_password_reset_token(token)
    if request.method == 'GET':
        attempted = session.pop('password_reset_exchange_attempted', False)
        if token and not user:
            session.pop('password_reset_token', None)
        token_valid = bool(user) if token or attempted else None
        return render_template(
            'reset_password.html', token_valid=token_valid,
        )

    password = data.get('password') or ''
    confirm_password = data.get('confirm_password') or ''
    if not user:
        session.pop('password_reset_token', None)
        return render_template(
            'reset_password.html',
            token_valid=False,
            error='This password reset link is invalid or has expired.',
        ), 400
    if not _valid_password(password):
        return render_template(
            'reset_password.html',
            token_valid=True,
            error=_password_requirements_message('Passwords'),
        ), 400
    if password != confirm_password:
        return render_template(
            'reset_password.html',
            token_valid=True,
            error='Passwords do not match.',
        ), 400

    reset_user_id = reset_user_password_with_token(token, password)
    if not reset_user_id:
        session.pop('password_reset_token', None)
        return render_template(
            'reset_password.html',
            token_valid=False,
            error='This password reset link is invalid or has expired.',
        ), 400
    session.pop('password_reset_token', None)
    current_app.logger.info('password_reset_completed user_id=%s', reset_user_id)
    return redirect(url_for('login', notice='Password updated. You can log in now.', level='success'))


def logout():
    session.clear()
    return redirect(url_for('index', notice='Logged out', level='success'))


@login_required
def account():
    from services import get_user, get_user_by_email, update_user_account
    from models import db

    user = _current_user()
    if request.method == 'GET':
        return render_template('account.html', user=user)

    data = _request_data()
    username = _display_username_input(data.get('username'))
    email = _canonical_email_input(data.get('email'))
    current_password = data.get('current_password') or ''
    new_password = data.get('new_password') or ''
    confirm_password = data.get('confirm_password') or ''

    if not user.check_password(current_password):
        return render_template('account.html', user=user, error='Enter your current password to save account changes.'), 400
    if not _valid_username(username):
        return render_template('account.html', user=user, error='Usernames must be 3-40 letters, numbers, dots, dashes, or underscores.'), 400
    if email is not None and not _valid_email(email):
        return render_template('account.html', user=user, error='Enter a valid email address.'), 400
    existing_user = get_user(username)
    if existing_user and existing_user.user_id != user.user_id:
        return render_template('account.html', user=user, error='That username is already taken.'), 400
    existing_email = get_user_by_email(email)
    if existing_email and existing_email.user_id != user.user_id:
        return render_template('account.html', user=user, error='That email is already in use.'), 400
    if new_password:
        if not _valid_password(new_password):
            return render_template('account.html', user=user, error=_password_requirements_message('New passwords')), 400
        if new_password != confirm_password:
            return render_template('account.html', user=user, error='New passwords do not match.'), 400

    email_changed = user.canonical_email != email
    try:
        updated_user = update_user_account(user.user_id, username=username, email=email, password=new_password or None)
    except (IntegrityError, ValueError):
        db.session.rollback()
        return render_template('account.html', user=user, error='That username or email is already in use.'), 400
    if new_password:
        session.clear()
        session.permanent = True
        session['user_id'] = updated_user.user_id
        session['auth_version'] = updated_user.auth_version
        session['csrf_token'] = secrets.token_urlsafe(32)
    if email_changed and updated_user.email:
        _queue_account_email(updated_user.user_id, 'verification')
        audit_event('email_verification_requested', updated_user, 'info', target_type='user', target_id=updated_user.user_id)
    return render_template('account.html', user=updated_user, success='Account updated.')


@login_required
def resend_email_verification():
    user = _current_user()
    if not user.email:
        return redirect(url_for('account', notice='Add an email address before requesting verification.', level='error'))
    if user.email_verified_at:
        return redirect(url_for('account', notice='Your email is already verified.', level='success'))
    _queue_account_email(user.user_id, 'verification')
    audit_event('email_verification_resent', user, 'info')
    return redirect(url_for('account', notice='If delivery is configured, a verification link is on its way.', level='success'))


@login_required
def enable_email_two_factor_route():
    from services import enable_email_two_factor
    user = _current_user()
    if not current_app.config.get('PASSWORD_RESET_EMAILS_ENABLED'):
        return render_template('account.html', user=user, error='Email delivery is not configured.'), 400
    recovery_codes = enable_email_two_factor(user, _request_data().get('current_password') or '')
    if not recovery_codes:
        return render_template('account.html', user=user, error='Verify your email and enter your current password to enable email 2FA.'), 400
    audit_event('two_factor_enabled', user, 'success', method='email')
    session['auth_version'] = user.auth_version
    return render_template(
        'account.html', user=user, recovery_codes=recovery_codes,
        success='Email two-factor authentication is enabled. Save your recovery codes now.',
    )


@login_required
def begin_totp_setup_route():
    from services import begin_totp_setup
    user = _current_user()
    setup = begin_totp_setup(user, _request_data().get('current_password') or '')
    if not setup:
        return render_template('account.html', user=user, error='Enter your current password to begin authenticator-app setup.'), 400
    secret, provisioning_uri = setup
    return render_template('account.html', user=user, totp_setup_secret=secret, totp_provisioning_uri=provisioning_uri)


@login_required
def confirm_totp_setup_route():
    from services import confirm_totp_setup
    user = _current_user()
    recovery_codes = confirm_totp_setup(user, _request_data().get('code') or '')
    if not recovery_codes:
        return render_template('account.html', user=user, error='That authenticator code is invalid. Try again.'), 400
    audit_event('two_factor_enabled', user, 'success', method='totp')
    session['auth_version'] = user.auth_version
    return render_template(
        'account.html', user=user, recovery_codes=recovery_codes,
        success='Authenticator-app two-factor authentication is enabled. Save your recovery codes now.',
    )


@login_required
def regenerate_two_factor_recovery_codes_route():
    from services import regenerate_two_factor_recovery_codes

    user = _current_user()
    recovery_codes = regenerate_two_factor_recovery_codes(
        user, _request_data().get('current_password') or '',
    )
    if not recovery_codes:
        return render_template(
            'account.html', user=user,
            error='Enter your current password to replace your recovery codes.',
        ), 400
    audit_event('two_factor_recovery_codes_regenerated', user, 'success')
    session['auth_version'] = user.auth_version
    return render_template(
        'account.html', user=user, recovery_codes=recovery_codes,
        success='New recovery codes generated. All previous codes are now invalid.',
    )


@login_required
def disable_two_factor_route():
    from services import disable_two_factor
    user = _current_user()
    if not disable_two_factor(user, _request_data().get('current_password') or ''):
        return render_template('account.html', user=user, error='Enter your current password to disable two-factor authentication.'), 400
    audit_event('two_factor_disabled', user, 'success')
    session['auth_version'] = user.auth_version
    return redirect(url_for('account', notice='Two-factor authentication is disabled.', level='success'))


@login_required
def delete_account():
    from services import delete_user_account

    user = _current_user()
    data = _request_data()
    current_password = data.get('current_password') or ''
    confirmation = (data.get('confirmation') or '').strip()

    if confirmation != 'DELETE':
        return render_template(
            'account.html',
            user=user,
            delete_error='Type DELETE to confirm account deletion.',
        ), 400
    if not user.check_password(current_password):
        return render_template(
            'account.html',
            user=user,
            delete_error='Enter your current password to delete your account.',
        ), 400

    deleted_user_id = user.user_id
    delete_user_account(deleted_user_id)
    session.clear()
    current_app.logger.info('self_service_account_deleted user_id=%s', deleted_user_id)
    return redirect(url_for('index', notice='Your account and owned content were deleted.', level='success'))


def update_theme_route():
    user = _current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    data = _request_data()
    theme = (data.get('theme') or '').strip().lower()
    if theme not in ('light', 'dark'):
        return jsonify({'error': 'Invalid theme'}), 400

    user.theme_preference = theme
    from models import db
    db.session.commit()
    return jsonify({'success': True, 'theme': user.theme_preference})


@login_required
def update_study_reminder_route():
    data = _request_data()
    enabled = _as_bool(data.get('enabled', False))
    reminder_time = str(data.get('reminder_time') or '').strip()
    match = re.fullmatch(r'([01]\d|2[0-3]):([0-5]\d)', reminder_time)
    if not match:
        return render_template(
            'account.html', user=_current_user(),
            error='Choose a valid reminder time.',
        ), 400
    user = _current_user()
    user.study_reminder_enabled = enabled
    user.study_reminder_minutes = int(match.group(1)) * 60 + int(match.group(2))
    db.session.commit()
    return redirect(url_for(
        'account', notice='Study reminder updated.', level='success',
    ))


@admin_required
def admin_users():
    from services import delete_user_account
    from models import User, db

    if request.method == 'GET':
        page = _requested_page()
        per_page = _requested_page_size()
        users = User.query.order_by(User.user_id.asc()).limit(per_page + 1).offset((page - 1) * per_page).all()
        has_next = len(users) > per_page
        users = users[:per_page]
        pagination = {
            'page': page, 'per_page': per_page, 'has_prev': page > 1, 'has_next': has_next,
            'prev_page': page - 1 if page > 1 else None, 'next_page': page + 1 if has_next else None,
        }
        return render_template('admin_users.html', users=users, pagination=pagination, **_pagination_context('admin_users'))

    data = _request_data()
    action = (data.get('action') or 'promote_admin').strip().lower()
    target_user_id = _int_value(data.get('user_id'))
    if not target_user_id:
        return redirect(url_for('admin_users', notice='User ID is required.', level='error'))

    target_user = db.session.get(User, target_user_id)
    if not target_user:
        return redirect(url_for('admin_users', notice='User not found.', level='error'))
    current_user = _current_user()

    if action == 'delete':
        if current_user and current_user.user_id == target_user.user_id:
            return redirect(url_for('admin_users', notice='You cannot delete your own account.', level='error'))
        delete_user_account(target_user.user_id)
        audit_event('account_deleted', current_user, 'success', target_type='user', target_id=target_user_id)
        current_app.logger.info('admin_action=delete_user actor_id=%s target_id=%s', current_user.user_id, target_user_id)
        return redirect(url_for('admin_users', notice='User and all owned data deleted.', level='success'))

    if action == 'promote_admin':
        if target_user.role == 'admin':
            return redirect(url_for('admin_users', notice='User is already an admin.', level='success'))
        target_user.role = 'admin'
        db.session.commit()
        audit_event('role_changed', current_user, 'success', target_type='user', target_id=target_user_id, role='admin')
        current_app.logger.info('admin_action=promote_admin actor_id=%s target_id=%s', current_user.user_id, target_user_id)
        return redirect(url_for('admin_users', notice='User promoted to admin.', level='success'))

    if action == 'promote_moderator':
        if target_user.role == 'moderator':
            return redirect(url_for('admin_users', notice='User is already a moderator.', level='success'))
        if target_user.role == 'admin':
            return redirect(url_for('admin_users', notice='Admins cannot be changed to moderator here.', level='error'))
        target_user.role = 'moderator'
        db.session.commit()
        audit_event('role_changed', current_user, 'success', target_type='user', target_id=target_user_id, role='moderator')
        current_app.logger.info('admin_action=promote_moderator actor_id=%s target_id=%s', current_user.user_id, target_user_id)
        return redirect(url_for('admin_users', notice='User promoted to moderator.', level='success'))

    if action == 'demote_standard':
        if current_user and current_user.user_id == target_user.user_id:
            return redirect(url_for('admin_users', notice='You cannot demote your own account.', level='error'))
        if target_user.role == 'standard':
            return redirect(url_for('admin_users', notice='User is already standard.', level='success'))
        if target_user.role == 'admin':
            return redirect(url_for('admin_users', notice='Use a dedicated admin-role workflow to demote admins.', level='error'))
        target_user.role = 'standard'
        db.session.commit()
        audit_event('role_changed', current_user, 'success', target_type='user', target_id=target_user_id, role='standard')
        current_app.logger.info('admin_action=demote_standard actor_id=%s target_id=%s', current_user.user_id, target_user_id)
        return redirect(url_for('admin_users', notice='User demoted to standard.', level='success'))

    return redirect(url_for('admin_users', notice='Unknown admin action.', level='error'))


@admin_required
def admin_audit_log():
    from models import AuditLog

    event = (request.args.get('event') or '').strip()
    actor_id = _int_value(request.args.get('actor_id'))
    outcome = (request.args.get('outcome') or '').strip().lower()
    query = AuditLog.query
    if event:
        query = query.filter(AuditLog.event == event[:80])
    if actor_id:
        query = query.filter(AuditLog.actor_id == actor_id)
    if outcome in ('success', 'failure', 'info'):
        query = query.filter(AuditLog.outcome == outcome)
    query = query.order_by(AuditLog.occurred_at.desc(), AuditLog.log_id.desc())

    if request.args.get('format') == 'csv':
        rows = query.limit(10_000).all()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['id', 'occurred_at', 'actor_id', 'event', 'outcome', 'target_type', 'target_id', 'ip_address', 'metadata'])
        for row in rows:
            writer.writerow([
                spreadsheet_safe_cell(value)
                for value in (
                    row.log_id, row.occurred_at, row.actor_id, row.event, row.outcome,
                    row.target_type, row.target_id, row.ip_address, row.metadata_json,
                )
            ])
        return Response(output.getvalue(), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=audit-log.csv'})

    page = _requested_page()
    per_page = _requested_page_size()
    rows = query.limit(per_page + 1).offset((page - 1) * per_page).all()
    has_next = len(rows) > per_page
    rows = rows[:per_page]
    pagination = {
        'page': page, 'per_page': per_page, 'has_prev': page > 1, 'has_next': has_next,
        'prev_page': page - 1 if page > 1 else None, 'next_page': page + 1 if has_next else None,
    }
    return render_template(
        'admin_audit_log.html', logs=rows, event=event, actor_id=actor_id or '', outcome=outcome,
        pagination=pagination, **_pagination_context('admin_audit_log'),
    )


@admin_required
def admin_featured_content_route():
    from models import Deck, User

    selected_status = (request.args.get('status') or 'all').strip().lower()
    if selected_status not in ('all', 'featured', 'unfeatured'):
        selected_status = 'all'
    search_query = (request.args.get('q') or '').strip()[:100]

    if request.method == 'POST':
        data = _request_data()
        deck_id = _int_value(data.get('deck_id'))
        action = (data.get('action') or '').strip().lower()
        deck = db.session.get(Deck, deck_id)
        if not deck:
            return redirect(url_for(
                'admin_featured_content', q=search_query, status=selected_status,
                notice='Deck not found.', level='error',
            ))
        if action not in ('feature', 'unfeature'):
            return redirect(url_for(
                'admin_featured_content', q=search_query, status=selected_status,
                notice='Choose a valid featured-content action.', level='error',
            ))
        should_feature = action == 'feature'
        if should_feature and not deck.is_public:
            return redirect(url_for(
                'admin_featured_content', q=search_query, status=selected_status,
                notice='Only public decks can be featured.', level='error',
            ))
        if deck.is_featured == should_feature:
            state = 'featured' if should_feature else 'not featured'
            return redirect(url_for(
                'admin_featured_content', q=search_query, status=selected_status,
                notice=f'Deck is already {state}.', level='success',
            ))

        deck.is_featured = should_feature
        db.session.commit()
        audit_event(
            'deck_featured_changed', _current_user(), 'success',
            target_type='deck', target_id=deck.deck_id, featured=should_feature,
        )
        state = 'featured' if should_feature else 'removed from featured content'
        return redirect(url_for(
            'admin_featured_content', q=search_query, status=selected_status,
            notice=f'Deck {state}.', level='success',
        ))

    query = Deck.query.join(User, Deck.owned_by == User.user_id).options(
        joinedload(Deck.owner),
    ).filter(Deck.is_public == True)
    if selected_status == 'featured':
        query = query.filter(Deck.is_featured == True)
    elif selected_status == 'unfeatured':
        query = query.filter(Deck.is_featured == False)
    if search_query:
        escaped_query = search_query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        pattern = f'%{escaped_query}%'
        query = query.filter(db.or_(
            Deck.description.ilike(pattern, escape='\\'),
            User.username.ilike(pattern, escape='\\'),
        ))
    pagination = _query_page(
        query.order_by(Deck.is_featured.desc(), Deck.deck_id.asc()),
        _requested_page(),
        _requested_page_size(),
    )
    return render_template(
        'admin_featured_content.html', decks=pagination['items'],
        selected_status=selected_status, search_query=search_query,
        pagination=pagination, **_pagination_context('admin_featured_content'),
    )


@moderator_required
def moderation_reports_route():
    """Review and resolve user-submitted public deck reports."""
    from models import DeckReport

    if request.method == 'POST':
        data = _request_data()
        report_id = _int_value(data.get('report_id'))
        action = (data.get('action') or '').strip().lower()
        report = db.session.get(DeckReport, report_id)
        if not report:
            return jsonify({'error': 'Report not found'}), 404

        actor = _current_user()
        if action == 'reopen':
            report.status = 'open'
            report.resolved_by = None
            report.resolved_at = None
            report.resolution_note = None
        elif action in ('resolve', 'dismiss', 'unpublish'):
            if report.status != 'open':
                return jsonify({'error': 'Only open reports can be resolved or dismissed'}), 400
            resolution_note = (data.get('resolution_note') or '').strip()[:500] or None
            if action == 'unpublish':
                _suspend_moderated_content('deck', report.deck)
                affected_reports = DeckReport.query.filter_by(
                    deck_id=report.deck_id, status='open',
                ).all()
            else:
                affected_reports = [report]
            resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
            for affected_report in affected_reports:
                affected_report.status = 'dismissed' if action == 'dismiss' else 'resolved'
                affected_report.resolved_by = actor.user_id
                affected_report.resolved_at = resolved_at
                affected_report.resolution_note = resolution_note
        else:
            return jsonify({'error': 'Choose a valid moderation action'}), 400

        db.session.commit()
        audit_event(
            f'deck_report_{action}', actor, 'success',
            target_type='deck_report', target_id=report.report_id,
            deck_id=report.deck_id, status=report.status,
        )
        if _wants_json():
            return jsonify({
                'success': True,
                'report_id': report.report_id,
                'status': report.status,
            })
        return redirect(url_for(
            'moderation_reports',
            status=request.args.get('status', 'open'),
            reason=request.args.get('reason', ''),
            notice='Report updated.',
            level='success',
        ))

    selected_status = (request.args.get('status') or 'open').strip().lower()
    selected_reason = (request.args.get('reason') or '').strip().lower()
    if selected_status not in ('open', 'resolved', 'dismissed', 'all'):
        selected_status = 'open'
    if selected_reason not in ('spam', 'copyright', 'inaccurate', 'other', ''):
        selected_reason = ''

    query = DeckReport.query.options(
        joinedload(DeckReport.deck),
        joinedload(DeckReport.reporter),
        joinedload(DeckReport.resolver),
    )
    if selected_status != 'all':
        query = query.filter(DeckReport.status == selected_status)
    if selected_reason:
        query = query.filter(DeckReport.reason == selected_reason)
    query = query.order_by(DeckReport.created_at.asc(), DeckReport.report_id.asc())

    page = _requested_page()
    per_page = _requested_page_size()
    rows = query.limit(per_page + 1).offset((page - 1) * per_page).all()
    has_next = len(rows) > per_page
    reports = rows[:per_page]
    pagination = {
        'page': page, 'per_page': per_page, 'has_prev': page > 1, 'has_next': has_next,
        'prev_page': page - 1 if page > 1 else None,
        'next_page': page + 1 if has_next else None,
    }
    counts = dict(
        db.session.query(DeckReport.status, db.func.count(DeckReport.report_id))
        .group_by(DeckReport.status)
        .all()
    )
    return render_template(
        'moderation_reports.html', reports=reports, counts=counts,
        selected_status=selected_status, selected_reason=selected_reason,
        pagination=pagination, **_pagination_context('moderation_reports'),
    )


@moderator_required
def moderation_quiz_reports_route():
    from models import QuizReport

    if request.method == 'POST':
        data = _request_data()
        report_id = _int_value(data.get('report_id'))
        action = (data.get('action') or '').strip().lower()
        report = db.session.get(QuizReport, report_id)
        if not report:
            return jsonify({'error': 'Report not found'}), 404
        actor = _current_user()
        if action == 'reopen':
            report.status = 'open'
            report.resolved_by = None
            report.resolved_at = None
            report.resolution_note = None
        elif action in ('resolve', 'dismiss', 'unpublish'):
            if report.status != 'open':
                return jsonify({
                    'error': 'Only open reports can be resolved or dismissed',
                }), 400
            resolution_note = (data.get('resolution_note') or '').strip()[:500] or None
            if action == 'unpublish':
                _suspend_moderated_content('quiz', report.quiz)
                affected_reports = QuizReport.query.filter_by(
                    quiz_id=report.quiz_id, status='open',
                ).all()
            else:
                affected_reports = [report]
            resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
            for affected_report in affected_reports:
                affected_report.status = (
                    'dismissed' if action == 'dismiss' else 'resolved'
                )
                affected_report.resolved_by = actor.user_id
                affected_report.resolved_at = resolved_at
                affected_report.resolution_note = resolution_note
        else:
            return jsonify({'error': 'Choose a valid moderation action'}), 400
        db.session.commit()
        audit_event(
            f'quiz_report_{action}', actor, 'success',
            target_type='quiz_report', target_id=report.report_id,
            quiz_id=report.quiz_id, status=report.status,
        )
        if _wants_json():
            return jsonify({
                'success': True, 'report_id': report.report_id,
                'status': report.status,
            })
        return redirect(url_for(
            'moderation_quiz_reports',
            status=request.args.get('status', 'open'),
            reason=request.args.get('reason', ''),
            notice='Report updated.', level='success',
        ))

    selected_status = (request.args.get('status') or 'open').strip().lower()
    selected_reason = (request.args.get('reason') or '').strip().lower()
    if selected_status not in ('open', 'resolved', 'dismissed', 'all'):
        selected_status = 'open'
    if selected_reason not in ('spam', 'copyright', 'inaccurate', 'other', ''):
        selected_reason = ''
    query = QuizReport.query.options(
        joinedload(QuizReport.quiz), joinedload(QuizReport.reporter),
        joinedload(QuizReport.resolver),
    )
    if selected_status != 'all':
        query = query.filter(QuizReport.status == selected_status)
    if selected_reason:
        query = query.filter(QuizReport.reason == selected_reason)
    query = query.order_by(
        QuizReport.created_at.asc(), QuizReport.report_id.asc(),
    )
    pagination = _query_page(
        query, _requested_page(), _requested_page_size(),
    )
    counts = dict(
        db.session.query(QuizReport.status, db.func.count(QuizReport.report_id))
        .group_by(QuizReport.status).all()
    )
    return render_template(
        'moderation_quiz_reports.html', reports=pagination['items'], counts=counts,
        selected_status=selected_status, selected_reason=selected_reason,
        pagination=pagination, **_pagination_context('moderation_quiz_reports'),
    )


def _suspend_moderated_content(content_type, content):
    """Suspend content and atomically revoke every unlisted access token."""
    from models import DeckShareLink, QuizShareLink

    content.is_public = False
    content.is_suspended = True
    if content_type == 'deck':
        content.is_featured = False
        DeckShareLink.query.filter_by(deck_id=content.deck_id).delete(
            synchronize_session=False,
        )
    else:
        QuizShareLink.query.filter_by(quiz_id=content.quiz_id).delete(
            synchronize_session=False,
        )


@moderator_required
def moderate_unpublish_route():
    """The only public-content mutation granted to moderators."""
    from models import Deck, Quiz

    data = _request_data()
    content_type = (data.get('content_type') or '').strip().lower()
    content_id = _int_value(data.get('content_id'))
    model = Deck if content_type == 'deck' else Quiz if content_type == 'quiz' else None
    if model is None or not content_id:
        return jsonify({'error': 'content_type and content_id are required'}), 400

    content = db.session.get(model, content_id)
    if not content or not content.is_public:
        return jsonify({'error': 'Public content not found'}), 404

    actor = _current_user()
    _suspend_moderated_content(content_type, content)
    db.session.commit()
    audit_event(
        'public_content_unpublished',
        actor,
        'success',
        target_type=content_type,
        target_id=content_id,
    )
    return jsonify({'success': True, 'content_type': content_type, 'content_id': content_id})


def _owned_deck(deck_id, user_id):
    from models import Deck, DeckCollaborator
    if not deck_id or not user_id:
        return None
    return Deck.query.outerjoin(
        DeckCollaborator, DeckCollaborator.deck_id == Deck.deck_id
    ).filter(
        Deck.deck_id == deck_id,
        (Deck.owned_by == user_id) | (DeckCollaborator.user_id == user_id),
    ).first()


def _is_deck_owner(deck_id, user_id):
    from models import Deck
    return bool(deck_id and user_id and Deck.query.filter_by(deck_id=deck_id, owned_by=user_id).first())


def _directly_accessible_deck(deck_id, user_id):
    """Allow an owned deck or an explicitly linked public deck."""
    from models import Deck, DeckCollaborator, db
    if not deck_id:
        return None
    deck = db.session.get(Deck, deck_id)
    is_collaborator = bool(user_id and DeckCollaborator.query.filter_by(deck_id=deck_id, user_id=user_id).first())
    if not deck or (deck.owned_by != user_id and not is_collaborator and not deck.is_public):
        return None
    return deck


def _owned_quiz(quiz_id, user_id):
    from models import Quiz, QuizCollaborator
    if not quiz_id or not user_id:
        return None
    return Quiz.query.outerjoin(
        QuizCollaborator, QuizCollaborator.quiz_id == Quiz.quiz_id,
    ).filter(
        Quiz.quiz_id == quiz_id,
        (Quiz.owned_by == user_id) | (QuizCollaborator.user_id == user_id),
    ).first()


def _is_quiz_owner(quiz_id, user_id):
    from models import Quiz
    return bool(
        quiz_id and user_id
        and Quiz.query.filter_by(quiz_id=quiz_id, owned_by=user_id).first()
    )


def _directly_accessible_quiz(quiz_id, user_id):
    from models import Quiz, QuizCollaborator
    if not quiz_id:
        return None
    quiz = db.session.get(Quiz, quiz_id)
    is_collaborator = bool(
        user_id and QuizCollaborator.query.filter_by(
            quiz_id=quiz_id, user_id=user_id,
        ).first()
    )
    if not quiz or (
        quiz.owned_by != user_id and not is_collaborator and not quiz.is_public
    ):
        return None
    return quiz


def _accessible_answer(answer_id, user_id):
    from models import CardAnswer
    answer = db.session.get(CardAnswer, answer_id) if answer_id else None
    if not answer or not _directly_accessible_deck(answer.card.deck_id, user_id):
        return None
    return answer


# Page routes.
# Render the home page.
def index():
    from services import get_homepage_public_data, get_public_deck_preview_state

    homepage_data = get_homepage_public_data(featured_limit=3, tag_limit=5)
    featured_decks = homepage_data['featured_decks']
    return render_template(
        'index.html',
        featured_decks=featured_decks,
        featured_tags=homepage_data['featured_tags'],
        deck_preview_state=get_public_deck_preview_state(
            (deck['deck_id'] for deck in featured_decks), _current_user_id(),
        ),
    )


@login_required
def dashboard():
    from services import get_dashboard_data

    user = _current_user()
    return render_template('dashboard.html', dashboard=get_dashboard_data(user.user_id))


@login_required
def saved_decks_route():
    """List the signed-in user's currently public bookmarked decks."""
    from models import Deck, DeckFavorite
    from services import get_public_deck_preview_state

    page = _requested_page()
    per_page = _requested_page_size()
    rows = db.session.query(
        DeckFavorite, Deck,
    ).join(
        Deck, Deck.deck_id == DeckFavorite.deck_id,
    ).filter(
        DeckFavorite.user_id == _current_user_id(),
        Deck.is_public == True,
    ).order_by(
        DeckFavorite.created_at.desc(), DeckFavorite.deck_id.desc(),
    ).limit(per_page + 1).offset((page - 1) * per_page).all()
    has_next = len(rows) > per_page
    saved_decks = [
        {'favorite': favorite, 'deck': deck}
        for favorite, deck in rows[:per_page]
    ]
    pagination = {
        'page': page, 'per_page': per_page, 'has_prev': page > 1, 'has_next': has_next,
        'prev_page': page - 1 if page > 1 else None,
        'next_page': page + 1 if has_next else None,
    }
    return render_template(
        'saved_decks.html', saved_decks=saved_decks, pagination=pagination,
        deck_preview_state=get_public_deck_preview_state(
            (item['deck'].deck_id for item in saved_decks), _current_user_id(),
        ),
        **_pagination_context('saved_decks'),
    )


@login_required
def saved_quizzes_route():
    from models import Quiz, QuizFavorite, QuizQuestion

    page = _requested_page()
    per_page = _requested_page_size()
    question_count = db.session.query(
        db.func.count(QuizQuestion.question_id),
    ).filter(
        QuizQuestion.quiz_id == Quiz.quiz_id,
    ).correlate(Quiz).scalar_subquery()
    rows = db.session.query(
        QuizFavorite, Quiz, question_count.label('question_count'),
    ).join(
        Quiz, Quiz.quiz_id == QuizFavorite.quiz_id,
    ).filter(
        QuizFavorite.user_id == _current_user_id(),
        Quiz.is_public == True,
    ).order_by(
        QuizFavorite.created_at.desc(), QuizFavorite.quiz_id.desc(),
    ).limit(per_page + 1).offset((page - 1) * per_page).all()
    has_next = len(rows) > per_page
    pagination = {
        'page': page, 'per_page': per_page, 'has_prev': page > 1,
        'has_next': has_next,
        'prev_page': page - 1 if page > 1 else None,
        'next_page': page + 1 if has_next else None,
    }
    return render_template(
        'saved_quizzes.html', saved_quizzes=rows[:per_page],
        pagination=pagination, **_pagination_context('saved_quizzes'),
    )


@login_required
def collections_route():
    """Manage the signed-in user's ordered deck collections."""
    from models import CuratedCollection, CuratedCollectionDeck, Deck, DeckFavorite

    user_id = _current_user_id()
    page = _requested_page()
    per_page = _requested_page_size()
    query = CuratedCollection.query.options(
        selectinload(CuratedCollection.entries).joinedload(CuratedCollectionDeck.deck),
    ).filter_by(
        owned_by=user_id,
    ).order_by(
        CuratedCollection.created_at.desc(), CuratedCollection.collection_id.desc(),
    )
    rows = query.limit(per_page + 1).offset((page - 1) * per_page).all()
    has_next = len(rows) > per_page
    collections = rows[:per_page]
    selected_id = _int_value(request.args.get('collection_id'))
    selected_collection = next(
        (collection for collection in collections if collection.collection_id == selected_id),
        None,
    )
    if selected_id and not selected_collection:
        selected_collection = CuratedCollection.query.options(
            selectinload(CuratedCollection.entries).joinedload(CuratedCollectionDeck.deck),
        ).filter_by(collection_id=selected_id, owned_by=user_id).first()
    if not selected_collection and collections:
        selected_collection = collections[0]

    available_decks = []
    selected_entries = []
    hidden_entries = []
    if selected_collection:
        selected_entries = [
            entry for entry in selected_collection.entries
            if entry.deck.is_public or entry.deck.owned_by == user_id
        ]
        hidden_entries = [
            entry for entry in selected_collection.entries
            if not entry.deck.is_public and entry.deck.owned_by != user_id
        ]
        selected_deck_ids = {entry.deck_id for entry in selected_collection.entries}
        owned_decks = Deck.query.filter_by(owned_by=user_id).order_by(
            Deck.description.asc(), Deck.deck_id.asc(),
        ).limit(50).all()
        saved_public_decks = Deck.query.join(
            DeckFavorite, DeckFavorite.deck_id == Deck.deck_id,
        ).filter(
            DeckFavorite.user_id == user_id,
            Deck.is_public == True,
        ).order_by(
            DeckFavorite.created_at.desc(), Deck.deck_id.desc(),
        ).limit(50).all()
        seen = set()
        for deck in [*owned_decks, *saved_public_decks]:
            if deck.deck_id in selected_deck_ids or deck.deck_id in seen:
                continue
            seen.add(deck.deck_id)
            available_decks.append(deck)

    pagination = {
        'page': page, 'per_page': per_page, 'has_prev': page > 1, 'has_next': has_next,
        'prev_page': page - 1 if page > 1 else None,
        'next_page': page + 1 if has_next else None,
    }
    return render_template(
        'collections.html', collections=collections,
        selected_collection=selected_collection, available_decks=available_decks,
        selected_entries=selected_entries, hidden_entries=hidden_entries,
        pagination=pagination, **_pagination_context('collections'),
    )


@login_required
def create_collection_route():
    from services import create_curated_collection

    data = _request_data()
    try:
        collection = create_curated_collection(
            _current_user_id(), data.get('title'), data.get('description'),
            _as_bool(data.get('is_public')),
        )
    except ValueError as exc:
        return redirect(url_for('collections', notice=str(exc), level='error'))
    return redirect(url_for(
        'collections', collection_id=collection.collection_id,
        notice='Collection created.', level='success',
    ))


@login_required
def edit_collection_route():
    from services import edit_curated_collection

    data = _request_data()
    collection_id = _int_value(data.get('collection_id'))
    try:
        collection = edit_curated_collection(
            collection_id, _current_user_id(), data.get('title'), data.get('description'),
            _as_bool(data.get('is_public')),
        )
    except ValueError as exc:
        return redirect(url_for(
            'collections', collection_id=collection_id, notice=str(exc), level='error',
        ))
    if not collection:
        return jsonify({'error': 'Collection not found'}), 404
    return redirect(url_for(
        'collections', collection_id=collection.collection_id,
        notice='Collection saved.', level='success',
    ))


@login_required
def delete_collection_route():
    from services import delete_curated_collection

    collection_id = _int_value(_request_data().get('collection_id'))
    if not delete_curated_collection(collection_id, _current_user_id()):
        return jsonify({'error': 'Collection not found'}), 404
    return redirect(url_for(
        'collections', notice='Collection deleted.', level='success',
    ))


@login_required
def add_collection_deck_route():
    from services import add_deck_to_collection

    data = _request_data()
    collection_id = _int_value(data.get('collection_id'))
    try:
        add_deck_to_collection(
            collection_id, _int_value(data.get('deck_id')), _current_user_id(),
        )
    except ValueError as exc:
        return redirect(url_for(
            'collections', collection_id=collection_id, notice=str(exc), level='error',
        ))
    return redirect(url_for(
        'collections', collection_id=collection_id,
        notice='Deck added to collection.', level='success',
    ))


@login_required
def remove_collection_deck_route():
    from services import remove_deck_from_collection

    data = _request_data()
    collection_id = _int_value(data.get('collection_id'))
    if not remove_deck_from_collection(
        collection_id, _int_value(data.get('deck_id')), _current_user_id(),
    ):
        return jsonify({'error': 'Collection entry not found'}), 404
    return redirect(url_for(
        'collections', collection_id=collection_id,
        notice='Deck removed from collection.', level='success',
    ))


@login_required
def move_collection_deck_route():
    from services import move_collection_deck

    data = _request_data()
    collection_id = _int_value(data.get('collection_id'))
    try:
        moved = move_collection_deck(
            collection_id, _int_value(data.get('deck_id')), _current_user_id(),
            (data.get('direction') or '').strip().lower(),
        )
    except ValueError as exc:
        return redirect(url_for(
            'collections', collection_id=collection_id, notice=str(exc), level='error',
        ))
    return redirect(url_for(
        'collections', collection_id=collection_id,
        notice='Collection order updated.' if moved else 'Deck is already at the edge.',
        level='success' if moved else 'info',
    ))


def public_collection_route(collection_id):
    from models import CuratedCollection, CuratedCollectionDeck
    from services import get_public_deck_preview_state

    collection = CuratedCollection.query.options(
        selectinload(CuratedCollection.entries).joinedload(CuratedCollectionDeck.deck),
        joinedload(CuratedCollection.owner),
    ).filter_by(collection_id=collection_id).first_or_404()
    user_id = _current_user_id()
    is_owner = collection.owned_by == user_id
    if not collection.is_public and not is_owner:
        abort(404)
    visible_entries = [
        entry for entry in collection.entries
        if entry.deck.is_public or entry.deck.owned_by == user_id
    ]
    return render_template(
        'public_collection.html', collection=collection,
        entries=visible_entries, is_owner=is_owner,
        deck_preview_state=get_public_deck_preview_state(
            (entry.deck_id for entry in visible_entries if entry.deck.is_public),
            user_id,
        ),
    )


# Deck editor.
# Render the deck editor page.
def edit():
    if not _current_user():
        return _login_required_response()
    user_id = _current_user_id()
    from services import get_user_decks_page, get_deck_details

    deck_page = get_user_decks_page(user_id, _requested_page(), _requested_page_size())
    decks = deck_page['items']
    deck_data = [_deck_summary_payload(deck, user_id) for deck in decks]

    selected_deck_id = _int_value(request.args.get('deck_id'))
    selected_deck = None
    selected_deck_export_text = ''
    selected_cards = []
    if selected_deck_id and _owned_deck(selected_deck_id, user_id):
        from services import export_deck_as_text
        selected_deck = get_deck_details(selected_deck_id, shuffle_cards=False, shuffle_answers=False)
        if selected_deck and selected_deck['deck_id']:
            selected_cards = selected_deck['cards']
            selected_deck_record = _owned_deck(selected_deck_id, user_id)
            selected_deck_export_text = export_deck_as_text(selected_deck_record) if selected_deck_record else ''
        else:
            selected_deck = None
    selected_deck_record = _owned_deck(selected_deck_id, user_id) if selected_deck_id else None
    deck_data = _include_selected_deck(deck_data, selected_deck_record, user_id)
    collaborators = []
    share_links = []
    bulk_target_decks = []
    if selected_deck:
        bulk_target_decks = [
            deck for deck in get_user_decks_page(user_id, 1, 50)['items']
            if deck.deck_id != selected_deck_id
        ]
    selected_deck_is_suspended = bool(
        selected_deck_record and selected_deck_record.is_suspended
    )
    can_change_visibility = bool(
        _is_deck_owner(selected_deck_id, user_id)
        and not selected_deck_is_suspended
    )
    can_manage_sharing = can_change_visibility
    if can_manage_sharing:
        from models import DeckCollaborator, DeckShareLink
        collaborators = DeckCollaborator.query.filter_by(deck_id=selected_deck_id).order_by(DeckCollaborator.created_at.asc()).all()
        share_links = DeckShareLink.query.filter_by(deck_id=selected_deck_id).order_by(DeckShareLink.created_at.desc()).all()

    return render_template(
        'edit.html',
        user_id=user_id,
        decks=deck_data,
        selected_deck=selected_deck,
        selected_cards=selected_cards,
        selected_deck_id=selected_deck_id,
        selected_deck_export_text=selected_deck_export_text,
        selected_deck_is_suspended=selected_deck_is_suspended,
        can_change_visibility=can_change_visibility,
        can_manage_sharing=can_manage_sharing,
        collaborators=collaborators,
        share_links=share_links,
        bulk_target_decks=bulk_target_decks,
        deck_page=deck_page,
        **_pagination_context('edit'),
    )


# Study view.
# Render the study page.
def view():
    user_id = _current_user_id()
    from services import get_deck_details, get_user_decks_page

    deck_page = get_user_decks_page(user_id, _requested_page(), _requested_page_size()) if user_id is not None else None
    decks = deck_page['items'] if deck_page else []
    deck_data = [_deck_summary_payload(deck, user_id) for deck in decks]

    selected_deck_id = _int_value(request.args.get('deck_id'))
    selected_deck_record = _directly_accessible_deck(selected_deck_id, user_id)
    if not selected_deck_record:
        selected_deck_id = None
    deck_data = _include_selected_deck(deck_data, selected_deck_record, user_id)
    study_deck = get_deck_details(selected_deck_id, shuffle_cards=False, shuffle_answers=False) if selected_deck_id else None
    selected_deck_is_owned = bool(selected_deck_record and selected_deck_record.owned_by == user_id)

    return render_template('view.html', user_id=user_id, decks=deck_data, study_deck=study_deck, selected_deck_id=selected_deck_id, selected_deck_is_owned=selected_deck_is_owned, deck_page=deck_page, **_pagination_context('view'))


# Matching game.
# Render the matching game page.
def match():
    user = _current_user()
    user_id = user.user_id if user else None
    from services import get_match_game_data, get_match_strategy_catalog, get_user_decks_page, normalize_match_strategy

    deck_page = get_user_decks_page(user_id, _requested_page(), _requested_page_size()) if user_id is not None else None
    decks = deck_page['items'] if deck_page else []
    deck_data = [_deck_summary_payload(deck, user_id) for deck in decks]

    selected_deck_id = _int_value(request.args.get('deck_id'))
    selected_question_id = _int_value(request.args.get('selected_question'))
    error_message = request.args.get('error')
    selected_deck_record = _directly_accessible_deck(selected_deck_id, user_id)
    if not selected_deck_record:
        selected_deck_id = None
    deck_data = _include_selected_deck(deck_data, selected_deck_record, user_id)
    match_strategy_catalog = get_match_strategy_catalog(include_account_only=bool(user))
    selected_strategy = normalize_match_strategy(
        request.args.get('strategy') or (user.match_strategy_preference if user else None),
        include_account_only=bool(user),
    )
    match_deck = get_match_game_data(user_id, selected_deck_id, strategy=selected_strategy) if selected_deck_id else None
    selected_deck_is_owned = bool(selected_deck_record and selected_deck_record.owned_by == user_id)

    return render_template(
        'match.html',
        user_id=user_id,
        decks=deck_data,
        match_deck=match_deck,
        selected_deck_id=selected_deck_id,
        selected_deck_is_owned=selected_deck_is_owned,
        selected_question_id=selected_question_id,
        error_message=error_message,
        selected_strategy=selected_strategy,
        match_strategy_catalog=match_strategy_catalog,
        deck_page=deck_page,
        **_pagination_context('match'),
    )


# Render the reorder game page.
def reorder():
    user_id = _current_user_id()
    from services import get_deck_details, get_user_decks_page

    deck_page = get_user_decks_page(user_id, _requested_page(), _requested_page_size(), sortable_only=True) if user_id is not None else None
    sortable_decks = deck_page['items'] if deck_page else []
    deck_data = [_deck_summary_payload(deck, user_id) for deck in sortable_decks]

    selected_deck_id = _int_value(request.args.get('deck_id'))
    selected_deck_record = _directly_accessible_deck(selected_deck_id, user_id)
    if not selected_deck_record or not selected_deck_record.sortable:
        selected_deck_id = None
    deck_data = _include_selected_deck(deck_data, selected_deck_record if selected_deck_record and selected_deck_record.sortable else None, user_id)

    # Start each round with a shuffled card list.
    reorder_deck = get_deck_details(selected_deck_id, shuffle_cards=True, shuffle_answers=False) if selected_deck_id else None
    selected_deck_is_owned = bool(selected_deck_record and selected_deck_record.owned_by == user_id)

    return render_template(
        'reorder.html',
        user_id=user_id,
        decks=deck_data,
        reorder_deck=reorder_deck,
        selected_deck_id=selected_deck_id,
        selected_deck_is_owned=selected_deck_is_owned,
        deck_page=deck_page,
        **_pagination_context('reorder'),
    )


# Mastery mode page (spaced repetition-style practice).
@login_required
def master():
    from services import get_due_review_cards, get_mastery_snapshot, get_mastery_strategy_catalog, get_user_decks_page, normalize_mastery_strategy

    user = _current_user()
    user_id = user.user_id if user else None
    deck_page = get_user_decks_page(user_id, _requested_page(), _requested_page_size())
    decks = deck_page['items']
    deck_data = [_deck_summary_payload(deck, user_id) for deck in decks]

    selected_deck_id = _int_value(request.args.get('deck_id'))
    selected_deck_record = _directly_accessible_deck(selected_deck_id, user_id)
    if not selected_deck_record:
        selected_deck_id = None
    deck_data = _include_selected_deck(deck_data, selected_deck_record, user_id)

    selected_deck_meta = next((deck for deck in deck_data if deck['deck_id'] == selected_deck_id), None)
    if not selected_deck_meta and selected_deck_record:
        selected_deck_meta = _deck_summary_payload(selected_deck_record, user_id)
    requested_strategy = request.args.get('strategy')
    selected_strategy = normalize_mastery_strategy(
        requested_strategy or (user.mastery_strategy_preference if user else None),
        deck_sortable=bool(selected_deck_meta and selected_deck_meta['sortable'])
    )

    mastery_snapshot = get_mastery_snapshot(user_id, selected_deck_id, strategy=selected_strategy) if selected_deck_id else None
    round_restarted = False
    selected_master_card = mastery_snapshot['current_card'] if mastery_snapshot else None

    if mastery_snapshot:
        remaining_card_ids = [card['card_id'] for card in mastery_snapshot['queue']]
        all_card_ids = [card['card_id'] for card in mastery_snapshot['cards']]
        card_indexes = {card_id: index for index, card_id in enumerate(all_card_ids)}
        seen_bits, fingerprint = _master_round_state(selected_deck_id, all_card_ids)
        unseen_ids = [
            card_id for card_id in remaining_card_ids
            if not seen_bits & (1 << card_indexes[card_id])
        ]

        if remaining_card_ids and not unseen_ids:
            # One full pass finished; start the next pass using only unmastered cards.
            round_restarted = True
            seen_bits = 0
            unseen_ids = list(remaining_card_ids)

        _store_master_round_state(selected_deck_id, fingerprint, seen_bits)

        if unseen_ids:
            selected_master_card = next((card for card in mastery_snapshot['cards'] if card['card_id'] == unseen_ids[0]), None)
        else:
            selected_master_card = None
    else:
        session.pop('master_seen_cards', None)
        session.pop('master_round_state', None)

    return render_template(
        'master.html',
        user_id=user_id,
        decks=deck_data,
        selected_deck_id=selected_deck_id,
        mastery_snapshot=mastery_snapshot,
        selected_master_card=selected_master_card,
        round_restarted=round_restarted,
        selected_strategy=selected_strategy,
        mastery_strategy_catalog=get_mastery_strategy_catalog(),
        due_reviews=get_due_review_cards(user_id, limit=5),
        deck_page=deck_page,
        **_pagination_context('master'),
    )


@login_required
def master_rate_route():
    from services import normalize_mastery_strategy, record_mastery_rating
    from models import Card

    data = _request_data()
    user = _current_user()
    user_id = user.user_id if user else None
    deck_id = _int_value(data.get('deck_id'))
    card_id = _int_value(data.get('card_id'))
    rating = (data.get('rating') or '').strip()

    if not deck_id or not card_id:
        return _redirect_with_fragment('master', fragment='mastery-practice', notice='Deck and card are required.', level='error')

    deck_record = _directly_accessible_deck(deck_id, user_id)
    if not deck_record:
        return _redirect_with_fragment('master', fragment='mastery-practice', notice='Deck not found.', level='error')

    strategy = normalize_mastery_strategy(data.get('strategy') or (user.mastery_strategy_preference if user else None), deck_sortable=deck_record.sortable)
    user.mastery_strategy_preference = strategy

    result = record_mastery_rating(user_id=user_id, deck_id=deck_id, card_id=card_id, rating=rating)
    if not result.get('success'):
        return _redirect_with_fragment('master', deck_id=deck_id, strategy=strategy, fragment='mastery-practice', notice=result.get('error', 'Could not save rating.'), level='error')

    all_card_ids = [
        stored_card_id for (stored_card_id,) in
        db.session.query(Card.card_id)
        .filter(Card.deck_id == deck_id)
        .order_by(Card.position, Card.card_id)
        .all()
    ]
    seen_bits, fingerprint = _master_round_state(deck_id, all_card_ids)
    try:
        card_index = all_card_ids.index(card_id)
    except ValueError:
        card_index = None
    if card_index is not None:
        seen_bits |= 1 << card_index
    _store_master_round_state(deck_id, fingerprint, seen_bits)

    return _redirect_with_fragment('master', deck_id=deck_id, strategy=strategy, fragment='mastery-practice')


@login_required
def master_reset_route():
    from services import normalize_mastery_strategy, reset_mastery_progress

    data = _request_data()
    user = _current_user()
    user_id = user.user_id if user else None
    deck_id = _int_value(data.get('deck_id'))

    if not deck_id:
        return _redirect_with_fragment('master', notice='Deck is required.', level='error')

    deck_record = _directly_accessible_deck(deck_id, user_id)
    if not deck_record:
        return _redirect_with_fragment('master', notice='Deck not found.', level='error')

    strategy = normalize_mastery_strategy(data.get('strategy') or (user.mastery_strategy_preference if user else None), deck_sortable=deck_record.sortable)
    user.mastery_strategy_preference = strategy

    reset_mastery_progress(user_id=user_id, deck_id=deck_id)
    session.pop('master_seen_cards', None)
    session.pop('master_round_state', None)
    return _redirect_with_fragment('master', deck_id=deck_id, strategy=strategy, fragment='mastery-practice', notice='Mastery progress reset for this deck.', level='success')


@login_required
def review_queue_route():
    from services import get_due_review_cards

    if request.args.get('restart') == '1':
        session.pop('review_seen_card_ids', None)

    due_reviews = get_due_review_cards(_current_user_id(), limit=50)
    due_ids = {progress.card_id for progress in due_reviews}
    seen_ids = [
        card_id for card_id in session.get('review_seen_card_ids', [])
        if isinstance(card_id, int) and card_id in due_ids
    ]
    session['review_seen_card_ids'] = seen_ids
    seen_id_set = set(seen_ids)
    pending_reviews = [
        progress for progress in due_reviews
        if progress.card_id not in seen_id_set
    ]

    return render_template(
        'review_queue.html',
        current_review=pending_reviews[0] if pending_reviews else None,
        due_count=len(due_reviews),
        remaining_count=len(pending_reviews),
        completed_count=len(due_reviews) - len(pending_reviews),
    )


@login_required
def review_queue_rate_route():
    from models import CardMasteryProgress
    from services import record_mastery_rating

    data = _request_data()
    card_id = _int_value(data.get('card_id'))
    deck_id = _int_value(data.get('deck_id'))
    rating = (data.get('rating') or '').strip()
    progress = CardMasteryProgress.query.filter_by(
        user_id=_current_user_id(),
        card_id=card_id,
    ).first()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if (
        not progress
        or progress.card.deck_id != deck_id
        or not progress.next_review_at
        or progress.next_review_at > now
    ):
        return jsonify({'error': 'Due review not found'}), 404

    result = record_mastery_rating(_current_user_id(), deck_id, card_id, rating)
    if not result.get('success'):
        return redirect(url_for(
            'review_queue',
            notice=result.get('error', 'Could not save rating.'),
            level='error',
        ))

    seen_ids = [
        stored_id for stored_id in session.get('review_seen_card_ids', [])
        if isinstance(stored_id, int)
    ]
    if card_id not in seen_ids:
        seen_ids.append(card_id)
    session['review_seen_card_ids'] = seen_ids[-50:]
    return redirect(url_for('review_queue'))


# Deck routes.

# Handle deck creation.
def create_deck_route():
    if not _current_user():
        return _login_required_response()
    from services import create_deck

    data = _request_data()
    user_id = _current_user_id()
    description = data.get('description')
    detailed_description = data.get('detailed_description')
    tags = data.get('tags')
    sortable = _as_bool(data.get('sortable', False))
    is_public = _as_bool(data.get('is_public', False))

    if not description:
        return jsonify({'error': 'User ID and description are required'}), 400
    try:
        deck = create_deck(user_id, description, sortable, is_public, False, detailed_description, tags)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    if _wants_json():
        return jsonify({'success': True, 'deck_id': deck.deck_id, 'description': deck.description})
    return _redirect_with_fragment(
        'edit',
        deck_id=deck.deck_id,
        fragment='deck-editor',
        notice='Deck created',
        level='success',
    )


# Return the current user's decks.
def get_deck_list_route():
    if not _current_user():
        return _login_required_response()
    from services import get_user_decks_page

    user_id = _current_user_id()

    if not user_id:
        return jsonify({'error': 'User ID is required'}), 400
    
    deck_page = get_user_decks_page(user_id, _int_value(request.values.get('page')) or 1, _int_value(request.values.get('page_size')) or 20)
    decks_data = [{
            'deck_id': d.deck_id,
            'description': d.description,
            'sortable': d.sortable,
            'card_count': _deck_card_count(d),
        } for d in deck_page['items']]
    return jsonify({'success': True, 'decks': decks_data, 'pagination': {key: deck_page[key] for key in ('page', 'per_page', 'has_prev', 'has_next', 'prev_page', 'next_page')}})


# Delete a deck.
def delete_deck_route():
    if not _current_user():
        return _login_required_response()
    from services import delete_deck

    data = _request_data()
    deck_id = _int_value(data.get('deck_id'))
    user_id = _current_user_id()

    if not deck_id:
        return jsonify({'error': 'Deck ID is required'}), 400
    if not _is_deck_owner(deck_id, user_id):
        return jsonify({'error': 'You can only delete decks you own'}), 403
    
    deleted = delete_deck(deck_id)
    if deleted:
        if _wants_json():
            return jsonify({'success': True, 'deck_id': deck_id})
        return _redirect_with_fragment('edit', fragment='decks-section', notice='Deck deleted', level='success')
    else:
        return jsonify({'error': 'Deck not found'}), 404


# Update deck settings.
def edit_deck_route():
    if not _current_user():
        return _login_required_response()
    from services import edit_deck

    data = _request_data()
    deck_id = _int_value(data.get('deck_id'))
    user_id = _current_user_id()
    description = data.get('description')
    detailed_description = data.get('detailed_description')
    tags = data.get('tags')
    sortable = _as_bool(data.get('sortable', False))
    visibility_supplied = 'is_public' in data
    is_public = _as_bool(data.get('is_public', False))

    if not deck_id or not description:
        return jsonify({'error': 'Deck ID and description are required'}), 400
    owned_deck = _owned_deck(deck_id, user_id)
    if not owned_deck:
        return jsonify({'error': 'You can only edit decks you own'}), 403
    if owned_deck.owned_by != user_id:
        if visibility_supplied and is_public != bool(owned_deck.is_public):
            return jsonify({'error': 'Only the deck owner can change visibility'}), 403
        is_public = bool(owned_deck.is_public)
    if owned_deck.is_suspended and is_public:
        return jsonify({'error': 'Suspended decks cannot be published'}), 403
    try:
        existing_featured = bool(owned_deck.is_featured)
        deck = edit_deck(deck_id, description, sortable, is_public, existing_featured, detailed_description, tags)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    if deck:
        if _wants_json():
            return jsonify({'success': True, 'deck_id': deck.deck_id})
        return _redirect_with_fragment(
            'edit',
            deck_id=deck.deck_id,
            fragment='deck-editor',
            notice='Deck saved',
            level='success',
        )
    else:
        return jsonify({'error': 'Deck not found'}), 404


# Card routes.

# Add a card to a deck.
def add_card_route():
    if not _current_user():
        return _login_required_response()
    from services import add_card

    data = _request_data()
    deck_id = _int_value(data.get('deck_id'))
    user_id = _current_user_id()
    question = data.get('question')
    answers = data.get('answers')
    
    if not deck_id or not question or not answers:
        return jsonify({'error': 'Deck ID, question, and answers are required'}), 400
    if not _owned_deck(deck_id, user_id):
        return jsonify({'error': 'You can only edit decks you own'}), 403
    try:
        card = add_card(deck_id, question, answers)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    if _wants_json():
        return jsonify({'success': True, 'card_id': card.card_id})
    return _redirect_with_fragment('edit', deck_id=deck_id, fragment='deck-editor', notice='Card added', level='success')


# Delete a card.
def delete_card_route():
    if not _current_user():
        return _login_required_response()
    from services import delete_card
    from models import Card

    data = _request_data()
    card_id = _int_value(data.get('card_id'))
    deck_id = _int_value(data.get('deck_id'))
    user_id = _current_user_id()

    if not card_id:
        return jsonify({'error': 'Card ID is required'}), 400
    card = db.session.get(Card, card_id)
    if not card or not _owned_deck(card.deck_id, user_id):
        return jsonify({'error': 'You can only edit decks you own'}), 403
    
    deleted = delete_card(card_id)
    if deleted:
        if _wants_json():
            return jsonify({'success': True, 'card_id': card_id})
        return _redirect_with_fragment('edit', deck_id=deck_id, fragment='deck-editor', notice='Card deleted', level='success') if deck_id else _redirect_with_fragment('edit', fragment='decks-section', notice='Card deleted', level='success')
    else:
        return jsonify({'error': 'Card not found'}), 404


# Update a card and its answers.
def edit_card_route():
    if not _current_user():
        return _login_required_response()
    from services import edit_card
    from models import Card

    data = _request_data()
    card_id = _int_value(data.get('card_id'))
    deck_id = _int_value(data.get('deck_id'))
    user_id = _current_user_id()
    question = data.get('question')
    answers = data.get('answers')
    
    if not card_id or not question:
        return jsonify({'error': 'Card ID and question are required'}), 400
    card_record = db.session.get(Card, card_id)
    if not card_record or not _owned_deck(card_record.deck_id, user_id):
        return jsonify({'error': 'You can only edit decks you own'}), 403
    
    try:
        card = edit_card(card_id, question, answers)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    if card:
        if isinstance(card, dict) and card.get('deleted'):
            if _wants_json():
                return jsonify({'success': True, 'card_id': card_id, 'deleted': True})
            return _redirect_with_fragment('edit', deck_id=card.get('deck_id') or deck_id, fragment='deck-editor', notice='Card updated', level='success')
        if _wants_json():
            return jsonify({'success': True, 'card_id': card.card_id})
        return _redirect_with_fragment('edit', deck_id=deck_id, fragment='deck-editor', notice='Card updated', level='success')
    else:
        return jsonify({'error': 'Card not found'}), 404


def bulk_cards_route():
    if not _current_user():
        return _login_required_response()
    from services import bulk_edit_cards

    data = _request_data()
    deck_id = _int_value(data.get('deck_id'))
    target_deck_id = _int_value(data.get('target_deck_id'))
    action = (data.get('action') or '').strip().lower()
    raw_card_ids = data.get('card_ids') if request.is_json else request.form.getlist('card_ids')
    if not isinstance(raw_card_ids, (list, tuple)):
        raw_card_ids = []
    card_ids = [_int_value(card_id) for card_id in raw_card_ids]

    if not deck_id or any(card_id is None for card_id in card_ids):
        return jsonify({'error': 'Deck and valid card IDs are required'}), 400
    if not _owned_deck(deck_id, _current_user_id()):
        return jsonify({'error': 'You can only edit decks you own'}), 403
    if action == 'move' and not _owned_deck(target_deck_id, _current_user_id()):
        return jsonify({'error': 'You can only move cards to a deck you can edit'}), 403

    try:
        result = bulk_edit_cards(deck_id, card_ids, action, target_deck_id)
    except ValueError as exc:
        if _wants_json():
            return jsonify({'error': str(exc)}), 400
        return _redirect_with_fragment(
            'edit', deck_id=deck_id, fragment='deck-editor',
            notice=str(exc), level='error',
        )
    if _wants_json():
        return jsonify(result)
    action_label = {'delete': 'deleted', 'duplicate': 'duplicated', 'move': 'moved'}[action]
    return _redirect_with_fragment(
        'edit', deck_id=deck_id, fragment='deck-editor',
        notice=f"{result['count']} card{'s' if result['count'] != 1 else ''} {action_label}.",
        level='success',
    )


def duplicate_deck_route():
    if not _current_user():
        return _login_required_response()
    from services import duplicate_deck_for_user

    deck_id = _int_value(_request_data().get('deck_id'))
    if not _owned_deck(deck_id, _current_user_id()):
        return jsonify({'error': 'You can only duplicate decks you can edit'}), 403
    try:
        deck = duplicate_deck_for_user(deck_id, _current_user_id())
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    if _wants_json():
        return jsonify({'success': True, 'deck_id': deck.deck_id})
    return _redirect_with_fragment(
        'edit', deck_id=deck.deck_id, fragment='deck-editor',
        notice='Private deck copy created.', level='success',
    )


# List cards in a deck.
def list_cards_route():
    from services import list_cards_from_deck, get_deck_details

    data = _request_data()
    deck_id = _int_value(data.get('deck_id'))
    shuffle = str(data.get('shuffle', False)).lower() in ('1', 'true', 'yes', 'on')
    detailed = str(data.get('detailed', False)).lower() in ('1', 'true', 'yes', 'on')

    if not deck_id:
        return jsonify({'error': 'Deck ID is required'}), 400
    user_id = _current_user_id()
    if not _directly_accessible_deck(deck_id, user_id):
        return jsonify({'error': 'Deck not found'}), 404
    if detailed:
        deck = get_deck_details(deck_id, shuffle_cards=shuffle, shuffle_answers=shuffle)
        cards = deck['cards'] if deck else None
    else:
        cards = list_cards_from_deck(deck_id, detailed=False, shuffle=shuffle)
    return jsonify({'success': True, 'cards': cards or []})


def import_deck_route():
    if not _current_user():
        return _login_required_response()
    from services import import_deck

    data = _request_data()
    user_id = _current_user_id()
    description = (data.get('description') or '').strip()
    detailed_description = data.get('detailed_description')
    tags = data.get('tags')
    sortable = _as_bool(data.get('sortable', False))
    is_public = _as_bool(data.get('is_public', False))
    import_text = data.get('import_text')
    upload = request.files.get('import_file')
    if upload and upload.filename:
        from services import parse_import_file
        try:
            parsed, import_text = parse_import_file(upload.read(), data.get('question_column', 0), data.get('answer_column', 1))
        except ValueError as exc:
            return _redirect_with_fragment('edit', fragment='deck-import', notice=str(exc), level='error')

    if not description:
        return _redirect_with_fragment('edit', fragment='deck-import', notice='Deck name is required.', level='error')

    try:
        result = import_deck(
            user_id=user_id,
            description=description,
            raw_text=import_text,
            sortable=sortable,
            is_public=is_public,
            is_featured=False,
            detailed_description=detailed_description,
            tags=tags,
        )
    except ValueError as exc:
        return _redirect_with_fragment('edit', fragment='deck-import', notice=str(exc), level='error')

    message = f"Deck imported with {result['card_count']} card{'s' if result['card_count'] != 1 else ''}."
    if result['invalid_lines'] > 0:
        message = f"{message} Skipped {result['invalid_lines']} invalid line{'s' if result['invalid_lines'] != 1 else ''}."
    return _redirect_with_fragment(
        'edit',
        deck_id=result['deck'].deck_id,
        fragment='deck-editor',
        notice=message,
        level='success',
    )


# Return one card with answers.
def get_card_route():
    from services import get_card_from_deck
    from models import Card

    data = _request_data()
    card_id = _int_value(data.get('card_id'))

    if not card_id:
        return jsonify({'error': 'Card ID is required'}), 400
    card_record = db.session.get(Card, card_id)
    user_id = _current_user_id()
    if not card_record or not _directly_accessible_deck(card_record.deck_id, user_id):
        return jsonify({'error': 'Card not found'}), 404
    
    card = get_card_from_deck(card_id)
    if card:
        return jsonify({'success': True, 'card': card})
    else:
        return jsonify({'error': 'Card not found'}), 404


# Match only checks the pair, it does not mutate the answer row.
# Validate one matching-game answer.
def match_answer_route():
    from services import record_match_attempt
    from models import CardAnswer

    data = _request_data()
    answer_id = _int_value(data.get('answer_id'))
    selected_question_id = _int_value(data.get('selected_question_id'))
    user_id = _current_user_id()

    if not answer_id:
        return jsonify({'error': 'Answer ID is required'}), 400

    answer = _accessible_answer(answer_id, user_id)
    if not answer:
        return jsonify({'error': 'Answer not found'}), 404

    if not selected_question_id:
        return jsonify({'error': 'Select a question tile first'}), 400

    if answer.card_id != selected_question_id:
        record_match_attempt(user_id, answer.answer_id, is_correct=False)
        return jsonify({'error': 'That answer does not match the selected question'}), 400

    # Last answer means the question tile should disappear too.
    remaining_answers = CardAnswer.query.filter_by(card_id=selected_question_id).count() - 1
    card_deleted = remaining_answers == 0
    record_match_attempt(user_id, answer.answer_id, is_correct=True)

    return jsonify({
        'success': True,
        'answer_deleted': True,
        'card_deleted': card_deleted,
        'card_id': selected_question_id,
        'remaining_answers': remaining_answers
    })


def match_attempt_route():
    from services import normalize_match_strategy, record_match_attempt

    data = _request_data()
    answer_id = _int_value(data.get('answer_id'))
    selected_question_id = _int_value(data.get('selected_question_id'))
    timed_out = str(data.get('timed_out', '')).lower() in ('1', 'true', 'yes', 'on')
    user = _current_user()
    user_id = user.user_id if user else None

    if not answer_id:
        return jsonify({'error': 'Answer ID is required'}), 400
    answer = _accessible_answer(answer_id, user_id)
    if not answer:
        return jsonify({'error': 'Answer not found'}), 404
    if not selected_question_id:
        return jsonify({'error': 'Selected question ID is required'}), 400
    is_correct = not timed_out and answer.card_id == selected_question_id

    if user and data.get('strategy'):
        user.match_strategy_preference = normalize_match_strategy(
            data.get('strategy'),
            include_account_only=True,
        )
    record_match_attempt(user_id, answer_id, is_correct=is_correct)
    return jsonify({'success': True})


# Delete one answer in edit or match mode.
def delete_answer_route():
    if not _current_user():
        return _login_required_response()
    from services import delete_answer

    data = _request_data()
    answer_id = _int_value(data.get('answer_id'))
    deck_id = _int_value(data.get('deck_id'))
    selected_question_id = _int_value(data.get('selected_question_id'))
    context = data.get('context')
    user_id = _current_user_id()

    if not answer_id:
        return jsonify({'error': 'Answer ID is required'}), 400

    answer = _accessible_answer(answer_id, user_id)
    if not answer:
        return jsonify({'error': 'Answer not found'}), 404
    if not _owned_deck(answer.card.deck_id, user_id):
        return jsonify({'error': 'You can only edit decks you own'}), 403

    if context == 'edit':
        deleted = delete_answer(answer_id)
        if deleted:
            if _wants_json():
                return jsonify({'success': True, **deleted})
            return _redirect_with_fragment('edit', deck_id=deleted.get('deck_id') or deck_id, fragment='deck-editor', notice='Answer removed', level='success')
        return jsonify({'error': 'Answer not found'}), 404

    if not selected_question_id:
        if _wants_json():
            return jsonify({'error': 'Select a question tile first'}), 400
        return redirect(url_for('match', deck_id=deck_id or answer.card.deck_id, error='Select a question tile first'))

    if answer.card_id != selected_question_id:
        if _wants_json():
            return jsonify({'error': 'That answer does not match the selected question'}), 400
        return redirect(url_for('match', deck_id=deck_id or answer.card.deck_id, selected_question=selected_question_id, error='That answer does not match the selected question'))

    deleted = delete_answer(answer_id)
    if deleted:
        next_selected = None if deleted.get('card_deleted') else selected_question_id
        if _wants_json():
            return jsonify({'success': True, **deleted})
        return redirect(url_for('match', deck_id=deleted.get('deck_id') or deck_id, selected_question=next_selected or ''))
    return jsonify({'error': 'Answer not found'}), 404


# Move a card one slot.
def move_card_route():
    if not _current_user():
        return _login_required_response()
    from services import move_card_in_deck
    from models import Card

    data = _request_data()
    card_id = _int_value(data.get('card_id'))
    deck_id = _int_value(data.get('deck_id'))
    direction = str(data.get('direction', '')).lower()
    user_id = _current_user_id()

    if not card_id or direction not in ('up', 'down'):
        return jsonify({'error': 'Card ID and valid direction are required'}), 400
    card = db.session.get(Card, card_id)
    if not card or not _owned_deck(card.deck_id, user_id):
        return jsonify({'error': 'You can only edit decks you own'}), 403

    result = move_card_in_deck(card_id, direction)
    if not result.get('success'):
        return jsonify({'error': result.get('error', 'Unable to move card')}), 400

    if _wants_json():
        return jsonify({'success': True, **result})
    return _redirect_with_fragment('edit', deck_id=result.get('deck_id') or deck_id, fragment='deck-editor')


# Swap two cards in a sortable deck.
def swap_cards_route():
    if not _current_user():
        return _login_required_response()
    from services import swap_cards_in_deck
    from models import Card

    payload = _request_data()
    card_id = _int_value(payload.get('card_id'))
    target_card_id = _int_value(payload.get('target_card_id'))
    user_id = _current_user_id()

    if not card_id or not target_card_id:
        return jsonify({'error': 'Both card IDs are required'}), 400
    first_card = db.session.get(Card, card_id)
    second_card = db.session.get(Card, target_card_id)
    if not first_card or not second_card or not _owned_deck(first_card.deck_id, user_id):
        return jsonify({'error': 'You can only edit decks you own'}), 403

    result = swap_cards_in_deck(card_id, target_card_id)
    if not result.get('success'):
        return jsonify({'error': result.get('error', 'Unable to swap cards')}), 400

    return jsonify({'success': True, **result})


# Check a submitted reorder attempt.
def check_reorder_route():
    from services import check_deck_order

    payload = _request_data()
    deck_id = _int_value(payload.get('deck_id'))
    ordered_card_ids = payload.get('ordered_card_ids')

    if not deck_id:
        return jsonify({'error': 'Deck ID is required'}), 400
    user_id = _current_user_id()
    if not _directly_accessible_deck(deck_id, user_id):
        return jsonify({'error': 'Deck not found'}), 404

    if not isinstance(ordered_card_ids, list):
        return jsonify({'error': 'ordered_card_ids must be a list'}), 400

    try:
        # Normalize IDs before comparing order.
        normalized_card_ids = [_int_value(card_id) for card_id in ordered_card_ids]
    except (TypeError, ValueError):
        return jsonify({'error': 'ordered_card_ids must contain valid card IDs'}), 400

    if any(card_id is None for card_id in normalized_card_ids):
        return jsonify({'error': 'ordered_card_ids must contain valid card IDs'}), 400

    result = check_deck_order(deck_id, normalized_card_ids)
    if not result.get('valid'):
        return jsonify({'error': result.get('error', 'Unable to validate order')}), 400

    return jsonify({
        'success': True,
        'is_correct': result['is_correct'],
        'incorrect_card_ids': result['incorrect_card_ids'],
        'expected_order': result['expected_order'],
        'received_order': result['received_order'],
    })


# Search results.
# Render public search results.
def search_route():
    from services import search_public_content

    query = request.args.get('q', '')
    user_id = _current_user_id()
    results = search_public_content(query, page=_requested_page(), limit=_requested_page_size(), user_id=user_id) if query else {
        'decks': [],
        'quizzes': [],
        'has_exact_match': False,
        'query_tokens': [],
        'expanded_tokens': [],
        'pagination': {'page': _requested_page(), 'per_page': _requested_page_size(), 'has_prev': False, 'has_next': False, 'prev_page': None, 'next_page': None},
    }

    return render_template(
        'search.html',
        query=query,
        decks=results['decks'],
        quizzes=results['quizzes'],
        has_exact_match=results['has_exact_match'],
        query_tokens=results['query_tokens'],
        expanded_tokens=results['expanded_tokens'],
        pagination=results['pagination'],
        **_pagination_context('search'),
    )


# Public quiz detail (read-only).
def public_quiz_route():
    from services import get_quiz_with_content

    user_id = _current_user_id()
    quiz_id = _int_value(request.args.get('quiz_id'))
    if not quiz_id:
        return redirect(url_for('search'))

    accessible_quiz = _directly_accessible_quiz(quiz_id, user_id)
    quiz = get_quiz_with_content(quiz_id) if accessible_quiz else None
    if not quiz:
        return redirect(url_for('search'))
    if quiz.is_public:
        return redirect(url_for('public_quiz_detail', quiz_slug=quiz_url_slug(quiz)), code=301)
    return _render_public_quiz(quiz, can_copy=False)


def public_quiz_detail_route(quiz_slug):
    from services import get_quiz_with_content

    quiz_id = id_from_url_slug(quiz_slug)
    quiz = get_quiz_with_content(quiz_id) if quiz_id else None
    if not quiz or not quiz.is_public:
        return redirect(url_for('search'))
    canonical_slug = quiz_url_slug(quiz)
    if quiz_slug != canonical_slug:
        return redirect(url_for('public_quiz_detail', quiz_slug=canonical_slug), code=301)
    return _render_public_quiz(quiz, can_copy=True)


def _render_public_quiz(quiz, *, can_copy, **extra):
    from models import QuizFavorite, QuizRating

    user_id = _current_user_id()
    rating_count, average_rating = db.session.query(
        db.func.count(QuizRating.user_id), db.func.avg(QuizRating.rating),
    ).filter_by(quiz_id=quiz.quiz_id).one()
    rating = db.session.get(QuizRating, (user_id, quiz.quiz_id)) if user_id else None
    return render_template(
        'public_quiz.html', quiz=quiz, user_id=user_id, can_copy=can_copy,
        can_edit=bool(_owned_quiz(quiz.quiz_id, user_id)),
        is_favorite=bool(
            user_id and db.session.get(QuizFavorite, (user_id, quiz.quiz_id))
        ),
        my_rating=rating.rating if rating else None,
        rating_count=int(rating_count or 0),
        average_rating=round(float(average_rating or 0), 1), **extra,
    )


# Copy a public quiz to the current user's account.
def copy_public_quiz_route():
    if not _current_user():
        return _login_required_response()
    from services import copy_public_quiz_to_user

    data = _request_data()
    source_quiz_id = _int_value(data.get('quiz_id'))
    if not source_quiz_id:
        return redirect(url_for('search'))

    try:
        copied_quiz = copy_public_quiz_to_user(
            source_quiz_id, user_id=_current_user_id(),
            share_token=(data.get('share_token') or '').strip() or None,
        )
    except ValueError:
        return redirect(url_for('search'))
    if not copied_quiz:
        return redirect(url_for('search'))

    return _redirect_with_fragment(
        'edit_quiz_route',
        quiz_id=copied_quiz.quiz_id,
        fragment='quiz-editor',
        notice='Quiz copied to your account',
        level='success',
    )


def shared_quiz_route(token):
    from models import QuizShareLink
    from services import get_quiz_with_content

    share_link = db.session.get(QuizShareLink, token)
    quiz = get_quiz_with_content(share_link.quiz_id) if share_link else None
    if not quiz or quiz.is_suspended:
        return redirect(url_for('search'))
    return _render_public_quiz(
        quiz, can_copy=share_link.permission == 'copy',
        share_token=share_link.token, is_unlisted=True,
    )


def toggle_quiz_favorite_route():
    if not _current_user():
        return _login_required_response()
    from models import Quiz, QuizFavorite

    quiz_id = _int_value(_request_data().get('quiz_id'))
    quiz = db.session.get(Quiz, quiz_id)
    if not quiz or not quiz.is_public:
        return jsonify({'error': 'Public quiz not found.'}), 404
    favorite = db.session.get(QuizFavorite, (_current_user_id(), quiz_id))
    if favorite:
        db.session.delete(favorite)
    else:
        db.session.add(QuizFavorite(user_id=_current_user_id(), quiz_id=quiz_id))
    db.session.commit()
    return redirect(
        request.referrer
        or url_for('public_quiz_detail', quiz_slug=quiz_url_slug(quiz))
    )


def rate_quiz_route():
    if not _current_user():
        return _login_required_response()
    from models import Quiz, QuizRating

    data = _request_data()
    quiz_id = _int_value(data.get('quiz_id'))
    rating = _int_value(data.get('rating'))
    quiz = db.session.get(Quiz, quiz_id)
    if not quiz or not quiz.is_public or rating not in range(1, 6):
        return jsonify({
            'error': 'A public quiz and a rating from 1 to 5 are required.',
        }), 400
    record = db.session.get(QuizRating, (_current_user_id(), quiz_id))
    if record:
        record.rating = rating
    else:
        db.session.add(QuizRating(
            user_id=_current_user_id(), quiz_id=quiz_id, rating=rating,
        ))
    db.session.commit()
    return redirect(
        request.referrer
        or url_for('public_quiz_detail', quiz_slug=quiz_url_slug(quiz))
    )


def report_quiz_route():
    if not _current_user():
        return _login_required_response()
    from models import Quiz, QuizReport

    data = _request_data()
    quiz_id = _int_value(data.get('quiz_id'))
    reason = (data.get('reason') or '').strip()
    detail = (data.get('detail') or '').strip()[:500]
    quiz = db.session.get(Quiz, quiz_id)
    if (
        not quiz or not quiz.is_public
        or reason not in ('spam', 'copyright', 'inaccurate', 'other')
    ):
        return jsonify({'error': 'A valid public quiz report is required.'}), 400
    user_id = _current_user_id()
    if _owned_quiz(quiz_id, user_id):
        return jsonify({'error': 'You cannot report a quiz you can edit.'}), 400
    existing = QuizReport.query.filter_by(
        user_id=user_id, quiz_id=quiz_id, status='open',
    ).first()
    if existing:
        return redirect(url_for(
            'public_quiz_detail', quiz_slug=quiz_url_slug(quiz),
            notice='You already have an open report for this quiz.', level='info',
        ))
    report = QuizReport(
        user_id=user_id, quiz_id=quiz_id, reason=reason, detail=detail or None,
    )
    db.session.add(report)
    db.session.commit()
    audit_event(
        'quiz_report_submitted', _current_user(), 'info',
        target_type='quiz_report', target_id=report.report_id, quiz_id=quiz_id,
    )
    return redirect(url_for(
        'public_quiz_detail', quiz_slug=quiz_url_slug(quiz),
        notice='Report submitted for moderator review.', level='success',
    ))


# Public deck detail (read-only).
def public_deck_route():
    from services import get_deck_with_content

    user_id = _current_user_id()
    deck_id = _int_value(request.args.get('deck_id'))
    if not deck_id:
        return redirect(url_for('search'))

    accessible_deck = _directly_accessible_deck(deck_id, user_id)
    deck = get_deck_with_content(deck_id) if accessible_deck else None
    if not deck:
        return redirect(url_for('search'))
    if deck.is_public:
        return redirect(url_for('public_deck_detail', deck_slug=deck_url_slug(deck)), code=301)
    return render_template('public_deck.html', deck=deck, user_id=user_id, can_copy=False)


def public_deck_detail_route(deck_slug):
    from services import get_deck_with_content

    deck_id = id_from_url_slug(deck_slug)
    deck = get_deck_with_content(deck_id) if deck_id else None
    if not deck or not deck.is_public:
        return redirect(url_for('search'))
    canonical_slug = deck_url_slug(deck)
    if deck_slug != canonical_slug:
        return redirect(url_for('public_deck_detail', deck_slug=canonical_slug), code=301)
    return _render_public_deck(deck, can_copy=True)


def _render_public_deck(deck, *, can_copy, **extra):
    from models import DeckFavorite, DeckRating
    user_id = _current_user_id()
    rating_count, average_rating = db.session.query(
        db.func.count(DeckRating.user_id), db.func.avg(DeckRating.rating)
    ).filter_by(deck_id=deck.deck_id).one()
    my_rating = db.session.get(DeckRating, (user_id, deck.deck_id)).rating if user_id and db.session.get(DeckRating, (user_id, deck.deck_id)) else None
    return render_template(
        'public_deck.html', deck=deck, user_id=user_id, can_copy=can_copy,
        is_favorite=bool(user_id and db.session.get(DeckFavorite, (user_id, deck.deck_id))),
        my_rating=my_rating, rating_count=int(rating_count or 0), average_rating=round(float(average_rating or 0), 1), **extra,
    )


def import_deck_preview_route():
    if not _current_user(): return _login_required_response()
    from services import parse_import_file
    upload = request.files.get('import_file')
    if not upload or not upload.filename:
        return _redirect_with_fragment('edit', fragment='deck-import', notice='Choose a CSV or TSV file.', level='error')
    try:
        parsed, import_text = parse_import_file(upload.read(), request.form.get('question_column', 0), request.form.get('answer_column', 1))
    except ValueError as exc:
        return _redirect_with_fragment('edit', fragment='deck-import', notice=str(exc), level='error')
    return render_template('import_preview.html', parsed=parsed, import_text=import_text, form=request.form)


def download_deck_csv_route(deck_id):
    if not _current_user() or not _owned_deck(deck_id, _current_user_id()): return _login_required_response()
    from services import export_deck_as_text
    deck = _owned_deck(deck_id, _current_user_id())
    filename = f"deck-{deck_id}.csv"
    return Response(export_deck_as_text(deck) + '\n', mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename="{filename}"'})


def download_deck_tsv_route(deck_id):
    if not _current_user() or not _owned_deck(deck_id, _current_user_id()):
        return _login_required_response()
    from services import export_deck_as_text

    deck = _owned_deck(deck_id, _current_user_id())
    filename = f'deck-{deck_id}.tsv'
    return Response(
        export_deck_as_text(deck, delimiter='\t') + '\n',
        mimetype='text/tab-separated-values',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


def toggle_deck_favorite_route():
    if not _current_user(): return _login_required_response()
    from models import Deck, DeckFavorite
    deck_id = _int_value(_request_data().get('deck_id'))
    deck = db.session.get(Deck, deck_id)
    if not deck or not deck.is_public: return jsonify({'error': 'Public deck not found.'}), 404
    favorite = db.session.get(DeckFavorite, (_current_user_id(), deck_id))
    if favorite: db.session.delete(favorite)
    else: db.session.add(DeckFavorite(user_id=_current_user_id(), deck_id=deck_id))
    db.session.commit()
    return redirect(request.referrer or url_for('public_deck_detail', deck_slug=deck_url_slug(deck)))


def rate_deck_route():
    if not _current_user(): return _login_required_response()
    from models import Deck, DeckRating
    data = _request_data(); deck_id = _int_value(data.get('deck_id')); rating = _int_value(data.get('rating'))
    deck = db.session.get(Deck, deck_id)
    if not deck or not deck.is_public or rating not in range(1, 6): return jsonify({'error': 'A public deck and a rating from 1 to 5 are required.'}), 400
    record = db.session.get(DeckRating, (_current_user_id(), deck_id))
    if record: record.rating = rating
    else: db.session.add(DeckRating(user_id=_current_user_id(), deck_id=deck_id, rating=rating))
    db.session.commit()
    return redirect(request.referrer or url_for('public_deck_detail', deck_slug=deck_url_slug(deck)))


def report_deck_route():
    if not _current_user(): return _login_required_response()
    from models import Deck, DeckReport
    data = _request_data(); deck_id = _int_value(data.get('deck_id')); reason = (data.get('reason') or '').strip(); detail = (data.get('detail') or '').strip()[:500]
    deck = db.session.get(Deck, deck_id)
    if not deck or not deck.is_public or reason not in ('spam', 'copyright', 'inaccurate', 'other'): return jsonify({'error': 'A valid public deck report is required.'}), 400
    user_id = _current_user_id()
    if deck.owned_by == user_id:
        return jsonify({'error': 'You cannot report your own deck.'}), 400
    existing = DeckReport.query.filter_by(
        user_id=user_id, deck_id=deck_id, status='open',
    ).first()
    if existing:
        return redirect(url_for(
            'public_deck_detail', deck_slug=deck_url_slug(deck),
            notice='You already have an open report for this deck.', level='info',
        ))
    report = DeckReport(
        user_id=user_id, deck_id=deck_id, reason=reason, detail=detail or None,
    )
    db.session.add(report)
    db.session.commit()
    audit_event(
        'deck_report_submitted', _current_user(), 'info',
        target_type='deck_report', target_id=report.report_id, deck_id=deck_id,
    )
    return redirect(url_for(
        'public_deck_detail', deck_slug=deck_url_slug(deck),
        notice='Report submitted for moderator review.', level='success',
    ))


def creator_profile_route(username):
    from models import CuratedCollection, Deck, Quiz, User
    from services import get_public_deck_preview_state
    try:
        normalized_username = canonical_username(username)
    except ValueError:
        abort(404)
    creator = User.query.filter_by(canonical_username=normalized_username, is_active=True).first_or_404()
    per_page = _requested_page_size()
    deck_page = _query_page(
        Deck.query.filter_by(owned_by=creator.user_id, is_public=True).order_by(Deck.deck_id.desc()),
        max(1, _int_value(request.args.get('deck_page')) or 1),
        per_page,
    )
    quiz_page = _query_page(
        Quiz.query.filter_by(owned_by=creator.user_id, is_public=True).order_by(Quiz.quiz_id.desc()),
        max(1, _int_value(request.args.get('quiz_page')) or 1),
        per_page,
    )
    collections = CuratedCollection.query.filter_by(
        owned_by=creator.user_id, is_public=True,
    ).order_by(
        CuratedCollection.created_at.desc(), CuratedCollection.collection_id.desc(),
    ).limit(20).all()
    return render_template(
        'creator_profile.html', creator=creator,
        decks=deck_page['items'], quizzes=quiz_page['items'],
        collections=collections, deck_page=deck_page, quiz_page=quiz_page,
        deck_preview_state=get_public_deck_preview_state(
            (deck.deck_id for deck in deck_page['items']), _current_user_id(),
        ),
    )


def shared_deck_route(token):
    from models import DeckShareLink
    from services import get_deck_with_content

    share_link = db.session.get(DeckShareLink, token)
    deck = get_deck_with_content(share_link.deck_id) if share_link else None
    if not deck or deck.is_suspended:
        return redirect(url_for('search'))
    return render_template(
        'public_deck.html', deck=deck, user_id=_current_user_id(), share_token=share_link.token,
        can_copy=share_link.permission == 'copy', is_unlisted=True,
    )


# Copy a public deck to the current user's account.
def copy_public_deck_route():
    if not _current_user():
        return _login_required_response()
    from services import copy_public_deck_to_user

    data = _request_data()
    source_deck_id = _int_value(data.get('deck_id'))
    if not source_deck_id:
        return redirect(url_for('search'))

    share_token = (data.get('share_token') or '').strip()
    try:
        copied_deck = copy_public_deck_to_user(source_deck_id, user_id=_current_user_id(), share_token=share_token or None)
    except ValueError:
        return redirect(url_for('search'))
    if not copied_deck:
        return redirect(url_for('search'))

    return _redirect_with_fragment(
        'edit',
        deck_id=copied_deck.deck_id,
        fragment='deck-editor',
        notice='Deck copied to your account',
        level='success',
    )


# Resolve the quiz launcher selection without creating an attempt.
def _quiz_launcher_context(source_data):
    from services import get_accessible_custom_quizzes_page, get_user_decks_page

    user_id = _current_user_id()
    deck_page = get_user_decks_page(user_id, _requested_page(), _requested_page_size()) if user_id is not None else None
    decks = deck_page['items'] if deck_page else []
    deck_data = [_quiz_launcher_deck_payload(deck, user_id) for deck in decks]

    quiz_page = get_accessible_custom_quizzes_page(user_id, _requested_page(), _requested_page_size())
    custom_quizzes = quiz_page['items']

    selected_deck_id = None
    selected_custom_quiz_id = None
    selected_source = (source_data.get('quiz_source') or '').strip()

    if selected_source.startswith('deck:'):
        selected_deck_id = _int_value(selected_source.split(':', 1)[1])
    elif selected_source.startswith('custom:'):
        selected_custom_quiz_id = _int_value(selected_source.split(':', 1)[1])
    else:
        # Keep older deck/custom_quiz links working.
        selected_deck_id = _int_value(source_data.get('deck_id'))
        selected_custom_quiz_id = _int_value(source_data.get('custom_quiz_id'))
        if selected_deck_id and selected_custom_quiz_id:
            # Prefer a single source.
            selected_custom_quiz_id = None
        if selected_deck_id:
            selected_source = f'deck:{selected_deck_id}'
        elif selected_custom_quiz_id:
            selected_source = f'custom:{selected_custom_quiz_id}'

    selected_deck_record = _directly_accessible_deck(selected_deck_id, user_id)
    if not selected_deck_record:
        selected_deck_id = None
        if selected_source.startswith('deck:'):
            selected_source = ''
    from models import Quiz, db
    quiz_share_token = (source_data.get('quiz_share_token') or '').strip()
    selected_quiz_record = _directly_accessible_quiz(
        selected_custom_quiz_id, user_id,
    )
    if not selected_quiz_record and quiz_share_token and selected_custom_quiz_id:
        from models import QuizShareLink
        share_link = db.session.get(QuizShareLink, quiz_share_token)
        if share_link and share_link.quiz_id == selected_custom_quiz_id:
            selected_quiz_record = db.session.get(Quiz, selected_custom_quiz_id)
    if not selected_quiz_record:
        selected_custom_quiz_id = None
        if selected_source.startswith('custom:'):
            selected_source = ''

    deck_data = _include_selected_deck(deck_data, selected_deck_record, user_id)
    custom_quizzes = _include_selected_quiz(custom_quizzes, selected_quiz_record if selected_custom_quiz_id else None)
    available_question_pools = sorted({
        question.pool for question in (selected_quiz_record.questions if selected_quiz_record else [])
        if question.pool
    }, key=str.casefold)
    return {
        'user_id': user_id,
        'decks': deck_data,
        'custom_quizzes': custom_quizzes,
        'selected_deck_id': selected_deck_id,
        'selected_custom_quiz_id': selected_custom_quiz_id,
        'selected_source': selected_source,
        'selected_source_title': (
            selected_deck_record.description if selected_deck_record
            else selected_quiz_record.title if selected_custom_quiz_id else None
        ),
        'deck_page': deck_page,
        'quiz_page': quiz_page,
        'available_question_pools': available_question_pools,
        'quiz_share_token': quiz_share_token if selected_custom_quiz_id else '',
        **_pagination_context('quiz'),
    }


# Render the quiz launcher. GET requests never create server-side attempts.
def quiz_route():
    context = _quiz_launcher_context(request.args)
    return render_template(
        'quiz.html',
        **context,
        quiz_data=None,
        attempt_token=None,
        quiz_error=None,
    )


def create_deck_share_link_route():
    if not _current_user():
        return _login_required_response()
    from models import Deck, DeckShareLink

    data = _request_data()
    deck_id = _int_value(data.get('deck_id'))
    permission = (data.get('permission') or 'view').strip().lower()
    deck = db.session.get(Deck, deck_id)
    if (
        not deck or deck.owned_by != _current_user_id()
        or deck.is_suspended or permission not in ('view', 'copy')
    ):
        return jsonify({'error': 'Only the deck owner can create a share link.'}), 403
    share_link = DeckShareLink(token=secrets.token_urlsafe(32), deck_id=deck_id, permission=permission)
    db.session.add(share_link)
    db.session.commit()
    return _redirect_with_fragment('edit', deck_id=deck_id, fragment='sharing', notice='Unlisted share link created.', level='success')


def delete_deck_share_link_route():
    if not _current_user():
        return _login_required_response()
    from models import DeckShareLink

    data = _request_data()
    token = (data.get('token') or '').strip()
    share_link = db.session.get(DeckShareLink, token)
    if not share_link or not _is_deck_owner(share_link.deck_id, _current_user_id()):
        return jsonify({'error': 'Share link not found.'}), 404
    deck_id = share_link.deck_id
    db.session.delete(share_link)
    db.session.commit()
    return _redirect_with_fragment('edit', deck_id=deck_id, fragment='sharing', notice='Share link revoked.', level='success')


def add_deck_collaborator_route():
    if not _current_user():
        return _login_required_response()
    from models import DeckCollaborator, User

    data = _request_data()
    deck_id = _int_value(data.get('deck_id'))
    if not _is_deck_owner(deck_id, _current_user_id()):
        return jsonify({'error': 'Only the deck owner can manage collaborators.'}), 403
    try:
        username = canonical_username(data.get('username'))
    except ValueError:
        username = None
    collaborator = User.query.filter_by(canonical_username=username).first() if username else None
    if not collaborator or collaborator.user_id == _current_user_id() or not collaborator.is_active:
        return _redirect_with_fragment('edit', deck_id=deck_id, fragment='sharing', notice='Active user not found.', level='error')
    if not db.session.get(DeckCollaborator, (deck_id, collaborator.user_id)):
        db.session.add(DeckCollaborator(deck_id=deck_id, user_id=collaborator.user_id))
        db.session.commit()
    return _redirect_with_fragment('edit', deck_id=deck_id, fragment='sharing', notice='Co-author added.', level='success')


def remove_deck_collaborator_route():
    if not _current_user():
        return _login_required_response()
    from models import DeckCollaborator

    data = _request_data()
    deck_id = _int_value(data.get('deck_id'))
    collaborator_id = _int_value(data.get('user_id'))
    if not _is_deck_owner(deck_id, _current_user_id()):
        return jsonify({'error': 'Only the deck owner can manage collaborators.'}), 403
    collaborator = db.session.get(DeckCollaborator, (deck_id, collaborator_id))
    if collaborator:
        db.session.delete(collaborator)
        db.session.commit()
    return _redirect_with_fragment('edit', deck_id=deck_id, fragment='sharing', notice='Co-author removed.', level='success')


def create_quiz_share_link_route():
    if not _current_user():
        return _login_required_response()
    from models import Quiz, QuizShareLink

    data = _request_data()
    quiz_id = _int_value(data.get('quiz_id'))
    permission = (data.get('permission') or 'view').strip().lower()
    quiz = db.session.get(Quiz, quiz_id)
    if (
        not quiz or quiz.owned_by != _current_user_id()
        or quiz.is_suspended or permission not in ('view', 'copy')
    ):
        return jsonify({'error': 'Only the quiz owner can create a share link.'}), 403
    share_link = QuizShareLink(
        token=secrets.token_urlsafe(32), quiz_id=quiz_id, permission=permission,
    )
    db.session.add(share_link)
    db.session.commit()
    return _redirect_with_fragment(
        'edit_quiz_route', quiz_id=quiz_id, fragment='quiz-sharing',
        notice='Unlisted quiz share link created.', level='success',
    )


def delete_quiz_share_link_route():
    if not _current_user():
        return _login_required_response()
    from models import QuizShareLink

    token = (_request_data().get('token') or '').strip()
    share_link = db.session.get(QuizShareLink, token)
    if not share_link or not _is_quiz_owner(
        share_link.quiz_id, _current_user_id(),
    ):
        return jsonify({'error': 'Share link not found.'}), 404
    quiz_id = share_link.quiz_id
    db.session.delete(share_link)
    db.session.commit()
    return _redirect_with_fragment(
        'edit_quiz_route', quiz_id=quiz_id, fragment='quiz-sharing',
        notice='Quiz share link revoked.', level='success',
    )


def add_quiz_collaborator_route():
    if not _current_user():
        return _login_required_response()
    from models import QuizCollaborator, User

    data = _request_data()
    quiz_id = _int_value(data.get('quiz_id'))
    if not _is_quiz_owner(quiz_id, _current_user_id()):
        return jsonify({'error': 'Only the quiz owner can manage collaborators.'}), 403
    try:
        username = canonical_username(data.get('username'))
    except ValueError:
        username = None
    collaborator = User.query.filter_by(
        canonical_username=username,
    ).first() if username else None
    if (
        not collaborator or collaborator.user_id == _current_user_id()
        or not collaborator.is_active
    ):
        return _redirect_with_fragment(
            'edit_quiz_route', quiz_id=quiz_id, fragment='quiz-sharing',
            notice='Active user not found.', level='error',
        )
    if not db.session.get(QuizCollaborator, (quiz_id, collaborator.user_id)):
        db.session.add(QuizCollaborator(
            quiz_id=quiz_id, user_id=collaborator.user_id,
        ))
        db.session.commit()
    return _redirect_with_fragment(
        'edit_quiz_route', quiz_id=quiz_id, fragment='quiz-sharing',
        notice='Quiz co-author added.', level='success',
    )


def remove_quiz_collaborator_route():
    if not _current_user():
        return _login_required_response()
    from models import QuizCollaborator

    data = _request_data()
    quiz_id = _int_value(data.get('quiz_id'))
    collaborator_id = _int_value(data.get('user_id'))
    if not _is_quiz_owner(quiz_id, _current_user_id()):
        return jsonify({'error': 'Only the quiz owner can manage collaborators.'}), 403
    collaborator = db.session.get(
        QuizCollaborator, (quiz_id, collaborator_id),
    )
    if collaborator:
        db.session.delete(collaborator)
        db.session.commit()
    return _redirect_with_fragment(
        'edit_quiz_route', quiz_id=quiz_id, fragment='quiz-sharing',
        notice='Quiz co-author removed.', level='success',
    )


def start_quiz_route():
    from services import create_quiz_attempt, generate_quiz_data

    context = _quiz_launcher_context(_request_data())
    if not context['selected_source']:
        return render_template(
            'quiz.html',
            **context,
            quiz_data=None,
            attempt_token=None,
            quiz_error='That quiz source is unavailable.',
        ), 404

    data = _request_data()
    pool = ' '.join(str(data.get('question_pool') or '').split())
    if len(pool) > 80:
        pool = ''
    retry_question_ids = [
        item.strip() for item in str(data.get('retry_question_ids') or '').split(',')
        if item.strip()
    ]
    if len(retry_question_ids) > current_app.config['MAX_QUIZ_QUESTIONS']:
        retry_question_ids = []
    question_count = _int_value(data.get('question_count'))
    time_limit_seconds = _int_value(data.get('time_limit_seconds'))
    if str(data.get('time_limit_seconds') or '').strip() in ('', '0'):
        time_limit_seconds = None
    if time_limit_seconds not in (None, 300, 600, 1200, 1800):
        return render_template(
            'quiz.html', **context, quiz_data=None, attempt_token=None,
            quiz_error='Choose one of the available time limits.',
        ), 400

    if context['selected_deck_id']:
        quiz_questions = generate_quiz_data(
            deck_id=context['selected_deck_id'], question_ids=retry_question_ids or None,
        )
    else:
        quiz_questions = generate_quiz_data(
            custom_quiz_id=context['selected_custom_quiz_id'], pool=pool or None,
            question_ids=retry_question_ids or None,
        )
    if not quiz_questions:
        return render_template(
            'quiz.html',
            **context,
            quiz_data=None,
            attempt_token=None,
            quiz_error='This source does not contain any quiz questions yet.',
        ), 400

    quiz_session_id = session.get('quiz_session_id')
    if not quiz_session_id:
        quiz_session_id = secrets.token_urlsafe(24)
        session['quiz_session_id'] = quiz_session_id

    try:
        attempt_token, display_questions, active_tokens = create_quiz_attempt(
            context['user_id'], quiz_session_id, quiz_questions,
            question_limit=question_count, time_limit_seconds=time_limit_seconds,
            source_type='deck' if context['selected_deck_id'] else 'custom',
            source_id=context['selected_deck_id'] or context['selected_custom_quiz_id'],
            source_title=context['selected_source_title'],
        )
    except ValueError as exc:
        return render_template(
            'quiz.html', **context, quiz_data=None, attempt_token=None,
            quiz_error=str(exc),
        ), 400
    session['quiz_attempt_tokens'] = active_tokens
    response = current_app.make_response(render_template(
        'quiz.html',
        **context,
        quiz_data=display_questions,
        attempt_token=attempt_token,
        time_limit_seconds=time_limit_seconds,
        quiz_error=None,
    ))
    response.headers['Cache-Control'] = 'no-store'
    return response


# Score a submitted quiz.
def score_quiz_route():
    from services import score_quiz_attempt
    data = _request_data()
    submitted_answers = data.get('answers', {})
    attempt_token = str(data.get('attempt_token', '')).strip()
    active_attempt_tokens = list(session.get('quiz_attempt_tokens', []))
    answer_payload_valid = (
        isinstance(submitted_answers, dict)
        and all(
            isinstance(answer_values, list)
            and all(isinstance(answer, str) for answer in answer_values)
            for answer_values in submitted_answers.values()
        )
    )
    if not attempt_token or attempt_token not in active_attempt_tokens or not answer_payload_valid:
        return jsonify({'error': 'Quiz attempt is missing or expired.'}), 400

    result = score_quiz_attempt(
        attempt_token,
        _current_user_id(),
        session.get('quiz_session_id'),
        submitted_answers,
    )
    if not result:
        if attempt_token in active_attempt_tokens:
            active_attempt_tokens.remove(attempt_token)
            session['quiz_attempt_tokens'] = active_attempt_tokens
        return jsonify({'error': 'Quiz attempt is missing or expired.'}), 400

    active_attempt_tokens.remove(attempt_token)
    session['quiz_attempt_tokens'] = active_attempt_tokens
    return jsonify(result)


@login_required
def quiz_history_route():
    from models import QuizResult

    user_id = _current_user_id()
    page = _requested_page()
    per_page = _requested_page_size()
    query = QuizResult.query.filter_by(user_id=user_id).order_by(
        QuizResult.completed_at.desc(), QuizResult.result_id.desc(),
    )
    rows = query.limit(per_page + 1).offset((page - 1) * per_page).all()
    has_next = len(rows) > per_page
    results = rows[:per_page]
    count, average_percent, best_percent = db.session.query(
        db.func.count(QuizResult.result_id),
        db.func.avg(QuizResult.score * 100.0 / QuizResult.question_count),
        db.func.max(QuizResult.score * 100.0 / QuizResult.question_count),
    ).filter(QuizResult.user_id == user_id).one()
    pagination = {
        'page': page, 'per_page': per_page, 'has_prev': page > 1, 'has_next': has_next,
        'prev_page': page - 1 if page > 1 else None,
        'next_page': page + 1 if has_next else None,
    }
    return render_template(
        'quiz_history.html', results=results,
        stats={
            'count': int(count or 0),
            'average_percent': round(float(average_percent or 0)),
            'best_percent': round(float(best_percent or 0)),
        },
        pagination=pagination, **_pagination_context('quiz_history'),
    )


@login_required
def quiz_result_detail_route(result_id):
    from models import QuizResult

    result = QuizResult.query.filter_by(
        result_id=result_id, user_id=_current_user_id(),
    ).first_or_404()
    try:
        question_results = json.loads(result.question_results_json)
    except (TypeError, ValueError):
        question_results = []
    return render_template(
        'quiz_result_detail.html', result=result, question_results=question_results,
    )

# Render the custom quiz editor.
def edit_quiz_route():
    if not _current_user():
        return _login_required_response()
    from services import get_quiz_with_content, get_user_custom_quizzes_page
    user_id = _current_user_id()
    quiz_page = get_user_custom_quizzes_page(user_id, _requested_page(), _requested_page_size())
    quizzes = quiz_page['items']
    
    selected_quiz_id = _int_value(request.args.get('quiz_id'))
    selected_quiz = None
    if selected_quiz_id:
        selected_quiz = (
            get_quiz_with_content(selected_quiz_id)
            if _owned_quiz(selected_quiz_id, user_id) else None
        )
    quizzes = _include_selected_quiz(quizzes, selected_quiz)
            
    quiz_analytics = None
    quiz_collaborators = []
    quiz_share_links = []
    selected_quiz_is_suspended = bool(
        selected_quiz and selected_quiz.is_suspended
    )
    can_change_quiz_visibility = bool(
        _is_quiz_owner(selected_quiz_id, user_id)
        and not selected_quiz_is_suspended
    )
    can_manage_quiz_sharing = can_change_quiz_visibility
    if can_manage_quiz_sharing:
        from models import QuizCollaborator, QuizShareLink
        quiz_collaborators = QuizCollaborator.query.filter_by(
            quiz_id=selected_quiz_id,
        ).order_by(QuizCollaborator.created_at.asc()).all()
        quiz_share_links = QuizShareLink.query.filter_by(
            quiz_id=selected_quiz_id,
        ).order_by(QuizShareLink.created_at.desc()).all()
    if selected_quiz:
        from models import QuizResult

        count, average_percent, best_percent = db.session.query(
            db.func.count(QuizResult.result_id),
            db.func.avg(QuizResult.score * 100.0 / QuizResult.question_count),
            db.func.max(QuizResult.score * 100.0 / QuizResult.question_count),
        ).filter_by(source_type='custom', source_id=selected_quiz.quiz_id).one()
        quiz_analytics = {
            'count': int(count or 0),
            'average_percent': round(float(average_percent or 0)),
            'best_percent': round(float(best_percent or 0)),
        }
    return render_template(
        'edit_quiz.html', quizzes=quizzes, selected_quiz=selected_quiz,
        quiz_analytics=quiz_analytics, quiz_page=quiz_page,
        quiz_collaborators=quiz_collaborators,
        quiz_share_links=quiz_share_links,
        selected_quiz_is_suspended=selected_quiz_is_suspended,
        can_change_quiz_visibility=can_change_quiz_visibility,
        can_manage_quiz_sharing=can_manage_quiz_sharing,
        **_pagination_context('edit_quiz_route'),
    )

# Create a custom quiz.
def create_custom_quiz_route():
    if not _current_user():
        return _login_required_response()
    from services import create_custom_quiz
    data = _request_data()
    title = data.get('title')
    description = data.get('description')
    tags = data.get('tags')
    is_public = _as_bool(data.get('is_public', False))
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    try:
        quiz = create_custom_quiz(_current_user_id(), title, is_public, description, tags)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    return redirect(url_for('edit_quiz_route', quiz_id=quiz.quiz_id))

# Update custom quiz metadata.
def edit_custom_quiz_metadata_route():
    if not _current_user():
        return _login_required_response()
    from services import edit_custom_quiz
    data = _request_data()
    quiz_id = _int_value(data.get('quiz_id'))
    title = data.get('title')
    description = data.get('description')
    tags = data.get('tags')
    visibility_supplied = 'is_public' in data
    is_public = _as_bool(data.get('is_public', False))
    owned_quiz = _owned_quiz(quiz_id, _current_user_id())
    if not owned_quiz:
        return jsonify({'error': 'You can only edit quizzes you own'}), 403
    if owned_quiz.owned_by != _current_user_id():
        if visibility_supplied and is_public != bool(owned_quiz.is_public):
            return jsonify({'error': 'Only the quiz owner can change visibility'}), 403
        is_public = bool(owned_quiz.is_public)
    if owned_quiz.is_suspended and is_public:
        return jsonify({'error': 'Suspended quizzes cannot be published'}), 403
    try:
        edit_custom_quiz(quiz_id, title, is_public, description, tags)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    return redirect(url_for('edit_quiz_route', quiz_id=quiz_id))

# Delete a custom quiz.
def delete_custom_quiz_route():
    if not _current_user():
        return _login_required_response()
    from services import delete_custom_quiz
    quiz_id = _int_value(_request_data().get('quiz_id'))
    if not _is_quiz_owner(quiz_id, _current_user_id()):
        return jsonify({'error': 'You can only delete quizzes you own'}), 403
    delete_custom_quiz(quiz_id)
    return redirect(url_for('edit_quiz_route'))

# Add a question to a quiz.
def add_quiz_question_route():
    if not _current_user():
        return _login_required_response()
    from services import add_quiz_question

    data = _request_data()
    quiz_id = _int_value(data.get('quiz_id'))
    if not _owned_quiz(quiz_id, _current_user_id()):
        return jsonify({'error': 'You can only edit quizzes you own'}), 403
    question_text = data.get('question')
    answer_mode = data.get('answer_mode', 'choice')
    q_type = data.get('q_type', 'dynamic')
    
    try:
        options_data, correct_count = _parse_quiz_question_options(data, q_type, answer_mode)
    except ValueError as exc:
        if _wants_json():
            return jsonify({'error': str(exc)}), 400
        return _redirect_with_fragment(
            'edit_quiz_route', fragment='quiz-editor', quiz_id=quiz_id,
            notice=str(exc), level='error',
        )
    validation_error = _validate_quiz_question_option_count(quiz_id, q_type, options_data, correct_count, answer_mode)
    if validation_error:
        return validation_error

    try:
        add_quiz_question(
            quiz_id, question_text, q_type, options_data, answer_mode=answer_mode,
            pool=data.get('pool'), explanation=data.get('explanation'),
        )
    except ValueError as exc:
        return _redirect_with_fragment('edit_quiz_route', fragment='quiz-editor', quiz_id=quiz_id, notice=str(exc), level='error')
    return _redirect_with_fragment('edit_quiz_route', fragment='quiz-editor', quiz_id=quiz_id, notice='Question added successfully', level='success')

# Delete a quiz question.
def delete_quiz_question_route():
    if not _current_user():
        return _login_required_response()
    from services import delete_quiz_question
    from models import QuizQuestion
    data = _request_data()
    question_id = _int_value(data.get('question_id'))
    quiz_id = _int_value(data.get('quiz_id'))
    if not _owned_quiz(quiz_id, _current_user_id()):
        return jsonify({'error': 'You can only edit quizzes you own'}), 403
    question = db.session.get(QuizQuestion, question_id)
    if not question or question.quiz_id != quiz_id:
        return jsonify({'error': 'Question not found'}), 404
    delete_quiz_question(question_id)
    return _redirect_with_fragment('edit_quiz_route', fragment='quiz-editor', quiz_id=quiz_id, notice='Question deleted', level='success')

# Replace a quiz question.
def edit_quiz_question_route():
    if not _current_user():
        return _login_required_response()
    from services import edit_quiz_question
    from models import QuizQuestion
    data = _request_data()
    quiz_id = _int_value(data.get('quiz_id'))
    question_id = _int_value(data.get('question_id'))
    if not _owned_quiz(quiz_id, _current_user_id()):
        return jsonify({'error': 'You can only edit quizzes you own'}), 403
    question = db.session.get(QuizQuestion, question_id)
    if not question or question.quiz_id != quiz_id:
        return jsonify({'error': 'Question not found'}), 404
    question_text = data.get('question')
    answer_mode = data.get('answer_mode', 'choice')
    q_type = data.get('q_type', 'dynamic')
    
    try:
        options_data, correct_count = _parse_quiz_question_options(data, q_type, answer_mode)
    except ValueError as exc:
        if _wants_json():
            return jsonify({'error': str(exc)}), 400
        return _redirect_with_fragment(
            'edit_quiz_route', fragment='quiz-editor', quiz_id=quiz_id,
            notice=str(exc), level='error',
        )
    validation_error = _validate_quiz_question_option_count(quiz_id, q_type, options_data, correct_count, answer_mode)
    if validation_error:
        return validation_error

    try:
        edit_quiz_question(
            question_id, question_text, q_type, options_data, answer_mode=answer_mode,
            pool=data.get('pool'), explanation=data.get('explanation'),
        )
    except ValueError as exc:
        return _redirect_with_fragment('edit_quiz_route', fragment='quiz-editor', quiz_id=quiz_id, notice=str(exc), level='error')
    
    return _redirect_with_fragment('edit_quiz_route', fragment='quiz-editor', quiz_id=quiz_id, notice='Question updated', level='success')

# Route registration.
# Register every route on the Flask app.
def register_routes(app, app_limiter=None):
    app_limiter = app_limiter or limiter
    app.extensions['cards_limiter'] = app_limiter
    limiter_token = _ACTIVE_LIMITER.set(app_limiter)
    app.before_request(_prepare_security_request)
    app.before_request(_canonicalize_static_asset)
    app.before_request(_validate_csrf)
    app.after_request(_set_security_headers)
    for status_code in (400, 401, 403, 404, 405, 413, 415, 422, 500):
        app.register_error_handler(status_code, _api_error_handler)
    app.register_error_handler(429, _rate_limit_response)

    @app.context_processor
    def inject_security_context():
        user = _current_user()
        csrf_token_required = _page_needs_csrf_token(user)
        return {
            'current_user': user,
            'active_theme': (user.theme_preference if user else None),
            'study_reminder': ({
                'enabled': user.study_reminder_enabled,
                'minutes': user.study_reminder_minutes,
                'time': f'{user.study_reminder_minutes // 60:02d}:{user.study_reminder_minutes % 60:02d}',
                'scope': _study_reminder_scope(user),
            } if user else {
                'enabled': False, 'minutes': 1080, 'time': '18:00',
                'scope': '',
            }),
            'csrf_token': _csrf_token,
            'ensure_csrf_token': _ensure_csrf_token,
            'csrf_token_required': csrf_token_required,
            'csp_nonce': g.csp_nonce,
            'asset_url': asset_url,
        }

    # Main pages
    app.add_url_rule('/', endpoint='index', view_func=index)
    app.add_url_rule('/dashboard', endpoint='dashboard', view_func=dashboard, methods=['GET'])
    app.add_url_rule('/saved', endpoint='saved_decks', view_func=saved_decks_route, methods=['GET'])
    app.add_url_rule('/saved-quizzes', endpoint='saved_quizzes', view_func=saved_quizzes_route, methods=['GET'])
    app.add_url_rule('/collections', endpoint='collections', view_func=collections_route, methods=['GET'])
    app.add_url_rule('/collections/<int:collection_id>', endpoint='public_collection', view_func=public_collection_route, methods=['GET'])
    app.add_url_rule('/collections/create', endpoint='create_collection', view_func=_limit(create_collection_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/collections/edit', endpoint='edit_collection', view_func=_limit(edit_collection_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/collections/delete', endpoint='delete_collection', view_func=_limit(delete_collection_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/collections/decks/add', endpoint='add_collection_deck', view_func=_limit(add_collection_deck_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/collections/decks/remove', endpoint='remove_collection_deck', view_func=_limit(remove_collection_deck_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/collections/decks/move', endpoint='move_collection_deck', view_func=_limit(move_collection_deck_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/healthz', endpoint='healthz', view_func=healthz, methods=['GET'])
    app.add_url_rule('/readyz', endpoint='readyz', view_func=readyz, methods=['GET'])
    app.add_url_rule('/service-worker.js', endpoint='service_worker', view_func=service_worker_route, methods=['GET'])
    app.add_url_rule('/api/v1/decks', endpoint='api_v1_decks', view_func=_limit(api_v1_decks_route, 'api', ['GET']), methods=['GET'])
    app.add_url_rule('/api/v1/decks/<int:deck_id>', endpoint='api_v1_deck_detail', view_func=_limit(api_v1_deck_detail_route, 'api', ['GET']), methods=['GET'])
    app.add_url_rule('/api/v1/quizzes', endpoint='api_v1_quizzes', view_func=_limit(api_v1_quizzes_route, 'api', ['GET']), methods=['GET'])
    app.add_url_rule('/api/v1/quizzes/<int:quiz_id>', endpoint='api_v1_quiz_detail', view_func=_limit(api_v1_quiz_detail_route, 'api', ['GET']), methods=['GET'])
    app.add_url_rule(
        '/verify-email', endpoint='verify_email',
        view_func=_anonymous_sensitive_limit(
            verify_email, 'reset_password', ['POST'], _verification_target_key,
        ),
        methods=['GET', 'POST'],
    )
    app.add_url_rule('/register', endpoint='register', view_func=_anonymous_sensitive_limit(register, 'register', ['POST'], _registration_target_key), methods=['GET', 'POST'])
    app.add_url_rule('/login', endpoint='login', view_func=_anonymous_sensitive_limit(login, 'login', ['POST'], _login_target_key), methods=['GET', 'POST'])
    app.add_url_rule('/two-factor', endpoint='two_factor_challenge', view_func=_anonymous_sensitive_limit(two_factor_challenge, 'two_factor', ['POST'], _two_factor_target_key), methods=['GET', 'POST'])
    app.add_url_rule('/two-factor/resend', endpoint='resend_two_factor_code', view_func=_anonymous_sensitive_limit(resend_two_factor_code, 'two_factor', ['POST'], _two_factor_target_key), methods=['POST'])
    app.add_url_rule('/forgot-password', endpoint='forgot_password', view_func=_anonymous_sensitive_limit(forgot_password, 'forgot_password', ['POST'], _recovery_target_key), methods=['GET', 'POST'])
    app.add_url_rule('/reset-password', endpoint='reset_password', view_func=_anonymous_sensitive_limit(reset_password, 'reset_password', ['POST'], _reset_target_key), methods=['GET', 'POST'])
    app.add_url_rule('/logout', endpoint='logout', view_func=logout, methods=['POST'])
    app.add_url_rule('/account', endpoint='account', view_func=_limit(account, 'account', ['POST']), methods=['GET', 'POST'])
    app.add_url_rule('/account/delete', endpoint='delete_account', view_func=_limit(delete_account, 'delete_account', ['POST']), methods=['POST'])
    app.add_url_rule('/account/verify-email', endpoint='resend_email_verification', view_func=_limit(resend_email_verification, 'two_factor', ['POST']), methods=['POST'])
    app.add_url_rule('/account/two-factor/email', endpoint='enable_email_two_factor', view_func=_limit(enable_email_two_factor_route, 'two_factor', ['POST']), methods=['POST'])
    app.add_url_rule('/account/two-factor/totp/start', endpoint='begin_totp_setup', view_func=_limit(begin_totp_setup_route, 'two_factor', ['POST']), methods=['POST'])
    app.add_url_rule('/account/two-factor/totp/confirm', endpoint='confirm_totp_setup', view_func=_limit(confirm_totp_setup_route, 'two_factor', ['POST']), methods=['POST'])
    app.add_url_rule('/account/two-factor/recovery-codes', endpoint='regenerate_two_factor_recovery_codes', view_func=_limit(regenerate_two_factor_recovery_codes_route, 'two_factor', ['POST']), methods=['POST'])
    app.add_url_rule('/account/two-factor/disable', endpoint='disable_two_factor', view_func=_limit(disable_two_factor_route, 'two_factor', ['POST']), methods=['POST'])
    app.add_url_rule('/theme', endpoint='update_theme', view_func=_limit(update_theme_route, 'account', ['POST']), methods=['POST'])
    app.add_url_rule('/account/reminder', endpoint='update_study_reminder', view_func=_limit(update_study_reminder_route, 'account', ['POST']), methods=['POST'])
    app.add_url_rule('/admin/users', endpoint='admin_users', view_func=_limit(admin_users, 'admin_users', ['POST']), methods=['GET', 'POST'])
    app.add_url_rule('/admin/audit-log', endpoint='admin_audit_log', view_func=admin_audit_log, methods=['GET'])
    app.add_url_rule('/admin/featured', endpoint='admin_featured_content', view_func=_limit(admin_featured_content_route, 'content_mutation', ['POST']), methods=['GET', 'POST'])
    app.add_url_rule('/moderation/reports', endpoint='moderation_reports', view_func=_limit(moderation_reports_route, 'content_mutation', ['POST']), methods=['GET', 'POST'])
    app.add_url_rule('/moderation/quiz-reports', endpoint='moderation_quiz_reports', view_func=_limit(moderation_quiz_reports_route, 'content_mutation', ['POST']), methods=['GET', 'POST'])
    app.add_url_rule('/moderation/unpublish', endpoint='moderate_unpublish', view_func=_limit(moderate_unpublish_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/edit', endpoint='edit', view_func=edit)
    app.add_url_rule('/view', endpoint='view', view_func=view)
    app.add_url_rule('/master', endpoint='master', view_func=master, methods=['GET'])
    app.add_url_rule('/review', endpoint='review_queue', view_func=review_queue_route, methods=['GET'])
    app.add_url_rule('/match', endpoint='match', view_func=match)
    app.add_url_rule('/reorder', endpoint='reorder', view_func=reorder)
    app.add_url_rule('/search', endpoint='search', view_func=_limit(search_route, 'search', ['GET']))
    app.add_url_rule('/public_deck', endpoint='public_deck', view_func=public_deck_route, methods=['GET'])
    app.add_url_rule('/decks/<deck_slug>', endpoint='public_deck_detail', view_func=public_deck_detail_route, methods=['GET'])
    app.add_url_rule('/decks/favorite', endpoint='toggle_deck_favorite', view_func=_limit(toggle_deck_favorite_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/decks/duplicate', endpoint='duplicate_deck', view_func=_limit(duplicate_deck_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/decks/rate', endpoint='rate_deck', view_func=_limit(rate_deck_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/decks/report', endpoint='report_deck', view_func=_limit(report_deck_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/creators/<username>', endpoint='creator_profile', view_func=creator_profile_route, methods=['GET'])
    app.add_url_rule('/s/<token>', endpoint='shared_deck', view_func=shared_deck_route, methods=['GET'])
    app.add_url_rule('/copy_public_deck', endpoint='copy_public_deck', view_func=_limit(copy_public_deck_route, 'public_copy', ['POST']), methods=['POST'])
    app.add_url_rule('/decks/share', endpoint='create_deck_share_link', view_func=_limit(create_deck_share_link_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/decks/share/revoke', endpoint='delete_deck_share_link', view_func=_limit(delete_deck_share_link_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/decks/collaborators', endpoint='add_deck_collaborator', view_func=_limit(add_deck_collaborator_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/decks/collaborators/remove', endpoint='remove_deck_collaborator', view_func=_limit(remove_deck_collaborator_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/public_quiz', endpoint='public_quiz', view_func=public_quiz_route, methods=['GET'])
    app.add_url_rule('/quizzes/<quiz_slug>', endpoint='public_quiz_detail', view_func=public_quiz_detail_route, methods=['GET'])
    app.add_url_rule('/quizzes/favorite', endpoint='toggle_quiz_favorite', view_func=_limit(toggle_quiz_favorite_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/quizzes/rate', endpoint='rate_quiz', view_func=_limit(rate_quiz_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/quizzes/report', endpoint='report_quiz', view_func=_limit(report_quiz_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/sq/<token>', endpoint='shared_quiz', view_func=shared_quiz_route, methods=['GET'])
    app.add_url_rule('/copy_public_quiz', endpoint='copy_public_quiz', view_func=_limit(copy_public_quiz_route, 'public_copy', ['POST']), methods=['POST'])
    app.add_url_rule('/quizzes/share', endpoint='create_quiz_share_link', view_func=_limit(create_quiz_share_link_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/quizzes/share/revoke', endpoint='delete_quiz_share_link', view_func=_limit(delete_quiz_share_link_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/quizzes/collaborators', endpoint='add_quiz_collaborator', view_func=_limit(add_quiz_collaborator_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/quizzes/collaborators/remove', endpoint='remove_quiz_collaborator', view_func=_limit(remove_quiz_collaborator_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/quiz', endpoint='quiz', view_func=quiz_route, methods=['GET'])
    app.add_url_rule('/quiz/history', endpoint='quiz_history', view_func=quiz_history_route, methods=['GET'])
    app.add_url_rule('/quiz/history/<int:result_id>', endpoint='quiz_result_detail', view_func=quiz_result_detail_route, methods=['GET'])
    app.add_url_rule('/quiz/start', endpoint='start_quiz', view_func=_limit(start_quiz_route, 'start_quiz', ['POST']), methods=['POST'])
    app.add_url_rule('/edit_quiz', endpoint='edit_quiz_route', view_func=edit_quiz_route, methods=['GET'])
    
    # Custom Quiz operations
    app.add_url_rule('/create_custom_quiz', endpoint='create_custom_quiz', view_func=_limit(create_custom_quiz_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/edit_custom_quiz', endpoint='edit_custom_quiz', view_func=_limit(edit_custom_quiz_metadata_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/delete_custom_quiz', endpoint='delete_custom_quiz', view_func=_limit(delete_custom_quiz_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/add_quiz_question', endpoint='add_quiz_question', view_func=_limit(add_quiz_question_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/edit_quiz_question', endpoint='edit_quiz_question', view_func=_limit(edit_quiz_question_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/delete_quiz_question', endpoint='delete_quiz_question', view_func=_limit(delete_quiz_question_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/score_quiz', endpoint='score_quiz', view_func=_limit(score_quiz_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/master/rate', endpoint='master_rate', view_func=_limit(master_rate_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/master/reset', endpoint='master_reset', view_func=_limit(master_reset_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/review/rate', endpoint='review_queue_rate', view_func=_limit(review_queue_rate_route, 'content_mutation', ['POST']), methods=['POST'])

    # Deck operations
    app.add_url_rule('/create_deck', endpoint='create_deck', view_func=_limit(create_deck_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/import_deck', endpoint='import_deck', view_func=_limit(import_deck_route, 'import_deck', ['POST']), methods=['POST'])
    app.add_url_rule('/import_deck/preview', endpoint='import_deck_preview', view_func=_limit(import_deck_preview_route, 'import_deck', ['POST']), methods=['POST'])
    app.add_url_rule('/decks/<int:deck_id>/download.csv', endpoint='download_deck_csv', view_func=download_deck_csv_route, methods=['GET'])
    app.add_url_rule('/decks/<int:deck_id>/download.tsv', endpoint='download_deck_tsv', view_func=download_deck_tsv_route, methods=['GET'])
    app.add_url_rule('/get_decks', endpoint='get_decks', view_func=_limit(get_deck_list_route, 'api', ['POST']), methods=['POST'])
    app.add_url_rule('/delete_deck', endpoint='delete_deck', view_func=_limit(delete_deck_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/edit_deck', endpoint='edit_deck', view_func=_limit(edit_deck_route, 'content_mutation', ['POST']), methods=['POST'])

    # Card operations
    app.add_url_rule('/add_card', endpoint='add_card', view_func=_limit(add_card_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/delete_card', endpoint='delete_card', view_func=_limit(delete_card_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/match_answer', endpoint='match_answer', view_func=_limit(match_answer_route, 'api', ['POST']), methods=['POST'])
    app.add_url_rule('/match_attempt', endpoint='match_attempt', view_func=_limit(match_attempt_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/delete_answer', endpoint='delete_answer', view_func=_limit(delete_answer_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/list_cards', endpoint='list_cards', view_func=_limit(list_cards_route, 'api', ['POST']), methods=['POST'])
    app.add_url_rule('/get_card', endpoint='get_card', view_func=_limit(get_card_route, 'api', ['POST']), methods=['POST'])
    app.add_url_rule('/edit_card', endpoint='edit_card', view_func=_limit(edit_card_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/cards/bulk', endpoint='bulk_cards', view_func=_limit(bulk_cards_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/move_card', endpoint='move_card', view_func=_limit(move_card_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/swap_cards', endpoint='swap_cards', view_func=_limit(swap_cards_route, 'content_mutation', ['POST']), methods=['POST'])
    app.add_url_rule('/check_reorder', endpoint='check_reorder', view_func=_limit(check_reorder_route, 'api', ['POST']), methods=['POST'])
    _ACTIVE_LIMITER.reset(limiter_token)
