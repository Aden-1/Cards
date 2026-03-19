# Cards App

A Flask/Python application that uses flashcards to help you learn using unique and effective methods.

## What It Does (In progress)

- Create and manage flashcards for any subject
- Study flashcards with interactive learning tools
- Track your progress and learning patterns
- Personalized learning experience optimized for retention

## Tech Stack

- **Backend**: Flask
- **Database**: SQLite with SQLAlchemy ORM
- **Frontend**: HTML, Jinja2 Templates, Bootstrap

## Installation

### Prerequisites
- Python 3.8+
- pip (Python package manager)

### Website coming soon&trade;!

### Setup Local Instance

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd Cards
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv .venv
   ```

3. **Activate the virtual environment**
   - **Windows:**
     ```bash
     .venv\Scripts\activate
     ```
   - **macOS/Linux:**
     ```bash
     source .venv/bin/activate
     ```

4. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

5. **Initialize the database**
   ```bash
   flask db upgrade
   ```

6. **Run the application**
   ```bash
   python app.py
   ```



Visit `http://127.0.0.1:5000/` to start learning with flashcards!
