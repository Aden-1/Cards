from flask import jsonify, redirect, render_template, request, url_for


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


# Page routes.
# Render the home page.
def index():
    return render_template('index.html')


# Deck editor.
# Render the deck editor page.
def edit():
    user_id = 1  # Local demo user.
    from app import get_user_decks, get_deck_details

    decks = get_user_decks(user_id)
    deck_data = [{
        'deck_id': deck.deck_id,
        'description': deck.description,
        'detailed_description': deck.detailed_description,
        'tags': deck.tags,
        'sortable': deck.sortable,
        'is_public': deck.is_public,
        'card_count': len(deck.cards),
    } for deck in decks]

    selected_deck_id = _int_value(request.args.get('deck_id'))
    selected_deck = None
    selected_cards = []
    if selected_deck_id:
        selected_deck = get_deck_details(selected_deck_id, shuffle_cards=False, shuffle_answers=False)
        if selected_deck and selected_deck['deck_id']:
            selected_cards = selected_deck['cards']
        else:
            selected_deck = None

    return render_template('edit.html', user_id=user_id, decks=deck_data, selected_deck=selected_deck, selected_cards=selected_cards, selected_deck_id=selected_deck_id)


# Study view.
# Render the study page.
def view():
    user_id = 1  # Local demo user.
    from app import get_accessible_decks, get_deck_details

    decks = get_accessible_decks(user_id)
    deck_data = [{
        'deck_id': deck.deck_id,
        'description': deck.description,
        'detailed_description': deck.detailed_description,
        'tags': deck.tags,
        'sortable': deck.sortable,
        'is_public': deck.is_public,
        'card_count': len(deck.cards),
    } for deck in decks]

    selected_deck_id = _int_value(request.args.get('deck_id'))
    study_deck = get_deck_details(selected_deck_id, shuffle_cards=False, shuffle_answers=False) if selected_deck_id else None

    return render_template('view.html', user_id=user_id, decks=deck_data, study_deck=study_deck, selected_deck_id=selected_deck_id)


# Matching game.
# Render the matching game page.
def match():
    user_id = 1  # Local demo user.
    from app import get_accessible_decks, get_deck_study_data

    decks = get_accessible_decks(user_id)
    deck_data = [{
        'deck_id': deck.deck_id,
        'description': deck.description,
        'detailed_description': deck.detailed_description,
        'tags': deck.tags,
        'sortable': deck.sortable,
        'is_public': deck.is_public,
        'card_count': len(deck.cards),
    } for deck in decks]

    selected_deck_id = _int_value(request.args.get('deck_id'))
    selected_question_id = _int_value(request.args.get('selected_question'))
    error_message = request.args.get('error')
    match_deck = get_deck_study_data(selected_deck_id, shuffle=True) if selected_deck_id else None

    return render_template(
        'match.html',
        user_id=user_id,
        decks=deck_data,
        match_deck=match_deck,
        selected_deck_id=selected_deck_id,
        selected_question_id=selected_question_id,
        error_message=error_message,
    )


# Render the reorder game page.
def reorder():
    user_id = 1  # Local demo user.
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
        'card_count': len(deck.cards),
    } for deck in sortable_decks]

    selected_deck_id = _int_value(request.args.get('deck_id'))
    sortable_deck_ids = {deck['deck_id'] for deck in deck_data}
    if selected_deck_id not in sortable_deck_ids:
        selected_deck_id = None

    # Start each round with a shuffled card list.
    reorder_deck = get_deck_details(selected_deck_id, shuffle_cards=True, shuffle_answers=False) if selected_deck_id else None

    return render_template(
        'reorder.html',
        user_id=user_id,
        decks=deck_data,
        reorder_deck=reorder_deck,
        selected_deck_id=selected_deck_id,
    )


# Deck routes.

