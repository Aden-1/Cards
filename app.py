import random
import math
import re
import unicodedata
from difflib import SequenceMatcher

from flask import Flask
from flask_migrate import Migrate
from models import db, User, Deck, Card, CardAnswer, Quiz, QuizQuestion, QuizOption
from routes import register_routes

app = Flask(__name__, instance_relative_config=True)

# Secret key for session management (change this in production)
app.config['SECRET_KEY'] = 'temp_secret_key'

# SQLAlchemy configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///cards.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
migrate = Migrate(app, db)


def _normalize_answers(answers):
    if answers is None:
        return []
    if isinstance(answers, str):
        answers = [part.strip() for part in answers.split(',')]
    return [answer.strip() for answer in answers if str(answer).strip()]


def _serialize_card(card, detailed=False):
    answer_objects = [{'answer_id': answer.answer_id, 'answer': answer.answer} for answer in card.answers]
    payload = {
        'card_id': card.card_id,
        'question': card.question,
        'answers': [answer['answer'] for answer in answer_objects],
        'position': card.position,
    }
    if detailed:
        payload['answer_objects'] = answer_objects
    return payload


def _serialize_deck(deck, detailed_cards=False, shuffle_cards=False, shuffle_answers=False):
    cards = list(deck.cards)
    cards.sort(key=lambda card: card.position)
    if shuffle_cards:
        random.shuffle(cards)

    serialized_cards = []
    flattened_answers = []
    for card in cards:
        serialized_card = _serialize_card(card, detailed=detailed_cards or shuffle_answers)
        if shuffle_answers:
            serialized_card['answer_objects'] = [
                {'answer_id': answer.answer_id, 'answer': answer.answer}
                for answer in card.answers
            ]
        serialized_cards.append(serialized_card)
        for answer in card.answers:
            flattened_answers.append({
                'answer_id': answer.answer_id,
                'answer': answer.answer,
                'card_id': card.card_id,
                'question': card.question,
            })

    if shuffle_answers:
        random.shuffle(flattened_answers)

    return {
        'deck_id': deck.deck_id,
        'description': deck.description,
        'detailed_description': deck.detailed_description,
        'tags': deck.tags,
        'sortable': deck.sortable,
        'is_public': deck.is_public,
        'card_count': len(cards),
        'answer_count': len(flattened_answers),
        'cards': serialized_cards,
        'answers': flattened_answers,
    }


## Custom Quiz database operations

def get_accessible_custom_quizzes(user_id):
    return Quiz.query.filter((Quiz.owned_by == user_id) | (Quiz.is_public == True)).all()

def get_user_custom_quizzes(user_id):
    return Quiz.query.filter_by(owned_by=user_id).all()

def create_custom_quiz(user_id, title, is_public=False, description=None, tags=None):
    quiz = Quiz(
        owned_by=user_id,
        title=title,
        is_public=is_public,
        description=description,
        tags=tags,
    )
    db.session.add(quiz)
    db.session.commit()
    return quiz

def edit_custom_quiz(quiz_id, title, is_public=False, description=None, tags=None):
    quiz = Quiz.query.get(quiz_id)
    if quiz:
        quiz.title = title
        quiz.is_public = is_public
        quiz.description = description
        quiz.tags = tags
        db.session.commit()
        return quiz
    return None

def delete_custom_quiz(quiz_id):
    quiz = Quiz.query.get(quiz_id)
    if quiz:
        db.session.delete(quiz)
        db.session.commit()
        return True
    return False

def add_quiz_question(quiz_id, question_text, q_type, options_data):
    q = QuizQuestion(quiz_id=quiz_id, question=question_text, type=q_type)
    db.session.add(q)
    db.session.flush()
    
    for opt in options_data:
        if opt['text'].strip():
            qo = QuizOption(question_id=q.question_id, text=opt['text'].strip(), is_correct=opt.get('is_correct', False))
            db.session.add(qo)
    db.session.commit()
    return q

