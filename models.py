from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func

# Initialize SQLAlchemy
db = SQLAlchemy()


# Database model for flashcards.
class FlashCard(db.Model):
    FCid = db.Column(db.Integer, primary_key=True, autoincrement=True)
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=func.current_timestamp())

