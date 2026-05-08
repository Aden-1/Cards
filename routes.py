import re
import secrets
from functools import wraps

from flask import abort, jsonify, redirect, render_template, request, session, url_for


# Shared request helpers.
# Read request payload from JSON or form data.
def _request_data():
    return request.get_json(silent=True) or request.values.to_dict(flat=True)


# Parse an integer safely.
def _int_value(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# Redirect to a route with an optional fragment.
def _redirect_with_fragment(endpoint, fragment=None, **values):
    target = url_for(endpoint, **values)
    if fragment:
        target = f'{target}#{fragment}'
    return redirect(target)


def _current_user():
    from app import get_user_by_id
    user_id = session.get('user_id')
    if not user_id:
        return None
    user = get_user_by_id(user_id)
    if not user or not user.is_active:
        session.clear()
        return None
    return user


def _current_user_id():
    user = _current_user()
    return user.user_id if user else None


def _wants_json():
    return request.is_json or request.accept_mimetypes.best == 'application/json'


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
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        user = _current_user()
        if not user:
            return _login_required_response()
        if not user.is_admin:
            if _wants_json():
                return jsonify({'error': 'Admin access required'}), 403
            return redirect(url_for('index', notice='Admin access required.', level='error'))
        return view_func(*args, **kwargs)
    return wrapped


def _csrf_token():
    token = session.get('csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['csrf_token'] = token
    return token


def _validate_csrf():
    if request.method != 'POST':
        return None
    sent_token = request.headers.get('X-CSRFToken') or request.form.get('csrf_token')
    if not sent_token or not secrets.compare_digest(sent_token, session.get('csrf_token', '')):
        if _wants_json():
            return jsonify({'error': 'Invalid or missing CSRF token'}), 400
        abort(400)
    return None


def _valid_username(username):
    return bool(re.fullmatch(r'[A-Za-z0-9_.-]{3,40}', username or ''))


def _valid_password(password):
    return bool(password) and len(password) >= 8


def _safe_next_url(next_url):
    if next_url and next_url.startswith('/') and not next_url.startswith('//'):
        return next_url
    return url_for('index')


def _first_account_role():
    from models import User
    return 'admin' if User.query.count() == 0 else 'standard'


def register():
    from app import create_user, get_user, get_user_by_email

    if request.method == 'GET':
        return render_template('register.html')

    data = _request_data()
    username = (data.get('username') or '').strip()
    email = (data.get('email') or '').strip().lower() or None
    password = data.get('password') or ''
    confirm_password = data.get('confirm_password') or ''

    if not _valid_username(username):
        return render_template('register.html', error='Usernames must be 3-40 letters, numbers, dots, dashes, or underscores.'), 400
    if not _valid_password(password):
        return render_template('register.html', error='Passwords must be at least 8 characters.'), 400
    if password != confirm_password:
        return render_template('register.html', error='Passwords do not match.'), 400
    if get_user(username):
        return render_template('register.html', error='That username is already taken.'), 400
    if email and get_user_by_email(email):
        return render_template('register.html', error='That email is already in use.'), 400

    user = create_user(username=username, password=password, email=email, role=_first_account_role())
    session.clear()
    session['user_id'] = user.user_id
    session['csrf_token'] = secrets.token_urlsafe(32)
    return redirect(url_for('edit', notice='Account created', level='success'))


def login():
    from app import get_user

    if request.method == 'GET':
        return render_template('login.html', next=request.args.get('next', ''))

    data = _request_data()
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    user = get_user(username)
    if not user or not user.is_active or not user.check_password(password):
        return render_template('login.html', error='Invalid username or password.', next=data.get('next', '')), 401

    session.clear()
    session['user_id'] = user.user_id
    session['csrf_token'] = secrets.token_urlsafe(32)
    return redirect(_safe_next_url(data.get('next')))


def logout():
    session.clear()
    return redirect(url_for('index', notice='Logged out', level='success'))


@login_required
def account():
    from app import get_user, get_user_by_email, update_user_account

    user = _current_user()
    if request.method == 'GET':
        return render_template('account.html', user=user)

    data = _request_data()
    username = (data.get('username') or '').strip()
    email = (data.get('email') or '').strip().lower() or None
    current_password = data.get('current_password') or ''
    new_password = data.get('new_password') or ''
    confirm_password = data.get('confirm_password') or ''

    if not user.check_password(current_password):
        return render_template('account.html', user=user, error='Enter your current password to save account changes.'), 400
    if not _valid_username(username):
        return render_template('account.html', user=user, error='Usernames must be 3-40 letters, numbers, dots, dashes, or underscores.'), 400
    existing_user = get_user(username)
    if existing_user and existing_user.user_id != user.user_id:
        return render_template('account.html', user=user, error='That username is already taken.'), 400
    existing_email = get_user_by_email(email)
    if existing_email and existing_email.user_id != user.user_id:
        return render_template('account.html', user=user, error='That email is already in use.'), 400
    if new_password:
        if not _valid_password(new_password):
            return render_template('account.html', user=user, error='New passwords must be at least 8 characters.'), 400
        if new_password != confirm_password:
            return render_template('account.html', user=user, error='New passwords do not match.'), 400

    updated_user = update_user_account(user.user_id, username=username, email=email, password=new_password or None)
    return render_template('account.html', user=updated_user, success='Account updated.')


@admin_required
def admin_users():
    from app import _delete_content_fts_index_row
    from models import Deck, Quiz, User, db

    if request.method == 'GET':
        users = User.query.order_by(User.user_id).all()
        return render_template('admin_users.html', users=users)

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

        # Keep search index clean before cascading deletes.
        owned_deck_ids = [deck_id for (deck_id,) in db.session.query(Deck.deck_id).filter_by(owned_by=target_user.user_id).all()]
        owned_quiz_ids = [quiz_id for (quiz_id,) in db.session.query(Quiz.quiz_id).filter_by(owned_by=target_user.user_id).all()]
        for deck_id in owned_deck_ids:
            _delete_content_fts_index_row('deck', deck_id)
        for quiz_id in owned_quiz_ids:
            _delete_content_fts_index_row('quiz', quiz_id)

        db.session.delete(target_user)
        db.session.commit()
        return redirect(url_for('admin_users', notice='User and all owned data deleted.', level='success'))

    if action == 'promote_admin':
        if target_user.role == 'admin':
            return redirect(url_for('admin_users', notice='User is already an admin.', level='success'))
        target_user.role = 'admin'
        db.session.commit()
        return redirect(url_for('admin_users', notice='User promoted to admin.', level='success'))

    if action == 'promote_moderator':
        if target_user.role == 'moderator':
            return redirect(url_for('admin_users', notice='User is already a moderator.', level='success'))
        if target_user.role == 'admin':
            return redirect(url_for('admin_users', notice='Admins cannot be changed to moderator here.', level='error'))
        target_user.role = 'moderator'
        db.session.commit()
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
        return redirect(url_for('admin_users', notice='User demoted to standard.', level='success'))

    return redirect(url_for('admin_users', notice='Unknown admin action.', level='error'))


def _owned_deck(deck_id, user_id):
    from models import Deck
    if not deck_id:
        return None
    return Deck.query.filter_by(deck_id=deck_id, owned_by=user_id).first()


def _owned_quiz(quiz_id, user_id):
    from models import Quiz
    if not quiz_id:
        return None
    return Quiz.query.filter_by(quiz_id=quiz_id, owned_by=user_id).first()


# Page routes.
# Render the home page.
def index():
    return render_template('index.html')


# Deck editor.
# Render the deck editor page.
def edit():
    if not _current_user():
        return _login_required_response()
    user_id = _current_user_id()
    from app import get_user_decks, get_deck_details

    decks = get_user_decks(user_id)
    deck_data = [{
        'deck_id': deck.deck_id,
        'description': deck.description,
        'detailed_description': deck.detailed_description,
        'tags': deck.tags,
        'sortable': deck.sortable,
        'is_public': deck.is_public,
        'is_owned': bool(user_id is not None and deck.owned_by == user_id),
        'card_count': len(deck.cards),
    } for deck in decks]

    selected_deck_id = _int_value(request.args.get('deck_id'))
    selected_deck = None
    selected_deck_export_text = ''
    selected_cards = []
    if selected_deck_id and _owned_deck(selected_deck_id, user_id):
        from app import export_deck_as_text
        selected_deck = get_deck_details(selected_deck_id, shuffle_cards=False, shuffle_answers=False)
        if selected_deck and selected_deck['deck_id']:
            selected_cards = selected_deck['cards']
            selected_deck_record = _owned_deck(selected_deck_id, user_id)
            selected_deck_export_text = export_deck_as_text(selected_deck_record) if selected_deck_record else ''
        else:
            selected_deck = None

    return render_template(
        'edit.html',
        user_id=user_id,
        decks=deck_data,
        selected_deck=selected_deck,
        selected_cards=selected_cards,
        selected_deck_id=selected_deck_id,
        selected_deck_export_text=selected_deck_export_text,
    )


# Study view.
# Render the study page.
def view():
    user_id = _current_user_id()
    from app import get_accessible_decks, get_deck_details

    decks = get_accessible_decks(user_id)
    deck_data = [{
        'deck_id': deck.deck_id,
        'description': deck.description,
        'detailed_description': deck.detailed_description,
        'tags': deck.tags,
        'sortable': deck.sortable,
        'is_public': deck.is_public,
        'is_owned': bool(user_id is not None and deck.owned_by == user_id),
        'card_count': len(deck.cards),
    } for deck in decks]

    selected_deck_id = _int_value(request.args.get('deck_id'))
    accessible_deck_ids = {deck['deck_id'] for deck in deck_data}
    if selected_deck_id not in accessible_deck_ids:
        selected_deck_id = None
    study_deck = get_deck_details(selected_deck_id, shuffle_cards=False, shuffle_answers=False) if selected_deck_id else None
    selected_deck_is_owned = any(deck['deck_id'] == selected_deck_id and deck['is_owned'] for deck in deck_data)

    return render_template('view.html', user_id=user_id, decks=deck_data, study_deck=study_deck, selected_deck_id=selected_deck_id, selected_deck_is_owned=selected_deck_is_owned)


# Matching game.
# Render the matching game page.
def match():
    user_id = _current_user_id()
    from app import get_accessible_decks, get_deck_study_data

    decks = get_accessible_decks(user_id)
    deck_data = [{
        'deck_id': deck.deck_id,
        'description': deck.description,
        'detailed_description': deck.detailed_description,
        'tags': deck.tags,
        'sortable': deck.sortable,
        'is_public': deck.is_public,
        'is_owned': bool(user_id is not None and deck.owned_by == user_id),
        'card_count': len(deck.cards),
    } for deck in decks]

    selected_deck_id = _int_value(request.args.get('deck_id'))
    selected_question_id = _int_value(request.args.get('selected_question'))
    error_message = request.args.get('error')
    accessible_deck_ids = {deck['deck_id'] for deck in deck_data}
    if selected_deck_id not in accessible_deck_ids:
        selected_deck_id = None
    match_deck = get_deck_study_data(selected_deck_id, shuffle=True) if selected_deck_id else None
    selected_deck_is_owned = any(deck['deck_id'] == selected_deck_id and deck['is_owned'] for deck in deck_data)

    return render_template(
        'match.html',
        user_id=user_id,
        decks=deck_data,
        match_deck=match_deck,
        selected_deck_id=selected_deck_id,
        selected_deck_is_owned=selected_deck_is_owned,
        selected_question_id=selected_question_id,
        error_message=error_message,
    )


# Render the reorder game page.
def reorder():
    user_id = _current_user_id()
    from app import get_accessible_decks, get_deck_details

    decks = get_accessible_decks(user_id)
    # Only sortable decks can enter this game.
    sortable_decks = [deck for deck in decks if deck.sortable]
    deck_data = [{
        'deck_id': deck.deck_id,
        'description': deck.description,
        'detailed_description': deck.detailed_description,
        'tags': deck.tags,
        'sortable': deck.sortable,
        'is_public': deck.is_public,
        'is_owned': bool(user_id is not None and deck.owned_by == user_id),
        'card_count': len(deck.cards),
    } for deck in sortable_decks]

    selected_deck_id = _int_value(request.args.get('deck_id'))
    sortable_deck_ids = {deck['deck_id'] for deck in deck_data}
    if selected_deck_id not in sortable_deck_ids:
        selected_deck_id = None

    # Start each round with a shuffled card list.
    reorder_deck = get_deck_details(selected_deck_id, shuffle_cards=True, shuffle_answers=False) if selected_deck_id else None
    selected_deck_is_owned = any(deck['deck_id'] == selected_deck_id and deck['is_owned'] for deck in deck_data)

    return render_template(
        'reorder.html',
        user_id=user_id,
        decks=deck_data,
        reorder_deck=reorder_deck,
        selected_deck_id=selected_deck_id,
        selected_deck_is_owned=selected_deck_is_owned,
    )


# Deck routes.

# Handle deck creation.
def create_deck_route():
    if not _current_user():
        return _login_required_response()
    from app import create_deck

    data = _request_data()
    user_id = _current_user_id()
    description = data.get('description')
    detailed_description = data.get('detailed_description')
    tags = data.get('tags')
    sortable = str(data.get('sortable', False)).lower() in ('1', 'true', 'yes', 'on')
    is_public = str(data.get('is_public', False)).lower() in ('1', 'true', 'yes', 'on')

    if not description:
        return jsonify({'error': 'User ID and description are required'}), 400
    
    deck = create_deck(user_id, description, sortable, is_public, detailed_description, tags)
    if request.is_json:
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
    from app import get_user_decks

    user_id = _current_user_id()

    if not user_id:
        return jsonify({'error': 'User ID is required'}), 400
    
    decks = get_user_decks(user_id)
    if decks:
        decks_data = [{'deck_id': d.deck_id, 'description': d.description, 'sortable': d.sortable, 'card_count': len(d.cards)} for d in decks]
        return jsonify({'success': True, 'decks': decks_data})
    else:
        return jsonify({'success': True, 'decks': []})


# Delete a deck.
def delete_deck_route():
    if not _current_user():
        return _login_required_response()
    from app import delete_deck

    data = _request_data()
    deck_id = _int_value(data.get('deck_id'))
    user_id = _current_user_id()

    if not deck_id:
        return jsonify({'error': 'Deck ID is required'}), 400
    if not _owned_deck(deck_id, user_id):
        return jsonify({'error': 'You can only delete decks you own'}), 403
    
    deleted = delete_deck(deck_id)
    if deleted:
        if request.is_json:
            return jsonify({'success': True, 'deck_id': deck_id})
        return _redirect_with_fragment('edit', fragment='decks-section', notice='Deck deleted', level='success')
    else:
        return jsonify({'error': 'Deck not found'}), 404


# Update deck settings.
def edit_deck_route():
    if not _current_user():
        return _login_required_response()
    from app import edit_deck

    data = _request_data()
    deck_id = _int_value(data.get('deck_id'))
    user_id = _current_user_id()
    description = data.get('description')
    detailed_description = data.get('detailed_description')
    tags = data.get('tags')
    sortable = str(data.get('sortable', False)).lower() in ('1', 'true', 'yes', 'on')
    is_public = str(data.get('is_public', False)).lower() in ('1', 'true', 'yes', 'on')

    if not deck_id or not description:
        return jsonify({'error': 'Deck ID and description are required'}), 400
    if not _owned_deck(deck_id, user_id):
        return jsonify({'error': 'You can only edit decks you own'}), 403
    
    deck = edit_deck(deck_id, description, sortable, is_public, detailed_description, tags)
    if deck:
        if request.is_json:
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
    from app import add_card

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

    if request.is_json:
        return jsonify({'success': True, 'card_id': card.card_id})
    return _redirect_with_fragment('edit', deck_id=deck_id, fragment='deck-editor', notice='Card added', level='success')


# Delete a card.
def delete_card_route():
    if not _current_user():
        return _login_required_response()
    from app import delete_card
    from models import Card

    data = _request_data()
    card_id = _int_value(data.get('card_id'))
    deck_id = _int_value(data.get('deck_id'))
    user_id = _current_user_id()

    if not card_id:
        return jsonify({'error': 'Card ID is required'}), 400
    card = Card.query.get(card_id)
    if not card or not _owned_deck(card.deck_id, user_id):
        return jsonify({'error': 'You can only edit decks you own'}), 403
    
    deleted = delete_card(card_id)
    if deleted:
        if request.is_json:
            return jsonify({'success': True, 'card_id': card_id})
        return _redirect_with_fragment('edit', deck_id=deck_id, fragment='deck-editor', notice='Card deleted', level='success') if deck_id else _redirect_with_fragment('edit', fragment='decks-section', notice='Card deleted', level='success')
    else:
        return jsonify({'error': 'Card not found'}), 404


# Update a card and its answers.
def edit_card_route():
    if not _current_user():
        return _login_required_response()
    from app import edit_card
    from models import Card

    data = _request_data()
    card_id = _int_value(data.get('card_id'))
    deck_id = _int_value(data.get('deck_id'))
    user_id = _current_user_id()
    question = data.get('question')
    answers = data.get('answers')
    
    if not card_id or not question:
        return jsonify({'error': 'Card ID and question are required'}), 400
    card_record = Card.query.get(card_id)
    if not card_record or not _owned_deck(card_record.deck_id, user_id):
        return jsonify({'error': 'You can only edit decks you own'}), 403
    
    card = edit_card(card_id, question, answers)
    if card:
        if isinstance(card, dict) and card.get('deleted'):
            if request.is_json:
                return jsonify({'success': True, 'card_id': card_id, 'deleted': True})
            return _redirect_with_fragment('edit', deck_id=card.get('deck_id') or deck_id, fragment='deck-editor', notice='Card updated', level='success')
        if request.is_json:
            return jsonify({'success': True, 'card_id': card.card_id})
        return _redirect_with_fragment('edit', deck_id=deck_id, fragment='deck-editor', notice='Card updated', level='success')
    else:
        return jsonify({'error': 'Card not found'}), 404


# List cards in a deck.
def list_cards_route():
    from app import list_cards_from_deck, get_deck_details
    from models import Deck

    data = _request_data()
    deck_id = _int_value(data.get('deck_id'))
    shuffle = str(data.get('shuffle', False)).lower() in ('1', 'true', 'yes', 'on')
    detailed = str(data.get('detailed', False)).lower() in ('1', 'true', 'yes', 'on')

    if not deck_id:
        return jsonify({'error': 'Deck ID is required'}), 400
    deck_record = Deck.query.get(deck_id)
    user_id = _current_user_id()
    if not deck_record or (not deck_record.is_public and deck_record.owned_by != user_id):
        return jsonify({'error': 'Deck not found'}), 404


def import_deck_route():
    if not _current_user():
        return _login_required_response()
    from app import import_deck

    data = _request_data()
    user_id = _current_user_id()
    description = (data.get('description') or '').strip()
    detailed_description = data.get('detailed_description')
    tags = data.get('tags')
    sortable = str(data.get('sortable', False)).lower() in ('1', 'true', 'yes', 'on')
    is_public = str(data.get('is_public', False)).lower() in ('1', 'true', 'yes', 'on')
    import_text = data.get('import_text')

    if not description:
        return _redirect_with_fragment('edit', fragment='deck-import', notice='Deck name is required.', level='error')

    try:
        result = import_deck(
            user_id=user_id,
            description=description,
            raw_text=import_text,
            sortable=sortable,
            is_public=is_public,
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
    
    if detailed:
        deck = get_deck_details(deck_id, shuffle_cards=shuffle, shuffle_answers=shuffle)
        cards = deck['cards'] if deck else None
    else:
        cards = list_cards_from_deck(deck_id, detailed=False, shuffle=shuffle)
    if cards is not None:
        return jsonify({'success': True, 'cards': cards})
    else:
        return jsonify({'success': True, 'cards': []})


# Return one card with answers.
def get_card_route():
    from app import get_card_from_deck
    from models import Card

    data = _request_data()
    card_id = _int_value(data.get('card_id'))

    if not card_id:
        return jsonify({'error': 'Card ID is required'}), 400
    card_record = Card.query.get(card_id)
    user_id = _current_user_id()
    if not card_record or (not card_record.deck.is_public and card_record.deck.owned_by != user_id):
        return jsonify({'error': 'Card not found'}), 404
    
    card = get_card_from_deck(card_id)
    if card:
        return jsonify({'success': True, 'card': card})
    else:
        return jsonify({'error': 'Card not found'}), 404


# Match only checks the pair, it does not mutate the answer row.
# Validate one matching-game answer.
def match_answer_route():
    from models import CardAnswer

    data = _request_data()
    answer_id = _int_value(data.get('answer_id'))
    selected_question_id = _int_value(data.get('selected_question_id'))

    if not answer_id:
        return jsonify({'error': 'Answer ID is required'}), 400

    answer = CardAnswer.query.get(answer_id)
    if not answer:
        return jsonify({'error': 'Answer not found'}), 404

    if not selected_question_id:
        return jsonify({'error': 'Select a question tile first'}), 400

    if answer.card_id != selected_question_id:
        return jsonify({'error': 'That answer does not match the selected question'}), 400

    # Last answer means the question tile should disappear too.
    remaining_answers = CardAnswer.query.filter_by(card_id=selected_question_id).count() - 1
    card_deleted = remaining_answers == 0

    return jsonify({
        'success': True,
        'answer_deleted': True,
        'card_deleted': card_deleted,
        'card_id': selected_question_id,
        'remaining_answers': remaining_answers
    })


# Delete one answer in edit or match mode.
def delete_answer_route():
    if not _current_user():
        return _login_required_response()
    from app import delete_answer
    from models import CardAnswer

    data = _request_data()
    answer_id = _int_value(data.get('answer_id'))
    deck_id = _int_value(data.get('deck_id'))
    selected_question_id = _int_value(data.get('selected_question_id'))
    context = data.get('context')
    user_id = _current_user_id()

    if not answer_id:
        return jsonify({'error': 'Answer ID is required'}), 400

    answer = CardAnswer.query.get(answer_id)
    if not answer:
        return jsonify({'error': 'Answer not found'}), 404
    user_id = _current_user_id()
    if not answer.card.deck.is_public and answer.card.deck.owned_by != user_id:
        return jsonify({'error': 'Answer not found'}), 404
    if not _owned_deck(answer.card.deck_id, user_id):
        return jsonify({'error': 'You can only edit decks you own'}), 403

    if context == 'edit':
        deleted = delete_answer(answer_id)
        if deleted:
            if request.is_json:
                return jsonify({'success': True, **deleted})
            return _redirect_with_fragment('edit', deck_id=deleted.get('deck_id') or deck_id, fragment='deck-editor', notice='Answer removed', level='success')
        return jsonify({'error': 'Answer not found'}), 404

    if not selected_question_id:
        if request.is_json:
            return jsonify({'error': 'Select a question tile first'}), 400
        return redirect(url_for('match', deck_id=deck_id or answer.card.deck_id, error='Select a question tile first'))

    if answer.card_id != selected_question_id:
        if request.is_json:
            return jsonify({'error': 'That answer does not match the selected question'}), 400
        return redirect(url_for('match', deck_id=deck_id or answer.card.deck_id, selected_question=selected_question_id, error='That answer does not match the selected question'))

    deleted = delete_answer(answer_id)
    if deleted:
        next_selected = None if deleted.get('card_deleted') else selected_question_id
        if request.is_json:
            return jsonify({'success': True, **deleted})
        return redirect(url_for('match', deck_id=deleted.get('deck_id') or deck_id, selected_question=next_selected or ''))
    return jsonify({'error': 'Answer not found'}), 404


# Move a card one slot.
def move_card_route():
    if not _current_user():
        return _login_required_response()
    from app import move_card_in_deck
    from models import Card

    data = _request_data()
    card_id = _int_value(data.get('card_id'))
    deck_id = _int_value(data.get('deck_id'))
    direction = str(data.get('direction', '')).lower()
    user_id = _current_user_id()

    if not card_id or direction not in ('up', 'down'):
        return jsonify({'error': 'Card ID and valid direction are required'}), 400
    card = Card.query.get(card_id)
    if not card or not _owned_deck(card.deck_id, user_id):
        return jsonify({'error': 'You can only edit decks you own'}), 403

    result = move_card_in_deck(card_id, direction)
    if not result.get('success'):
        return jsonify({'error': result.get('error', 'Unable to move card')}), 400

    if request.is_json:
        return jsonify({'success': True, **result})
    return _redirect_with_fragment('edit', deck_id=result.get('deck_id') or deck_id, fragment='deck-editor')


# Swap two cards in a sortable deck.
def swap_cards_route():
    if not _current_user():
        return _login_required_response()
    from app import swap_cards_in_deck
    from models import Card

    payload = request.get_json(silent=True) or {}
    card_id = _int_value(payload.get('card_id'))
    target_card_id = _int_value(payload.get('target_card_id'))
    user_id = _current_user_id()

    if not card_id or not target_card_id:
        return jsonify({'error': 'Both card IDs are required'}), 400
    first_card = Card.query.get(card_id)
    second_card = Card.query.get(target_card_id)
    if not first_card or not second_card or not _owned_deck(first_card.deck_id, user_id):
        return jsonify({'error': 'You can only edit decks you own'}), 403

    result = swap_cards_in_deck(card_id, target_card_id)
    if not result.get('success'):
        return jsonify({'error': result.get('error', 'Unable to swap cards')}), 400

    return jsonify({'success': True, **result})


# Check a submitted reorder attempt.
def check_reorder_route():
    from app import check_deck_order
    from models import Deck

    payload = request.get_json(silent=True) or {}
    deck_id = _int_value(payload.get('deck_id'))
    ordered_card_ids = payload.get('ordered_card_ids')

    if not deck_id:
        return jsonify({'error': 'Deck ID is required'}), 400
    deck_record = Deck.query.get(deck_id)
    user_id = _current_user_id()
    if not deck_record or (not deck_record.is_public and deck_record.owned_by != user_id):
        return jsonify({'error': 'Deck not found'}), 404

    if not isinstance(ordered_card_ids, list):
        return jsonify({'error': 'ordered_card_ids must be a list'}), 400

    try:
        # Normalize IDs before comparing order.
        normalized_card_ids = [int(card_id) for card_id in ordered_card_ids]
    except (TypeError, ValueError):
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
    from app import search_public_content

    query = request.args.get('q', '')
    user_id = _current_user_id()
    results = search_public_content(query, user_id=user_id) if query else {
        'decks': [],
        'quizzes': [],
        'has_exact_match': False,
        'query_tokens': [],
        'expanded_tokens': [],
    }

    return render_template(
        'search.html',
        query=query,
        decks=results['decks'],
        quizzes=results['quizzes'],
        has_exact_match=results['has_exact_match'],
        query_tokens=results['query_tokens'],
        expanded_tokens=results['expanded_tokens'],
    )


# Public quiz detail (read-only).
def public_quiz_route():
    from models import Quiz

    user_id = _current_user_id()
    quiz_id = _int_value(request.args.get('quiz_id'))
    if not quiz_id:
        return redirect(url_for('search'))

    quiz = Quiz.query.get(quiz_id)
    if not quiz or (not quiz.is_public and quiz.owned_by != user_id):
        return redirect(url_for('search'))

    return render_template('public_quiz.html', quiz=quiz, user_id=user_id)


# Copy a public quiz to the current user's account.
def copy_public_quiz_route():
    if not _current_user():
        return _login_required_response()
    from app import copy_public_quiz_to_user

    data = _request_data()
    source_quiz_id = _int_value(data.get('quiz_id'))
    if not source_quiz_id:
        return redirect(url_for('search'))

    copied_quiz = copy_public_quiz_to_user(source_quiz_id, user_id=_current_user_id())
    if not copied_quiz:
        return redirect(url_for('search'))

    return _redirect_with_fragment(
        'edit_quiz_route',
        quiz_id=copied_quiz.quiz_id,
        fragment='quiz-editor',
        notice='Quiz copied to your account',
        level='success',
    )


# Public deck detail (read-only).
def public_deck_route():
    from models import Deck

    user_id = _current_user_id()
    deck_id = _int_value(request.args.get('deck_id'))
    if not deck_id:
        return redirect(url_for('search'))

    deck = Deck.query.get(deck_id)
    if not deck or (not deck.is_public and deck.owned_by != user_id):
        return redirect(url_for('search'))

    return render_template('public_deck.html', deck=deck, user_id=user_id)


# Copy a public deck to the current user's account.
def copy_public_deck_route():
    if not _current_user():
        return _login_required_response()
    from app import copy_public_deck_to_user

    data = _request_data()
    source_deck_id = _int_value(data.get('deck_id'))
    if not source_deck_id:
        return redirect(url_for('search'))

    copied_deck = copy_public_deck_to_user(source_deck_id, user_id=_current_user_id())
    if not copied_deck:
        return redirect(url_for('search'))

    return _redirect_with_fragment(
        'edit',
        deck_id=copied_deck.deck_id,
        fragment='deck-editor',
        notice='Deck copied to your account',
        level='success',
    )


# Render the quiz launcher and quiz data.
def quiz_route():
    from app import get_accessible_decks, get_accessible_custom_quizzes, generate_quiz_data
    user_id = _current_user_id()
    decks = get_accessible_decks(user_id)
    deck_data = [{
        'deck_id': deck.deck_id,
        'description': deck.description,
        'is_owned': bool(user_id is not None and deck.owned_by == user_id),
        'card_count': len(deck.cards),
    } for deck in decks]
    
    custom_quizzes = get_accessible_custom_quizzes(user_id)
    accessible_custom_quiz_ids = {quiz.quiz_id for quiz in custom_quizzes}

    selected_deck_id = None
    selected_custom_quiz_id = None
    selected_source = request.args.get('quiz_source', '').strip()

    if selected_source.startswith('deck:'):
        selected_deck_id = _int_value(selected_source.split(':', 1)[1])
    elif selected_source.startswith('custom:'):
        selected_custom_quiz_id = _int_value(selected_source.split(':', 1)[1])
    else:
        # Keep older deck/custom_quiz links working.
        selected_deck_id = _int_value(request.args.get('deck_id'))
        selected_custom_quiz_id = _int_value(request.args.get('custom_quiz_id'))
        if selected_deck_id and selected_custom_quiz_id:
            # Prefer a single source.
            selected_custom_quiz_id = None
        if selected_deck_id:
            selected_source = f'deck:{selected_deck_id}'
        elif selected_custom_quiz_id:
            selected_source = f'custom:{selected_custom_quiz_id}'

    accessible_deck_ids = {deck['deck_id'] for deck in deck_data}
    if selected_deck_id not in accessible_deck_ids:
        selected_deck_id = None
    if selected_custom_quiz_id not in accessible_custom_quiz_ids:
        selected_custom_quiz_id = None
    
    quiz_data = None
    
    if selected_deck_id:
        quiz_data = generate_quiz_data(deck_id=selected_deck_id)
    elif selected_custom_quiz_id:
        quiz_data = generate_quiz_data(custom_quiz_id=selected_custom_quiz_id)
        
    return render_template('quiz.html', decks=deck_data, custom_quizzes=custom_quizzes, 
                           selected_deck_id=selected_deck_id, 
                           selected_custom_quiz_id=selected_custom_quiz_id, 
                           selected_source=selected_source,
                           quiz_data=quiz_data)


# Score a submitted quiz.
def score_quiz_route():
    # Strictly score the submitted options.
    data = request.json
    submitted_answers = data.get('answers', {})
    quiz_questions = data.get('quiz_data', [])
    
    score = 0
    total = len(quiz_questions)
    results = []
    
    for q in quiz_questions:
        q_id = str(q['id'])
        user_selected = set(submitted_answers.get(q_id, []))
        correct_options = set(opt['text'] for opt in q['options'] if opt['is_correct'])
        
        # "If multiple answers from a card is chosen all must be recognized as correct"
        # We assume if the user selected EXACTLY the correct shown options, they get it right.
        # Or if we just require they select "any" correct option:
        # For strict check: user_selected == correct_options
        is_correct = len(user_selected) > 0 and user_selected.issubset(correct_options) and len(user_selected) == len(correct_options)
        
        if is_correct:
            score += 1
            
        results.append({
            'id': q_id,
            'is_correct': is_correct,
            'correct_answers': list(correct_options)
        })
        
    return jsonify({'success': True, 'score': score, 'total': total, 'results': results})

# Render the custom quiz editor.
def edit_quiz_route():
    if not _current_user():
        return _login_required_response()
    from app import get_user_custom_quizzes
    from models import Quiz
    user_id = _current_user_id()
    quizzes = get_user_custom_quizzes(user_id)
    
    selected_quiz_id = _int_value(request.args.get('quiz_id'))
    selected_quiz = None
    if selected_quiz_id:
        selected_quiz = Quiz.query.get(selected_quiz_id)
        if selected_quiz and selected_quiz.owned_by != user_id:
            selected_quiz = None
            
    return render_template('edit_quiz.html', quizzes=quizzes, selected_quiz=selected_quiz)

# Create a custom quiz.
def create_custom_quiz_route():
    if not _current_user():
        return _login_required_response()
    from app import create_custom_quiz
    data = _request_data()
    title = data.get('title')
    description = data.get('description')
    tags = data.get('tags')
    is_public = str(data.get('is_public', False)).lower() in ('1', 'true', 'yes', 'on')
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    quiz = create_custom_quiz(_current_user_id(), title, is_public, description, tags)
    return redirect(url_for('edit_quiz_route', quiz_id=quiz.quiz_id))

# Update custom quiz metadata.
def edit_custom_quiz_metadata_route():
    if not _current_user():
        return _login_required_response()
    from app import edit_custom_quiz
    data = _request_data()
    quiz_id = _int_value(data.get('quiz_id'))
    title = data.get('title')
    description = data.get('description')
    tags = data.get('tags')
    is_public = str(data.get('is_public', False)).lower() in ('1', 'true', 'yes', 'on')
    if not _owned_quiz(quiz_id, _current_user_id()):
        return jsonify({'error': 'You can only edit quizzes you own'}), 403
    edit_custom_quiz(quiz_id, title, is_public, description, tags)
    return redirect(url_for('edit_quiz_route', quiz_id=quiz_id))

# Delete a custom quiz.
def delete_custom_quiz_route():
    if not _current_user():
        return _login_required_response()
    from app import delete_custom_quiz
    quiz_id = _int_value(_request_data().get('quiz_id'))
    if not _owned_quiz(quiz_id, _current_user_id()):
        return jsonify({'error': 'You can only delete quizzes you own'}), 403
    delete_custom_quiz(quiz_id)
    return redirect(url_for('edit_quiz_route'))

# Add a question to a quiz.
def add_quiz_question_route():
    if not _current_user():
        return _login_required_response()
    from app import add_quiz_question

    data = _request_data()
    quiz_id = _int_value(data.get('quiz_id'))
    if not _owned_quiz(quiz_id, _current_user_id()):
        return jsonify({'error': 'You can only edit quizzes you own'}), 403
    question_text = data.get('question')
    q_type = data.get('q_type', 'dynamic')
    
    options_data = []
    correct_count = 0
    for i in range(1, 6):
        text = data.get(f'option_{i}', '').strip()
        if text:
            if q_type == 'dynamic':
                is_correct = True
                correct_count += 1
            else:
                is_correct = (data.get(f'is_correct_{i}') is not None)
                if is_correct:
                    correct_count += 1
            options_data.append({'text': text, 'is_correct': is_correct})
            
    if q_type == 'dynamic' and not (1 <= correct_count <= 2):
        return _redirect_with_fragment('edit_quiz_route', fragment='quiz-editor', quiz_id=quiz_id, notice='Dynamic questions must have 1-2 correct answers.', level='error')
        
    if q_type == 'static':
        if not (1 <= correct_count <= 2):
            return _redirect_with_fragment('edit_quiz_route', fragment='quiz-editor', quiz_id=quiz_id, notice='Static questions must have 1-2 correct answers.', level='error')
        if len(options_data) < 2:
            return _redirect_with_fragment('edit_quiz_route', fragment='quiz-editor', quiz_id=quiz_id, notice='Static questions must have at least 2 options.', level='error')

    add_quiz_question(quiz_id, question_text, q_type, options_data)
    return _redirect_with_fragment('edit_quiz_route', fragment='quiz-editor', quiz_id=quiz_id, notice='Question added successfully', level='success')

# Delete a quiz question.
def delete_quiz_question_route():
    if not _current_user():
        return _login_required_response()
    from app import delete_quiz_question
    from models import QuizQuestion
    data = _request_data()
    question_id = _int_value(data.get('question_id'))
    quiz_id = _int_value(data.get('quiz_id'))
    if not _owned_quiz(quiz_id, _current_user_id()):
        return jsonify({'error': 'You can only edit quizzes you own'}), 403
    question = QuizQuestion.query.get(question_id)
    if not question or question.quiz_id != quiz_id:
        return jsonify({'error': 'Question not found'}), 404
    delete_quiz_question(question_id)
    return _redirect_with_fragment('edit_quiz_route', fragment='quiz-editor', quiz_id=quiz_id, notice='Question deleted', level='success')

# Replace a quiz question.
def edit_quiz_question_route():
    if not _current_user():
        return _login_required_response()
    from app import delete_quiz_question, add_quiz_question
    from models import QuizQuestion
    data = _request_data()
    quiz_id = _int_value(data.get('quiz_id'))
    question_id = _int_value(data.get('question_id'))
    if not _owned_quiz(quiz_id, _current_user_id()):
        return jsonify({'error': 'You can only edit quizzes you own'}), 403
    question = QuizQuestion.query.get(question_id)
    if not question or question.quiz_id != quiz_id:
        return jsonify({'error': 'Question not found'}), 404
    question_text = data.get('question')
    q_type = data.get('q_type', 'dynamic')
    
    options_data = []
    correct_count = 0
    for i in range(1, 6):
        text = data.get(f'option_{i}', '').strip()
        if text:
            if q_type == 'dynamic':
                is_correct = True
                correct_count += 1
            else:
                is_correct = (data.get(f'is_correct_{i}') is not None)
                if is_correct:
                    correct_count += 1
            options_data.append({'text': text, 'is_correct': is_correct})
            
    if q_type == 'dynamic' and not (1 <= correct_count <= 2):
        return _redirect_with_fragment('edit_quiz_route', fragment='quiz-editor', quiz_id=quiz_id, notice='Dynamic questions must have 1-2 correct answers.', level='error')
        
    if q_type == 'static':
        if not (1 <= correct_count <= 2):
            return _redirect_with_fragment('edit_quiz_route', fragment='quiz-editor', quiz_id=quiz_id, notice='Static questions must have 1-2 correct answers.', level='error')
        if len(options_data) < 2:
            return _redirect_with_fragment('edit_quiz_route', fragment='quiz-editor', quiz_id=quiz_id, notice='Static questions must have at least 2 options.', level='error')

    delete_quiz_question(question_id)
    add_quiz_question(quiz_id, question_text, q_type, options_data)
    
    return _redirect_with_fragment('edit_quiz_route', fragment='quiz-editor', quiz_id=quiz_id, notice='Question updated', level='success')

# Route registration.
# Register every route on the Flask app.
def register_routes(app):
    app.before_request(_validate_csrf)

    @app.context_processor
    def inject_security_context():
        return {
            'current_user': _current_user(),
            'csrf_token': _csrf_token,
        }

    # Main pages
    app.add_url_rule('/', endpoint='index', view_func=index)
    app.add_url_rule('/register', endpoint='register', view_func=register, methods=['GET', 'POST'])
    app.add_url_rule('/login', endpoint='login', view_func=login, methods=['GET', 'POST'])
    app.add_url_rule('/logout', endpoint='logout', view_func=logout, methods=['POST'])
    app.add_url_rule('/account', endpoint='account', view_func=account, methods=['GET', 'POST'])
    app.add_url_rule('/admin/users', endpoint='admin_users', view_func=admin_users, methods=['GET', 'POST'])
    app.add_url_rule('/edit', endpoint='edit', view_func=edit)
    app.add_url_rule('/view', endpoint='view', view_func=view)
    app.add_url_rule('/match', endpoint='match', view_func=match)
    app.add_url_rule('/reorder', endpoint='reorder', view_func=reorder)
    app.add_url_rule('/search', endpoint='search', view_func=search_route)
    app.add_url_rule('/public_deck', endpoint='public_deck', view_func=public_deck_route, methods=['GET'])
    app.add_url_rule('/copy_public_deck', endpoint='copy_public_deck', view_func=copy_public_deck_route, methods=['POST'])
    app.add_url_rule('/public_quiz', endpoint='public_quiz', view_func=public_quiz_route, methods=['GET'])
    app.add_url_rule('/copy_public_quiz', endpoint='copy_public_quiz', view_func=copy_public_quiz_route, methods=['POST'])
    app.add_url_rule('/quiz', endpoint='quiz', view_func=quiz_route, methods=['GET'])
    app.add_url_rule('/edit_quiz', endpoint='edit_quiz_route', view_func=edit_quiz_route, methods=['GET'])
    
    # Custom Quiz operations
    app.add_url_rule('/create_custom_quiz', endpoint='create_custom_quiz', view_func=create_custom_quiz_route, methods=['POST'])
    app.add_url_rule('/edit_custom_quiz', endpoint='edit_custom_quiz', view_func=edit_custom_quiz_metadata_route, methods=['POST'])
    app.add_url_rule('/delete_custom_quiz', endpoint='delete_custom_quiz', view_func=delete_custom_quiz_route, methods=['POST'])
    app.add_url_rule('/add_quiz_question', endpoint='add_quiz_question', view_func=add_quiz_question_route, methods=['POST'])
    app.add_url_rule('/edit_quiz_question', endpoint='edit_quiz_question', view_func=edit_quiz_question_route, methods=['POST'])
    app.add_url_rule('/delete_quiz_question', endpoint='delete_quiz_question', view_func=delete_quiz_question_route, methods=['POST'])
    app.add_url_rule('/score_quiz', endpoint='score_quiz', view_func=score_quiz_route, methods=['POST'])

    # Deck operations
    app.add_url_rule('/create_deck', endpoint='create_deck', view_func=create_deck_route, methods=['POST'])
    app.add_url_rule('/import_deck', endpoint='import_deck', view_func=import_deck_route, methods=['POST'])
    app.add_url_rule('/get_decks', endpoint='get_decks', view_func=get_deck_list_route, methods=['POST'])
    app.add_url_rule('/delete_deck', endpoint='delete_deck', view_func=delete_deck_route, methods=['POST'])
    app.add_url_rule('/edit_deck', endpoint='edit_deck', view_func=edit_deck_route, methods=['POST'])

    # Card operations
    app.add_url_rule('/add_card', endpoint='add_card', view_func=add_card_route, methods=['POST'])
    app.add_url_rule('/delete_card', endpoint='delete_card', view_func=delete_card_route, methods=['POST'])
    app.add_url_rule('/match_answer', endpoint='match_answer', view_func=match_answer_route, methods=['POST'])
    app.add_url_rule('/delete_answer', endpoint='delete_answer', view_func=delete_answer_route, methods=['POST'])
    app.add_url_rule('/list_cards', endpoint='list_cards', view_func=list_cards_route, methods=['POST'])
    app.add_url_rule('/get_card', endpoint='get_card', view_func=get_card_route, methods=['POST'])
    app.add_url_rule('/edit_card', endpoint='edit_card', view_func=edit_card_route, methods=['POST'])
    app.add_url_rule('/move_card', endpoint='move_card', view_func=move_card_route, methods=['POST'])
    app.add_url_rule('/swap_cards', endpoint='swap_cards', view_func=swap_cards_route, methods=['POST'])
    app.add_url_rule('/check_reorder', endpoint='check_reorder', view_func=check_reorder_route, methods=['POST'])