def delete_quiz_question(question_id):
    q = QuizQuestion.query.get(question_id)
    if q:
        db.session.delete(q)
        db.session.commit()
        return True
    return False


## User database operations

# Create a new user
def create_user(username):
    user = User(username=username)
    db.session.add(user)
    db.session.commit()
    return user


# Get user by username
def get_user(username):
    return User.query.filter_by(username=username).first()


## Deck database operations

# Create a new deck for a user
def create_deck(user_id, description, sortable=False, is_public=False, detailed_description=None, tags=None):
    deck = Deck(
        owned_by=user_id,
        description=description,
        sortable=sortable,
        is_public=is_public,
        detailed_description=detailed_description,
        tags=tags
    )
    db.session.add(deck)
    db.session.commit()
    return deck


# Get all decks owned by a user
def get_user_decks(user_id):
    return Deck.query.filter_by(owned_by=user_id).all()


# Get accessible decks (owned by user or public)
def get_accessible_decks(user_id):
    return Deck.query.filter((Deck.owned_by == user_id) | (Deck.is_public == True)).all()


# Get a specific deck by ID
def get_deck(deck_id):
    return Deck.query.get(deck_id)


# Delete a deck and all its cards
def delete_deck(deck_id):
    deck = Deck.query.get(deck_id)
    if deck:
        db.session.delete(deck)
        db.session.commit()
        return True
    return False


# Edit a deck's description and sortable status
def edit_deck(deck_id, description, sortable=False, is_public=False, detailed_description=None, tags=None):
    deck = Deck.query.get(deck_id)
    if deck:
        deck.description = description
        deck.sortable = sortable
        deck.is_public = is_public
        deck.detailed_description = detailed_description
        deck.tags = tags
        db.session.commit()
        return deck
    return None


# Search public decks
def search_public_decks(query_text):
    if not query_text:
        return []

    # Simple search on description, detailed_description, and tags, case-insensitive (sqlite ilike)
    search_term = f"%{query_text}%"
    decks = Deck.query.filter(
        Deck.is_public == True,
        db.or_(
            Deck.description.ilike(search_term),
            Deck.detailed_description.ilike(search_term),
            Deck.tags.ilike(search_term)
        )
    ).all()
    return decks


def search_public_quizzes(query_text):
    if not query_text:
        return []

    search_term = f"%{query_text}%"
    quizzes = Quiz.query.filter(
        Quiz.is_public == True,
        db.or_(
            Quiz.title.ilike(search_term),
            Quiz.description.ilike(search_term),
            Quiz.tags.ilike(search_term)
        )
    ).all()
    return quizzes


SEARCH_STOP_WORDS = {
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from', 'how', 'in', 'into',
    'is', 'it', 'of', 'on', 'or', 'that', 'the', 'their', 'this', 'to', 'with', 'your'
}

SEARCH_SYNONYMS = {
    'learn': ['study', 'practice', 'beginner', 'intro', 'training'],
    'study': ['learn', 'review', 'practice', 'revise'],
    'practice': ['train', 'learn', 'study', 'drill'],
    'beginner': ['intro', 'starter', 'basic', 'fundamentals'],
    'intro': ['beginner', 'basic', 'starter', 'fundamentals'],
    'basic': ['beginner', 'intro', 'fundamental', 'starter'],
    'fundamental': ['basic', 'core', 'essential'],
    'fundamentals': ['basic', 'core', 'essential'],
    'spanish': ['espanol', 'español', 'language', 'castilian'],
    'espanol': ['spanish', 'language'],
    'español': ['spanish', 'language'],
    'language': ['vocabulary', 'grammar', 'linguistics'],
    'vocabulary': ['words', 'language', 'terms'],
    'history': ['historical', 'past', 'timeline'],
    'historical': ['history', 'past'],
    'biology': ['bio', 'anatomy', 'life', 'science'],
    'bio': ['biology', 'science'],
    'science': ['biology', 'chemistry', 'physics'],
    'coding': ['programming', 'development', 'software'],
    'programming': ['coding', 'software', 'development'],
    'python': ['programming', 'coding', 'software'],
    'math': ['mathematics', 'algebra', 'geometry', 'calculus'],
    'geography': ['maps', 'countries', 'regions'],
    'quiz': ['test', 'exam', 'assessment'],
    'test': ['quiz', 'exam', 'assessment'],
    'exam': ['test', 'quiz', 'assessment'],
}


