from flask import jsonify, redirect, render_template, request, session, url_for


# Display the home page
def index():
    return render_template('index.html')


# Display the deck and card management page
def edit():
    userId = 1  # Default user for now
    return render_template('edit.html', userId=userId)


# Display the study page for learning cards
def view():
    userId = 1  # Default user for now
    return render_template('view.html', userId=userId)


## Deck route handlers

# Create a new deck
def createDeckRoute():
    from app import createDeck
    
    data = request.get_json()
    userId = data.get('userId')
    description = data.get('description')
    sortable = data.get('sortable', False)
    
    if not userId or not description:
        return jsonify({'error': 'User ID and description are required'}), 400
    
    deck = createDeck(userId, description, sortable)
    return jsonify({'success': True, 'deckID': deck.deckID, 'description': deck.description})


# Get all decks for a user
def getDeckListRoute():
    from app import getUserDecks
    
    data = request.get_json()
    userId = data.get('userId')
    
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
    
    data = request.get_json()
    deckId = data.get('deckId')
    
    if not deckId:
        return jsonify({'error': 'Deck ID is required'}), 400
    
    deleted = deleteDeck(deckId)
    if deleted:
        return jsonify({'success': True, 'deckId': deckId})
    else:
        return jsonify({'error': 'Deck not found'}), 404


# Edit a deck
def editDeckRoute():
    from app import editDeck
    
    data = request.get_json()
    deckId = data.get('deckId')
    description = data.get('description')
    sortable = data.get('sortable', False)
    
    if not deckId or not description:
        return jsonify({'error': 'Deck ID and description are required'}), 400
    
    deck = editDeck(deckId, description, sortable)
    if deck:
        return jsonify({'success': True, 'deckID': deck.deckID})
    else:
        return jsonify({'error': 'Deck not found'}), 404


## Card route handlers

# Add a new card to a deck
def addCardRoute():
    from app import addCard
    
    data = request.get_json()
    deckId = data.get('deckId')
    question = data.get('question')
    answers = data.get('answers')
    
    if not deckId or not question or not answers:
        return jsonify({'error': 'Deck ID, question, and answers are required'}), 400
    
    card = addCard(deckId, question, answers)
    return jsonify({'success': True, 'cardID': card.cardID})


# Delete a card
def deleteCardRoute():
    from app import deleteCard
    
    data = request.get_json()
    cardId = data.get('cardId')
    
    if not cardId:
        return jsonify({'error': 'Card ID is required'}), 400
    
    deleted = deleteCard(cardId)
    if deleted:
        return jsonify({'success': True, 'cardId': cardId})
    else:
        return jsonify({'error': 'Card not found'}), 404


# Edit a card
def editCardRoute():
    from app import editCard
    
    data = request.get_json()
    cardId = data.get('cardId')
    question = data.get('question')
    answers = data.get('answers')
    
    if not cardId or not question or not answers:
        return jsonify({'error': 'Card ID, question, and answers are required'}), 400
    
    card = editCard(cardId, question, answers)
    if card:
        return jsonify({'success': True, 'cardID': card.cardID})
    else:
        return jsonify({'error': 'Card not found'}), 404


# Get all cards from a deck
def listCardsRoute():
    from app import listCardsFromDeck
    
    data = request.get_json()
    deckId = data.get('deckId')
    
    if not deckId:
        return jsonify({'error': 'Deck ID is required'}), 400
    
    cards = listCardsFromDeck(deckId)
    if cards is not None:
        return jsonify({'success': True, 'cards': cards})
    else:
        return jsonify({'success': True, 'cards': []})


# Get a single card with all answers
def getCardRoute():
    from app import getCardFromDeck
    
    data = request.get_json()
    cardId = data.get('cardId')
    
    if not cardId:
        return jsonify({'error': 'Card ID is required'}), 400
    
    card = getCardFromDeck(cardId)
    if card:
        return jsonify({'success': True, 'card': card})
    else:
        return jsonify({'error': 'Card not found'}), 404


# Register all routes with Flask
def registerRoutes(app):
    # Main pages
    app.add_url_rule('/', endpoint='index', view_func=index)
    app.add_url_rule('/edit', endpoint='edit', view_func=edit)
    app.add_url_rule('/view', endpoint='view', view_func=view)
    
    # Deck operations
    app.add_url_rule('/create_deck', endpoint='createDeck', view_func=createDeckRoute, methods=['POST'])
    app.add_url_rule('/get_decks', endpoint='getDecks', view_func=getDeckListRoute, methods=['POST'])
    app.add_url_rule('/delete_deck', endpoint='deleteDeck', view_func=deleteDeckRoute, methods=['POST'])
    app.add_url_rule('/edit_deck', endpoint='editDeck', view_func=editDeckRoute, methods=['POST'])
    
    # Card operations
    app.add_url_rule('/add_card', endpoint='addCard', view_func=addCardRoute, methods=['POST'])
    app.add_url_rule('/delete_card', endpoint='deleteCard', view_func=deleteCardRoute, methods=['POST'])
    app.add_url_rule('/list_cards', endpoint='listCards', view_func=listCardsRoute, methods=['POST'])
    app.add_url_rule('/get_card', endpoint='getCard', view_func=getCardRoute, methods=['POST'])
    app.add_url_rule('/edit_card', endpoint='editCard', view_func=editCardRoute, methods=['POST'])
