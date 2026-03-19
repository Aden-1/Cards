from flask import Flask
from flask_migrate import Migrate
from models import db, FlashCard
from routes import registerRoutes

app = Flask(__name__, instance_relative_config=True)

# Secret key for session management (change this to a random string in production)
app.config['SECRET_KEY'] = 'temp_secret_key'

# SQLAlchemy Configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///cards.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
migrate = Migrate(app, db)


## Handle database operations for flashcards.

# Add a new flashcard to the database.
def addFlashcard(question, answer, category=None):
    card = FlashCard(question=question, answer=answer, category=category)
    db.session.add(card)
    db.session.commit()
    return card


# Delete a flashcard from the database by ID.
def deleteFlashcard(cardId):
    card = FlashCard.query.get(cardId)
    if card:
        db.session.delete(card)
        db.session.commit()
        return True
    return False


# Get all flashcards by category.
def listFlashcard(cardCat):
    # Convert string "None" to Python None for proper database querying.
    if cardCat.lower() == "none":
        cardCat = None
    cards = FlashCard.query.filter_by(category=cardCat).all()
    return cards


# Register all application routes.
registerRoutes(app)


if __name__ == '__app__':
    app.run(debug=True)