def _normalize_search_text(text):
    text = (text or '').lower()
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _stem_token(token):
    if len(token) > 5 and token.endswith('ing'):
        return token[:-3]
    if len(token) > 4 and token.endswith('ed'):
        return token[:-2]
    if len(token) > 4 and token.endswith('es'):
        return token[:-2]
    if len(token) > 3 and token.endswith('s'):
        return token[:-1]
    return token


def _tokenize_search_text(text):
    normalized = _normalize_search_text(text)
    if not normalized:
        return []
    tokens = []
    for raw_token in normalized.split():
        token = _stem_token(raw_token)
        if token and token not in SEARCH_STOP_WORDS:
            tokens.append(token)
    return tokens


def _expand_search_tokens(tokens):
    expanded = set(tokens)
    for token in tokens:
        for synonym in SEARCH_SYNONYMS.get(token, []):
            normalized_synonym = _normalize_search_text(synonym)
            if not normalized_synonym:
                continue
            syn_parts = _tokenize_search_text(normalized_synonym)
            for part in syn_parts:
                expanded.add(part)
    return expanded


def _build_search_document(item_type, item):
    if item_type == 'deck':
        title = item.description or ''
        description = item.detailed_description or ''
        tags = item.tags or ''
        size_count = len(item.cards)
        item_id = item.deck_id
    else:
        title = item.title or ''
        description = item.description or ''
        tags = item.tags or ''
        size_count = len(item.questions)
        item_id = item.quiz_id

    return {
        'type': item_type,
        'id': item_id,
        'title': title,
        'description': description,
        'tags': tags,
        'title_norm': _normalize_search_text(title),
        'description_norm': _normalize_search_text(description),
        'tags_norm': _normalize_search_text(tags),
        'title_tokens': set(_tokenize_search_text(title)),
        'description_tokens': set(_tokenize_search_text(description)),
        'tags_tokens': set(_tokenize_search_text(tags)),
        'size_count': size_count,
    }


def _fuzzy_token_match(token, field_tokens):
    best = None
    best_ratio = 0.0
    for candidate in field_tokens:
        ratio = SequenceMatcher(None, token, candidate).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best = candidate
    if best and best_ratio >= 0.86:
        return best, best_ratio
    return None, 0.0


def _idf(token, corpus_docs):
    if not corpus_docs:
        return 1.0
    df = 0
    for doc in corpus_docs:
        all_tokens = doc['title_tokens'] | doc['tags_tokens'] | doc['description_tokens']
        if token in all_tokens:
            df += 1
    return math.log((len(corpus_docs) + 1) / (df + 1)) + 1.0


