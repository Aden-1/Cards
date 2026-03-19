from flask import jsonify, redirect, render_template, request, session, url_for


# Render the home page.
def index():
    return render_template('index.html')


# Handles the HTTP requests for flashcard operations.

# Handle POST request to add a new flashcard from the form.
def addCard():
    # Import the database function for this route.
    from app import addFlashcard
    
    # Extract JSON data from the HTTP request body.
    data = request.get_json()
    question = data.get('question')
    answer = data.get('answer')
    category = data.get('category')
    
    # Validate that both required fields are present.
    if not question or not answer:
        return jsonify({'error': 'Question and answer are required'}), 400
    
    # Call the database function to save the card.
    card = addFlashcard(question, answer, category)
    # Return a JSON response with the newly created card's ID.
    return jsonify({'success': True, 'cardId': card.FCid})


# Handle POST request to delete a flashcard by ID.
def deleteCard():
    # Import the database function for this route.
    from app import deleteFlashcard
    
    # Extract JSON data from the HTTP request body.
    data = request.get_json()
    cardId = data.get('cardId')
    
    # Validate that the card ID is provided.
    if not cardId:
        return jsonify({'error': 'Card ID is required'}), 400
    
    # Call the database function to delete the card.
    deleted = deleteFlashcard(cardId)
    
    # Return appropriate response based on deletion success.
    if deleted:
        return jsonify({'success': True, 'cardId': cardId})
    else:
        return jsonify({'error': 'Card not found'}), 404


# Handle POST request to retrieve flashcards by category.
def listCard():
    # Import the database function for this route.
    from app import listFlashcard
    
    # Extract JSON data from the HTTP request body.
    data = request.get_json()
    cardCat = data.get('cardCat')

    # Validate that the category is provided.
    if not cardCat:
        return jsonify({'error': 'Card category is required'}), 400

    # Call the database function to retrieve cards by category.
    listed = listFlashcard(cardCat)

    # Format and return the results as JSON.
    if listed:
        cardsData = [{'id': card.FCid, 'question': card.question, 'answer': card.answer, 'category': card.category} for card in listed]
        return jsonify({'success': True, 'cards': cardsData})
    else:
        return jsonify({'error': 'No cards found in this category'}), 404


# Register all route handlers with endpoint names for url_for() calls.
def registerRoutes(app):
    app.add_url_rule('/', endpoint='index', view_func=index)
    app.add_url_rule('/add_card', endpoint='addCard', view_func=addCard, methods=['POST'])
    app.add_url_rule('/delete_card', endpoint='deleteCard', view_func=deleteCard, methods=['POST'])
    app.add_url_rule('/list_card', endpoint='listCard', view_func=listCard, methods=['POST'])
