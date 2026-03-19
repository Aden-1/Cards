from flask import jsonify, redirect, render_template, request, session, url_for
from models import db, FlashCard


# Ensure click exists.
def ensure_click_count() -> None:
    if 'click_count' not in session:
        session['click_count'] = 0


# Render the home page.
def index():
    ensure_click_count()
    return render_template('index.html', clicks=session['click_count'])


# Handle an async increment action and return JSON response.
def increment():
    ensure_click_count()
    session['click_count'] += 1
    return jsonify({'clicks': session['click_count']})


# Handle an async reset action and return JSON response.
def resetCounter():
    ensure_click_count()
    session['click_count'] = 0
    return jsonify({'clicks': session['click_count']})


# Add a new flashcard to the database.
def add_flashcard(question, answer, category=None):
    card = FlashCard(question=question, answer=answer, category=category)
    db.session.add(card)
    db.session.commit()
    return card


# Delete a flashcard from the database by ID.
def delete_flashcard(card_id):
    card = FlashCard.query.get(card_id)
    if card:
        db.session.delete(card)
        db.session.commit()
        return True
    return False


# Register all route handlers with endpoint names for url_for() calls.
def register_routes(app):
    app.add_url_rule('/', endpoint='index', view_func=index)
    app.add_url_rule('/increment', endpoint='increment', view_func=increment, methods=['POST'])
    app.add_url_rule('/resetCounter', endpoint='resetCounter', view_func=resetCounter, methods=['POST'])