def _score_document(query_text, query_tokens, expanded_tokens, doc, corpus_docs):
    if not query_tokens:
        return {'score': 0.0, 'matched_tokens': set(), 'reasons': []}

    title_tokens = doc['title_tokens']
    tag_tokens = doc['tags_tokens']
    desc_tokens = doc['description_tokens']

    exact_query_set = set(query_tokens)
    expanded_only = set(expanded_tokens) - exact_query_set
    matched_exact = set()
    matched_expanded = set()
    matched_fuzzy = set()
    reasons = []
    score = 0.0

    field_specs = [
        ('title', title_tokens, 8.0, 4.0, 2.5),
        ('tags', tag_tokens, 6.0, 3.0, 2.0),
        ('description', desc_tokens, 3.0, 1.5, 1.0),
    ]

    for field_name, field_tokens, exact_weight, expanded_weight, fuzzy_weight in field_specs:
        for token in exact_query_set:
            if token in field_tokens:
                token_idf = _idf(token, corpus_docs)
                score += exact_weight * token_idf
                matched_exact.add(token)
                reasons.append(f"{field_name}: {token}")

        for token in expanded_only:
            if token in field_tokens:
                token_idf = _idf(token, corpus_docs)
                score += expanded_weight * token_idf
                matched_expanded.add(token)

        for token in exact_query_set:
            if token in field_tokens:
                continue
            fuzzy_token, ratio = _fuzzy_token_match(token, field_tokens)
            if fuzzy_token:
                token_idf = _idf(fuzzy_token, corpus_docs)
                score += fuzzy_weight * token_idf * ratio
                matched_fuzzy.add(token)

    normalized_query = _normalize_search_text(query_text)
    if normalized_query:
        if normalized_query in doc['title_norm']:
            score += 10.0
        if normalized_query in doc['tags_norm']:
            score += 7.0
        if normalized_query in doc['description_norm']:
            score += 4.0

    coverage = len(matched_exact | matched_fuzzy) / max(1, len(exact_query_set))
    score += coverage * 8.0

    popularity_bonus = min(3.0, math.log1p(doc['size_count']) * 0.8)
    score += popularity_bonus

    reason_parts = []
    if matched_exact:
        reason_parts.append("Exact: " + ", ".join(sorted(matched_exact)[:4]))
    if matched_expanded:
        reason_parts.append("Related: " + ", ".join(sorted(matched_expanded)[:4]))
    if matched_fuzzy:
        reason_parts.append("Close: " + ", ".join(sorted(matched_fuzzy)[:3]))

    return {
        'score': score,
        'matched_tokens': matched_exact | matched_expanded | matched_fuzzy,
        'reasons': reason_parts,
    }


def search_public_content(query_text, limit=50):
    query_text = (query_text or '').strip()
    if not query_text:
        return {'decks': [], 'quizzes': [], 'has_exact_match': False, 'query_tokens': [], 'expanded_tokens': []}

    query_tokens = _tokenize_search_text(query_text)
    expanded_tokens = _expand_search_tokens(query_tokens)

    public_decks = Deck.query.filter(Deck.is_public == True).all()
    public_quizzes = Quiz.query.filter(Quiz.is_public == True).all()

    docs = []
    docs.extend([_build_search_document('deck', deck) for deck in public_decks])
    docs.extend([_build_search_document('quiz', quiz) for quiz in public_quizzes])

    scored = []
    for doc in docs:
        result = _score_document(query_text, query_tokens, expanded_tokens, doc, docs)
        if result['score'] > 0:
            scored.append({'doc': doc, **result})

    scored.sort(key=lambda item: (-item['score'], -len(item['matched_tokens']), -(item['doc']['size_count']), item['doc']['title']))

    # Minimum confidence threshold to reduce weak noise.
    threshold = 4.0
    filtered = [item for item in scored if item['score'] >= threshold]
    if not filtered and scored:
        filtered = scored[:min(8, len(scored))]

    has_exact_match = any(len(set(query_tokens) & item['doc']['title_tokens']) > 0 or len(set(query_tokens) & item['doc']['tags_tokens']) > 0 for item in filtered)

    deck_results = []
    quiz_results = []
    for item in filtered[:limit]:
        doc = item['doc']
        payload = {
            'score': round(item['score'], 2),
            'match_reasons': item['reasons'],
        }
        if doc['type'] == 'deck':
            deck = next((d for d in public_decks if d.deck_id == doc['id']), None)
            if not deck:
                continue
            payload.update({
                'deck_id': deck.deck_id,
                'description': deck.description,
                'detailed_description': deck.detailed_description,
                'tags': deck.tags,
                'sortable': deck.sortable,
                'is_public': deck.is_public,
                'card_count': len(deck.cards),
            })
            deck_results.append(payload)
        else:
            quiz = next((q for q in public_quizzes if q.quiz_id == doc['id']), None)
            if not quiz:
                continue
            payload.update({
                'quiz_id': quiz.quiz_id,
                'title': quiz.title,
                'description': quiz.description,
                'tags': quiz.tags,
                'is_public': quiz.is_public,
                'question_count': len(quiz.questions),
            })
            quiz_results.append(payload)

    return {
        'decks': deck_results,
        'quizzes': quiz_results,
        'has_exact_match': has_exact_match,
        'query_tokens': query_tokens,
        'expanded_tokens': sorted(expanded_tokens - set(query_tokens)),
    }


