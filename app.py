from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from sqlalchemy import func
from routes import register_routes

app = Flask(__name__, instance_relative_config=True)

# Secret key for session management (change this to a random string in production)
app.config['SECRET_KEY'] = 'temp_secret_key'

# SQLAlchemy Configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///cards.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)


# Database Models
class FlashCard(db.Model):
    FCid = db.Column(db.Integer, primary_key=True)
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=func.current_timestamp())


# Register all application routes.
register_routes(app)


if __name__ == '__main__':
    app.run(debug=True)