# Handle deck creation.
def create_deck_route():
    from app import create_deck

    data = _request_data()
    user_id = _int_value(data.get('user_id'))
    description = data.get('description')
    detailed_description = data.get('detailed_description')
    tags = data.get('tags')
    sortable = str(data.get('sortable', False)).lower() in ('1', 'true', 'yes', 'on')
    is_public = str(data.get('is_public', False)).lower() in ('1', 'true', 'yes', 'on')

    if not user_id or not description:
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
    from app import get_user_decks

    data = _request_data()
    user_id = _int_value(data.get('user_id'))

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
    from app import delete_deck

    data = _request_data()
    deck_id = _int_value(data.get('deck_id'))

    if not deck_id:
        return jsonify({'error': 'Deck ID is required'}), 400
    
    deleted = delete_deck(deck_id)
    if deleted:
        if request.is_json:
            return jsonify({'success': True, 'deck_id': deck_id})
        return _redirect_with_fragment('edit', fragment='decks-section', notice='Deck deleted', level='success')
    else:
        return jsonify({'error': 'Deck not found'}), 404


# Update deck settings.
def edit_deck_route():
    from app import edit_deck

    data = _request_data()
    deck_id = _int_value(data.get('deck_id'))
    description = data.get('description')
    detailed_description = data.get('detailed_description')
    tags = data.get('tags')
    sortable = str(data.get('sortable', False)).lower() in ('1', 'true', 'yes', 'on')
    is_public = str(data.get('is_public', False)).lower() in ('1', 'true', 'yes', 'on')

    if not deck_id or not description:
        return jsonify({'error': 'Deck ID and description are required'}), 400
    
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
    from app import add_card

    data = _request_data()
    deck_id = _int_value(data.get('deck_id'))
    question = data.get('question')
    answers = data.get('answers')
    
    if not deck_id or not question or not answers:
        return jsonify({'error': 'Deck ID, question, and answers are required'}), 400
    try:
        card = add_card(deck_id, question, answers)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    if request.is_json:
        return jsonify({'success': True, 'card_id': card.card_id})
    return _redirect_with_fragment('edit', deck_id=deck_id, fragment='deck-editor', notice='Card added', level='success')


# Delete a card.
def delete_card_route():
    from app import delete_card

    data = _request_data()
    card_id = _int_value(data.get('card_id'))
    deck_id = _int_value(data.get('deck_id'))

    if not card_id:
        return jsonify({'error': 'Card ID is required'}), 400
    
    deleted = delete_card(card_id)
    if deleted:
        if request.is_json:
            return jsonify({'success': True, 'card_id': card_id})
        return _redirect_with_fragment('edit', deck_id=deck_id, fragment='deck-editor', notice='Card deleted', level='success') if deck_id else _redirect_with_fragment('edit', fragment='decks-section', notice='Card deleted', level='success')
    else:
        return jsonify({'error': 'Card not found'}), 404


# Update a card and its answers.
def edit_card_route():
    from app import edit_card

    data = _request_data()
    card_id = _int_value(data.get('card_id'))
    deck_id = _int_value(data.get('deck_id'))
    question = data.get('question')
    answers = data.get('answers')
    
    if not card_id or not question:
        return jsonify({'error': 'Card ID and question are required'}), 400
    
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

    data = _request_data()
    deck_id = _int_value(data.get('deck_id'))
    shuffle = str(data.get('shuffle', False)).lower() in ('1', 'true', 'yes', 'on')
    detailed = str(data.get('detailed', False)).lower() in ('1', 'true', 'yes', 'on')

    if not deck_id:
        return jsonify({'error': 'Deck ID is required'}), 400
    
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

    data = _request_data()
    card_id = _int_value(data.get('card_id'))

    if not card_id:
        return jsonify({'error': 'Card ID is required'}), 400
    
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
    from app import delete_answer
    from models import CardAnswer

    data = _request_data()
    answer_id = _int_value(data.get('answer_id'))
    deck_id = _int_value(data.get('deck_id'))
    selected_question_id = _int_value(data.get('selected_question_id'))
    context = data.get('context')

    if not answer_id:
        return jsonify({'error': 'Answer ID is required'}), 400

    answer = CardAnswer.query.get(answer_id)
    if not answer:
        return jsonify({'error': 'Answer not found'}), 404

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
    from app import move_card_in_deck

    data = _request_data()
    card_id = _int_value(data.get('card_id'))
    deck_id = _int_value(data.get('deck_id'))
    direction = str(data.get('direction', '')).lower()

    if not card_id or direction not in ('up', 'down'):
        return jsonify({'error': 'Card ID and valid direction are required'}), 400

    result = move_card_in_deck(card_id, direction)
    if not result.get('success'):
        return jsonify({'error': result.get('error', 'Unable to move card')}), 400

    if request.is_json:
        return jsonify({'success': True, **result})
    return _redirect_with_fragment('edit', deck_id=result.get('deck_id') or deck_id, fragment='deck-editor')


