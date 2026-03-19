from flask import Flask
from flask_migrate import Migrate
from models import db, FlashCard
from routes import register_routes

app = Flask(__name__, instance_relative_config=True)

# Secret key for session management (change this to a random string in production)
app.config['SECRET_KEY'] = 'temp_secret_key'

# SQLAlchemy Configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///cards.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
migrate = Migrate(app, db)



# Register all application routes.
register_routes(app)


if __name__ == '__main__':
    app.run(debug=True)
