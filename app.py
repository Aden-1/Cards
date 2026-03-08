from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from sqlalchemy import func

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


# Routes
@app.route('/')
def index():
    # Initialize click count for this user's session if it doesn't exist
    if 'click_count' not in session:
        session['click_count'] = 0

    return render_template('index.html', clicks=session['click_count'])


@app.route('/increment', methods=['POST'])
def increment():
    # Initialize if not exists
    if 'click_count' not in session:
        session['click_count'] = 0

    # Increment this user's session counter
    session['click_count'] += 1

    # Return JSON response for AJAX request
    return jsonify({'clicks': session['click_count']})

@app.route('/resetCounter', methods=['POST'])
def resetCounter():
    # Initialize if not exists
    if 'click_count' not in session:
        session['click_count'] = 0

    # Reset this user's session counter
    session['click_count'] = 0

    # Return JSON response for AJAX request
    return jsonify({'clicks': session['click_count']})


if __name__ == '__main__':
    app.run(debug=True)