## Card and answer database operations

# Create a new card with one or more answers
def add_card(deck_id, question, answers):
    # Get the next position for this card
    max_position = db.session.query(db.func.max(Card.position)).filter_by(deck_id=deck_id).scalar() or 0
    next_position = max_position + 1

    # Create the card
    card = Card(deck_id=deck_id, question=question, position=next_position)
    db.session.add(card)
    db.session.flush()
    
    answers = _normalize_answers(answers)
    if not answers:
        raise ValueError('At least one answer is required')

    # Add each answer to the database
    for answer_text in answers:
        card_answer = CardAnswer(card_id=card.card_id, answer=answer_text)
        db.session.add(card_answer)

    db.session.commit()
    return card


# Add an additional answer to an existing card
def add_answer_to_card(card_id, answer):
    card = Card.query.get(card_id)
    if card:
        card_answer = CardAnswer(card_id=card_id, answer=answer)
        db.session.add(card_answer)
        db.session.commit()
        return card_answer
    return None


# Delete a single answer and remove the card if it no longer has answers.
def delete_answer(answer_id):
    answer = CardAnswer.query.get(answer_id)
    if not answer:
        return None

    card = answer.card
    deck_id = card.deck_id if card else None
    card_id = card.card_id if card else None

    db.session.delete(answer)
    db.session.flush()

    card_deleted = False
    remaining_answers = CardAnswer.query.filter_by(card_id=card_id).count() if card_id else 0
    if card and remaining_answers == 0:
        db.session.delete(card)
        card_deleted = True

    db.session.commit()
    return {'answer_deleted': True, 'card_deleted': card_deleted, 'card_id': card_id, 'deck_id': deck_id}


# Delete a card and all its answers
def delete_card(card_id):
    card = Card.query.get(card_id)
    if card:
        db.session.delete(card)
        db.session.commit()
        return True
    return False


# Edit a card's question and answers
def edit_card(card_id, question, answers):
    card = Card.query.get(card_id)
    if card:
        card.question = question
        answers = _normalize_answers(answers)
        if not answers:
            deck_id = card.deck_id
            db.session.delete(card)
            db.session.commit()
            return {'deleted': True, 'card_id': card_id, 'deck_id': deck_id}

        # Delete old answers
        CardAnswer.query.filter_by(card_id=card_id).delete()
        # Add new answers
        for answer_text in answers:
            card_answer = CardAnswer(card_id=card_id, answer=answer_text)
            db.session.add(card_answer)
        db.session.commit()
        return card
    return None


# Get a single card with all its answers
def get_card_from_deck(card_id, detailed=False):
    card = Card.query.get(card_id)
    if card:
        if detailed:
            return _serialize_card(card, detailed=True)
        return {
            'card_id': card.card_id,
            'question': card.question,
            'answers': [answer.answer for answer in card.answers],
            'deck_id': card.deck_id,
            'position': card.position,
        }
    return None


def get_deck_study_data(deck_id, shuffle=True):
    deck = Deck.query.get(deck_id)
    if not deck:
        return None

    return _serialize_deck(deck, detailed_cards=True, shuffle_cards=shuffle, shuffle_answers=shuffle)