# Swap two cards in a sortable deck.
def swap_cards_route():
    from app import swap_cards_in_deck

    payload = request.get_json(silent=True) or {}
    card_id = _int_value(payload.get('card_id'))
    target_card_id = _int_value(payload.get('target_card_id'))

    if not card_id or not target_card_id:
        return jsonify({'error': 'Both card IDs are required'}), 400

    result = swap_cards_in_deck(card_id, target_card_id)
    if not result.get('success'):
        return jsonify({'error': result.get('error', 'Unable to swap cards')}), 400

    return jsonify({'success': True, **result})


# Check a submitted reorder attempt.
def check_reorder_route():
    from app import check_deck_order

    payload = request.get_json(silent=True) or {}
    deck_id = _int_value(payload.get('deck_id'))
    ordered_card_ids = payload.get('ordered_card_ids')

    if not deck_id:
        return jsonify({'error': 'Deck ID is required'}), 400

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
    results = search_public_content(query) if query else {
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


# Render the quiz launcher and quiz data.
def quiz_route():
    from app import get_accessible_decks, get_accessible_custom_quizzes, generate_quiz_data
    user_id = 1
    decks = get_accessible_decks(user_id)
    deck_data = [{
        'deck_id': deck.deck_id,
        'description': deck.description,
        'card_count': len(deck.cards),
    } for deck in decks]
    
    custom_quizzes = get_accessible_custom_quizzes(user_id)

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
    from app import get_user_custom_quizzes
    from models import Quiz
    user_id = 1
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
    from app import create_custom_quiz
    data = _request_data()
    title = data.get('title')
    description = data.get('description')
    tags = data.get('tags')
    is_public = str(data.get('is_public', False)).lower() in ('1', 'true', 'yes', 'on')
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    quiz = create_custom_quiz(1, title, is_public, description, tags)  # user_id = 1
    return redirect(url_for('edit_quiz_route', quiz_id=quiz.quiz_id))

# Update custom quiz metadata.
def edit_custom_quiz_metadata_route():
    from app import edit_custom_quiz
    data = _request_data()
    quiz_id = _int_value(data.get('quiz_id'))
    title = data.get('title')
    description = data.get('description')
    tags = data.get('tags')
    is_public = str(data.get('is_public', False)).lower() in ('1', 'true', 'yes', 'on')
    edit_custom_quiz(quiz_id, title, is_public, description, tags)
    return redirect(url_for('edit_quiz_route', quiz_id=quiz_id))

# Delete a custom quiz.
def delete_custom_quiz_route():
    from app import delete_custom_quiz
    quiz_id = _int_value(_request_data().get('quiz_id'))
    delete_custom_quiz(quiz_id)
    return redirect(url_for('edit_quiz_route'))

# Add a question to a quiz.
def add_quiz_question_route():
    from app import add_quiz_question

    data = _request_data()
    quiz_id = _int_value(data.get('quiz_id'))
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
    from app import delete_quiz_question
    data = _request_data()
    question_id = _int_value(data.get('question_id'))
    quiz_id = _int_value(data.get('quiz_id'))
    delete_quiz_question(question_id)
    return _redirect_with_fragment('edit_quiz_route', fragment='quiz-editor', quiz_id=quiz_id, notice='Question deleted', level='success')

# Replace a quiz question.
def edit_quiz_question_route():
    from app import delete_quiz_question, add_quiz_question
    data = _request_data()
    quiz_id = _int_value(data.get('quiz_id'))
    question_id = _int_value(data.get('question_id'))
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
    # Main pages
    app.add_url_rule('/', endpoint='index', view_func=index)
    app.add_url_rule('/edit', endpoint='edit', view_func=edit)
    app.add_url_rule('/view', endpoint='view', view_func=view)
    app.add_url_rule('/match', endpoint='match', view_func=match)
    app.add_url_rule('/reorder', endpoint='reorder', view_func=reorder)
    app.add_url_rule('/search', endpoint='search', view_func=search_route)
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
