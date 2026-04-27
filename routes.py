from flask import jsonify, redirect, render_template, request, url_for


def _request_data():
    return request.get_json(silent=True) or request.values.to_dict(flat=True)


def _int_value(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# Display the home page
def index():
    return render_template('index.html')


# Display the deck and card management page
def edit():
    userId = 1  # Default user for now
    from app import getUserDecks, getDeckDetails

    decks = getUserDecks(userId)
    deckData = [{
        'deckID': deck.deckID,
        'description': deck.description,
        'sortable': deck.sortable,
        'cardCount': len(deck.cards),
    } for deck in decks]

    selectedDeckId = _int_value(request.args.get('deck_id'))
    selectedDeck = None
    selectedCards = []
    if selectedDeckId:
        selectedDeck = getDeckDetails(selectedDeckId, shuffle_cards=False, shuffle_answers=False)
        if selectedDeck and selectedDeck['deckID']:
            selectedCards = selectedDeck['cards']
        else:
            selectedDeck = None

    return render_template('edit.html', userId=userId, decks=deckData, selectedDeck=selectedDeck, selectedCards=selectedCards, selectedDeckId=selectedDeckId)


# Display the study page for learning cards
def view():
    userId = 1  # Default user for now
    from app import getUserDecks, getDeckDetails

    decks = getUserDecks(userId)
    deckData = [{
        'deckID': deck.deckID,
        'description': deck.description,
        'sortable': deck.sortable,
        'cardCount': len(deck.cards),
    } for deck in decks]

    selectedDeckId = _int_value(request.args.get('deck_id'))
    studyDeck = getDeckDetails(selectedDeckId, shuffle_cards=False, shuffle_answers=False) if selectedDeckId else None

    return render_template('view.html', userId=userId, decks=deckData, studyDeck=studyDeck, selectedDeckId=selectedDeckId)


# Display the matching game page
def match():
    userId = 1  # Default user for now
    from app import getUserDecks, getDeckStudyData

    decks = getUserDecks(userId)
    deckData = [{
        'deckID': deck.deckID,
        'description': deck.description,
        'sortable': deck.sortable,
        'cardCount': len(deck.cards),
    } for deck in decks]

    selectedDeckId = _int_value(request.args.get('deck_id'))
    selectedQuestionId = _int_value(request.args.get('selected_question'))
    errorMessage = request.args.get('error')
    matchDeck = getDeckStudyData(selectedDeckId, shuffle=True) if selectedDeckId else None

    return render_template(
        'match.html',
        userId=userId,
        decks=deckData,
        matchDeck=matchDeck,
        selectedDeckId=selectedDeckId,
        selectedQuestionId=selectedQuestionId,
        errorMessage=errorMessage,
    )


## Deck route handlers

# Create a new deck
def createDeckRoute():
    from app import createDeck
    
    data = _request_data()
    userId = _int_value(data.get('userId'))
    description = data.get('description')
    sortable = str(data.get('sortable', False)).lower() in ('1', 'true', 'yes', 'on')

    if not userId or not description:
        return jsonify({'error': 'User ID and description are required'}), 400
    
    deck = createDeck(userId, description, sortable)
    if request.is_json:
        return jsonify({'success': True, 'deckID': deck.deckID, 'description': deck.description})
    return redirect(url_for('edit', deck_id=deck.deckID))


# Get all decks for a user
def getDeckListRoute():
    from app import getUserDecks
    
    data = _request_data()
    userId = _int_value(data.get('userId'))

    if not userId:
        return jsonify({'error': 'User ID is required'}), 400
    
    decks = getUserDecks(userId)
    if decks:
        decksData = [{'deckID': d.deckID, 'description': d.description, 'sortable': d.sortable, 'cardCount': len(d.cards)} for d in decks]
        return jsonify({'success': True, 'decks': decksData})
    else:
        return jsonify({'success': True, 'decks': []})


# Delete a deck
def deleteDeckRoute():
    from app import deleteDeck
    
    data = _request_data()
    deckId = _int_value(data.get('deckId'))

    if not deckId:
        return jsonify({'error': 'Deck ID is required'}), 400
    
    deleted = deleteDeck(deckId)
    if deleted:
        if request.is_json:
            return jsonify({'success': True, 'deckId': deckId})
        return redirect(url_for('edit'))
    else:
        return jsonify({'error': 'Deck not found'}), 404


# Edit a deck
def editDeckRoute():
    from app import editDeck
    
    data = _request_data()
    deckId = _int_value(data.get('deckId'))
    description = data.get('description')
    sortable = str(data.get('sortable', False)).lower() in ('1', 'true', 'yes', 'on')

    if not deckId or not description:
        return jsonify({'error': 'Deck ID and description are required'}), 400
    
    deck = editDeck(deckId, description, sortable)
    if deck:
        if request.is_json:
            return jsonify({'success': True, 'deckID': deck.deckID})
        return redirect(url_for('edit', deck_id=deck.deckID))
    else:
        return jsonify({'error': 'Deck not found'}), 404


## Card route handlers

# Add a new card to a deck
def addCardRoute():
    from app import addCard
    
    data = _request_data()
    deckId = _int_value(data.get('deckId'))
    question = data.get('question')
    answers = data.get('answers')
    
    if not deckId or not question or not answers:
        return jsonify({'error': 'Deck ID, question, and answers are required'}), 400
    try:
        card = addCard(deckId, question, answers)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    if request.is_json:
        return jsonify({'success': True, 'cardID': card.cardID})
    return redirect(url_for('edit', deck_id=deckId))


# Delete a card
def deleteCardRoute():
    from app import deleteCard
    
    data = _request_data()
    cardId = _int_value(data.get('cardId'))
    deckId = _int_value(data.get('deckId'))

    if not cardId:
        return jsonify({'error': 'Card ID is required'}), 400
    
    deleted = deleteCard(cardId)
    if deleted:
        if request.is_json:
            return jsonify({'success': True, 'cardId': cardId})
        return redirect(url_for('edit', deck_id=deckId) if deckId else url_for('edit'))
    else:
        return jsonify({'error': 'Card not found'}), 404


# Edit a card
def editCardRoute():
    from app import editCard
    
    data = _request_data()
    cardId = _int_value(data.get('cardId'))
    deckId = _int_value(data.get('deckId'))
    question = data.get('question')
    answers = data.get('answers')
    
    if not cardId or not question:
        return jsonify({'error': 'Card ID and question are required'}), 400
    
    card = editCard(cardId, question, answers)
    if card:
        if isinstance(card, dict) and card.get('deleted'):
            if request.is_json:
                return jsonify({'success': True, 'cardID': cardId, 'deleted': True})
            return redirect(url_for('edit', deck_id=card.get('deckID') or deckId))
        if request.is_json:
            return jsonify({'success': True, 'cardID': card.cardID})
        return redirect(url_for('edit', deck_id=deckId))
    else:
        return jsonify({'error': 'Card not found'}), 404


# Get all cards from a deck
def listCardsRoute():
    from app import listCardsFromDeck, getDeckDetails

    data = _request_data()
    deckId = _int_value(data.get('deckId'))
    shuffle = str(data.get('shuffle', False)).lower() in ('1', 'true', 'yes', 'on')
    detailed = str(data.get('detailed', False)).lower() in ('1', 'true', 'yes', 'on')

    if not deckId:
        return jsonify({'error': 'Deck ID is required'}), 400
    
    if detailed:
        deck = getDeckDetails(deckId, shuffle_cards=shuffle, shuffle_answers=shuffle)
        cards = deck['cards'] if deck else None
    else:
        cards = listCardsFromDeck(deckId, detailed=False, shuffle=shuffle)
    if cards is not None:
        return jsonify({'success': True, 'cards': cards})
    else:
        return jsonify({'success': True, 'cards': []})


# Get a single card with all answers
def getCardRoute():
    from app import getCardFromDeck
    
    data = _request_data()
    cardId = _int_value(data.get('cardId'))

    if not cardId:
        return jsonify({'error': 'Card ID is required'}), 400
    
    card = getCardFromDeck(cardId)
    if card:
        return jsonify({'success': True, 'card': card})
    else:
        return jsonify({'error': 'Card not found'}), 404


# Delete a single answer from a card
def deleteAnswerRoute():
    from app import deleteAnswer
    from models import CardAnswer

    data = _request_data()
    answerId = _int_value(data.get('answerId'))
    deckId = _int_value(data.get('deckId'))
    selectedQuestionId = _int_value(data.get('selectedQuestionId'))

    if not answerId:
        return jsonify({'error': 'Answer ID is required'}), 400

    answer = CardAnswer.query.get(answerId)
    if not answer:
        return jsonify({'error': 'Answer not found'}), 404

    if not selectedQuestionId:
        if request.is_json:
            return jsonify({'error': 'Select a question tile first'}), 400
        return redirect(url_for('match', deck_id=deckId or answer.card.deckID, error='Select a question tile first'))

    if answer.cardID != selectedQuestionId:
        if request.is_json:
            return jsonify({'error': 'That answer does not match the selected question'}), 400
        return redirect(url_for('match', deck_id=deckId or answer.card.deckID, selected_question=selectedQuestionId, error='That answer does not match the selected question'))

    deleted = deleteAnswer(answerId)
    if deleted:
        nextSelected = None if deleted.get('cardDeleted') else selectedQuestionId
        if request.is_json:
            return jsonify({'success': True, **deleted})
        return redirect(url_for('match', deck_id=deleted.get('deckID') or deckId, selected_question=nextSelected or ''))
    return jsonify({'error': 'Answer not found'}), 404


# Register all routes with Flask
def registerRoutes(app):
    # Main pages
    app.add_url_rule('/', endpoint='index', view_func=index)
    app.add_url_rule('/edit', endpoint='edit', view_func=edit)
    app.add_url_rule('/view', endpoint='view', view_func=view)
    app.add_url_rule('/match', endpoint='match', view_func=match)
    
    # Deck operations
    app.add_url_rule('/create_deck', endpoint='createDeck', view_func=createDeckRoute, methods=['POST'])
    app.add_url_rule('/get_decks', endpoint='getDecks', view_func=getDeckListRoute, methods=['POST'])
    app.add_url_rule('/delete_deck', endpoint='deleteDeck', view_func=deleteDeckRoute, methods=['POST'])
    app.add_url_rule('/edit_deck', endpoint='editDeck', view_func=editDeckRoute, methods=['POST'])
    
    # Card operations
    app.add_url_rule('/add_card', endpoint='addCard', view_func=addCardRoute, methods=['POST'])
    app.add_url_rule('/delete_card', endpoint='deleteCard', view_func=deleteCardRoute, methods=['POST'])
    app.add_url_rule('/delete_answer', endpoint='deleteAnswer', view_func=deleteAnswerRoute, methods=['POST'])
    app.add_url_rule('/list_cards', endpoint='listCards', view_func=listCardsRoute, methods=['POST'])
    app.add_url_rule('/get_card', endpoint='getCard', view_func=getCardRoute, methods=['POST'])
    app.add_url_rule('/edit_card', endpoint='editCard', view_func=editCardRoute, methods=['POST'])