def check_deck_order(deck_id, ordered_card_ids):
    """Validate a user-submitted card order against stored card positions."""
    deck = Deck.query.get(deck_id)
    if not deck:
        return {'valid': False, 'error': 'Deck not found'}
    if not deck.sortable:
        return {'valid': False, 'error': 'Deck is not sortable'}

    # Stored order is the canonical source of truth for the reorder game.
    cards = sorted(list(deck.cards), key=lambda card: card.position)
    expected_order = [card.card_id for card in cards]

    if len(expected_order) == 0:
        return {'valid': True, 'is_correct': True, 'incorrect_card_ids': [], 'expected_order': [], 'received_order': []}

    if len(ordered_card_ids) != len(expected_order):
        return {'valid': False, 'error': 'Submitted order does not include all cards'}

    if set(ordered_card_ids) != set(expected_order):
        return {'valid': False, 'error': 'Submitted order contains unknown cards'}

    incorrect_card_ids = []
    for index, card_id in enumerate(ordered_card_ids):
        if card_id != expected_order[index]:
            incorrect_card_ids.append(card_id)

    return {
        'valid': True,
        'is_correct': len(incorrect_card_ids) == 0,
        'incorrect_card_ids': incorrect_card_ids,
        'expected_order': expected_order,
        'received_order': ordered_card_ids,
    }


def move_card_in_deck(card_id, direction):
    """Move a card up or down within its deck by swapping position with a neighbor."""
    card = Card.query.get(card_id)
    if not card:
        return {'success': False, 'error': 'Card not found'}
    if not card.deck.sortable:
        return {'success': False, 'error': 'Card order can only be changed in sortable decks'}

    if direction not in ('up', 'down'):
        return {'success': False, 'error': 'Invalid direction'}

    deck_cards = Card.query.filter_by(deck_id=card.deck_id).order_by(Card.position).all()
    current_index = next((index for index, deck_card in enumerate(deck_cards) if deck_card.card_id == card_id), None)
    if current_index is None:
        return {'success': False, 'error': 'Card not found in deck'}

    target_index = current_index - 1 if direction == 'up' else current_index + 1
    if target_index < 0 or target_index >= len(deck_cards):
        return {'success': True, 'moved': False, 'deck_id': card.deck_id}

    target_card = deck_cards[target_index]
    card.position, target_card.position = target_card.position, card.position
    db.session.commit()

    return {'success': True, 'moved': True, 'deck_id': card.deck_id}


def swap_cards_in_deck(card_id, target_card_id):
    """Swap two cards in the same sortable deck."""
    first_card = Card.query.get(card_id)
    second_card = Card.query.get(target_card_id)

    if not first_card or not second_card:
        return {'success': False, 'error': 'One or more cards were not found'}
    if first_card.deck_id != second_card.deck_id:
        return {'success': False, 'error': 'Cards must be in the same deck'}
    if not first_card.deck.sortable:
        return {'success': False, 'error': 'Card order can only be changed in sortable decks'}
    if first_card.card_id == second_card.card_id:
        return {'success': True, 'swapped': False, 'deck_id': first_card.deck_id}

    first_card.position, second_card.position = second_card.position, first_card.position
    db.session.commit()

    return {'success': True, 'swapped': True, 'deck_id': first_card.deck_id}


# Get all cards from a deck ordered by position
def list_cards_from_deck(deck_id, detailed=False, shuffle=False):
    deck = Deck.query.get(deck_id)
    if not deck:
        return []
    return _serialize_deck(deck, detailed_cards=detailed, shuffle_cards=shuffle, shuffle_answers=False)['cards']


def get_deck_details(deck_id, shuffle_cards=False, shuffle_answers=False):
    deck = Deck.query.get(deck_id)
    if not deck:
        return None
    return _serialize_deck(deck, detailed_cards=True, shuffle_cards=shuffle_cards, shuffle_answers=shuffle_answers)


def generate_quiz_data(deck_id=None, custom_quiz_id=None):
    from models import Quiz, Card, CardAnswer
    quiz_questions = []
    
    if deck_id:
        deck = Deck.query.get(deck_id)
        cards = list(deck.cards) if deck else []

        # Get all answers from THIS deck to use as distractors (user's feedback: "The answers should only be taken from the selected deck.")
        all_distractors = []
        other_answers = CardAnswer.query.join(Card).filter(Card.deck_id == deck_id).all()
        all_distractors = [a.answer for a in other_answers]
        
        if not all_distractors:
            all_distractors = ["Option A", "Option B", "Option C", "Option D", "No other answers available"]

        for i, c in enumerate(cards):
            is_obj = hasattr(c, 'question')
            question_text = c.question if is_obj else c.get('question')
            
            if is_obj:
                correct_pool = [a.answer for a in c.answers]
            else:
                correct_pool = c.get('correct_answers', [])
                
            num_correct = random.randint(1, len(correct_pool)) if correct_pool else 0
            chosen_correct = random.sample(correct_pool, num_correct) if correct_pool else []
            
            num_wrong = 4 - len(chosen_correct)
            if num_wrong < 0:
                chosen_correct = random.sample(chosen_correct, 4)
                num_wrong = 0
                
            chosen_wrong = []
            if num_wrong > 0:
                safe_distractors = [d for d in all_distractors if d not in correct_pool]
                if len(safe_distractors) >= num_wrong:
                    chosen_wrong = random.sample(safe_distractors, num_wrong)
                else:
                    chosen_wrong = safe_distractors + [f"Generic Distractor {x}" for x in range(num_wrong - len(safe_distractors))]
                    
            options = [{'text': ans, 'is_correct': True} for ans in chosen_correct] + \
                      [{'text': ans, 'is_correct': False} for ans in chosen_wrong]
            random.shuffle(options)
            
            quiz_questions.append({
                'id': c.card_id if is_obj else f"custom_{i}",
                'question': question_text,
                'options': options
            })
            
    elif custom_quiz_id:
        quiz = Quiz.query.get(custom_quiz_id)
        if not quiz: return []
        
        all_quiz_options_by_q = {q.question_id: [opt.text for opt in q.options] for q in quiz.questions}
        
        for q in quiz.questions:
            if q.type == 'static':
                options = [{'text': opt.text, 'is_correct': opt.is_correct} for opt in q.options]
                random.shuffle(options)
                quiz_questions.append({
                    'id': f"q_{q.question_id}",
                    'question': q.question,
                    'options': options
                })
            elif q.type == 'dynamic':
                # Dynamic: pool of correct options (treat all provided options as valid correct answers for this question)
                # We select 1 correct option.
                correct_pool = [opt.text for opt in q.options]
                chosen_correct = [random.choice(correct_pool)] if correct_pool else []
                
                distractor_pool = []
                for other_q_id, opts in all_quiz_options_by_q.items():
                    if other_q_id != q.question_id:
                        distractor_pool.extend(opts)
                
                chosen_wrong = []
                # Ensure distractors are not accidentally correct for this question
                safe_distractors = list(set([d for d in distractor_pool if d not in correct_pool]))
                if len(safe_distractors) >= 3:
                    chosen_wrong = random.sample(safe_distractors, 3)
                else:
                    chosen_wrong = safe_distractors + [f"Distractor {x}" for x in range(3 - len(safe_distractors))]
                    
                options = [{'text': ans, 'is_correct': True} for ans in chosen_correct] + \
                          [{'text': ans, 'is_correct': False} for ans in chosen_wrong]
                random.shuffle(options)
                
                quiz_questions.append({
                    'id': f"q_{q.question_id}",
                    'question': q.question,
                    'options': options
                })
        
    return quiz_questions


# Register all application routes
register_routes(app)


if __name__ == '__main__':
    app.run(debug=True)

