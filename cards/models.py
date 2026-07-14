from sqlalchemy import CheckConstraint, event, func
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db
from .identity import canonical_email, canonical_username, display_username, recovery_email_digest
from .search_index import register_metadata_hooks


# Account and ownership models.
class User(db.Model):
    user_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(100), nullable=False, unique=True)
    canonical_username = db.Column(db.String(40), nullable=False, unique=True)
    email = db.Column(db.String(255), nullable=True, unique=True)
    canonical_email = db.Column(db.String(255), nullable=True, unique=True)
    # Keyed digest used by the password-reset worker; it is not reversible and
    # keeps the raw recovery address out of queue payloads.
    recovery_email_digest = db.Column(db.String(64), nullable=True, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    auth_version = db.Column(db.Integer, nullable=False, default=0, server_default=db.text('0'))
    role = db.Column(db.String(20), nullable=False, default='standard', server_default=db.text("'standard'"))
    theme_preference = db.Column(db.String(10), nullable=False, default='dark', server_default=db.text("'dark'"))
    mastery_strategy_preference = db.Column(db.String(30), nullable=False, default='spaced', server_default=db.text("'spaced'"))
    match_strategy_preference = db.Column(db.String(30), nullable=False, default='standard_shuffle', server_default=db.text("'standard_shuffle'"))
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.text('true'))
    created_at = db.Column(db.DateTime, nullable=False, server_default=func.now())
    updated_at = db.Column(db.DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    decks_owned = db.relationship('Deck', backref='owner', lazy=True, cascade='all, delete-orphan', passive_deletes=True)
    quizzes_owned = db.relationship('Quiz', backref='owner', lazy=True, cascade='all, delete-orphan', passive_deletes=True)
    mastery_progress = db.relationship('CardMasteryProgress', backref='user', lazy=True, cascade='all, delete-orphan', passive_deletes=True)
    match_progress = db.relationship('MatchPairProgress', backref='user', lazy=True, cascade='all, delete-orphan', passive_deletes=True)
    quiz_attempts = db.relationship('QuizAttempt', backref='user', lazy=True, passive_deletes=True)

    __table_args__ = (
        CheckConstraint("role IN ('standard', 'moderator', 'admin')", name='ck_user_role'),
        CheckConstraint("theme_preference IN ('light', 'dark')", name='ck_user_theme_preference'),
        CheckConstraint("mastery_strategy_preference IN ('linear', 'weakest_first', 'spaced', 'mastery_mix', 'random')", name='ck_user_mastery_strategy'),
        CheckConstraint("match_strategy_preference IN ('standard_shuffle', 'retry_misses', 'progressive_build', 'reverse_pressure', 'timed_recovery', 'weakest_first', 'mastery_mix')", name='ck_user_match_strategy'),
        CheckConstraint('auth_version >= 0', name='ck_user_auth_version_nonnegative'),
        CheckConstraint('is_active IS TRUE OR is_active IS FALSE', name='ck_user_is_active_boolean'),
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.is_active and self.role == 'admin'


# Flashcard models.
class Deck(db.Model):
    deck_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    owned_by = db.Column(db.Integer, db.ForeignKey('user.user_id', ondelete='CASCADE'), nullable=False, index=True)
    description = db.Column(db.String(255), nullable=True)
    detailed_description = db.Column(db.Text, nullable=True)
    tags = db.Column(db.String(255), nullable=True)
    sortable = db.Column(db.Boolean, nullable=False, default=False, server_default=db.text('false'))
    is_public = db.Column(db.Boolean, nullable=False, default=False, server_default=db.text('false'), index=True)
    is_featured = db.Column(db.Boolean, nullable=False, default=False, server_default=db.text('false'), index=True)
    cards = db.relationship('Card', backref='deck', lazy=True, cascade='all, delete-orphan', passive_deletes=True)
    tag_rows = db.relationship('DeckTag', backref='deck', lazy=True, cascade='all, delete-orphan', passive_deletes=True)
    collaborators = db.relationship('DeckCollaborator', backref='deck', lazy=True, cascade='all, delete-orphan', passive_deletes=True)
    share_links = db.relationship('DeckShareLink', backref='deck', lazy=True, cascade='all, delete-orphan', passive_deletes=True)

    __table_args__ = (
        db.Index('ix_deck_public_featured_id', 'is_public', 'is_featured', 'deck_id'),
        CheckConstraint('sortable IS TRUE OR sortable IS FALSE', name='ck_deck_sortable_boolean'),
        CheckConstraint('is_public IS TRUE OR is_public IS FALSE', name='ck_deck_is_public_boolean'),
        CheckConstraint('is_featured IS TRUE OR is_featured IS FALSE', name='ck_deck_is_featured_boolean'),
    )


class DeckTag(db.Model):
    """One normalized tag per deck, used for public tag aggregation."""
    deck_id = db.Column(db.Integer, db.ForeignKey('deck.deck_id', ondelete='CASCADE'), primary_key=True)
    tag_normalized = db.Column(db.String(255), primary_key=True)
    tag_display = db.Column(db.String(255), nullable=False)

    __table_args__ = (
        db.Index('ix_deck_tag_normalized_deck_id', 'tag_normalized', 'deck_id'),
    )


class DeckCollaborator(db.Model):
    """An account that may edit a deck without becoming its owner."""
    deck_id = db.Column(db.Integer, db.ForeignKey('deck.deck_id', ondelete='CASCADE'), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.user_id', ondelete='CASCADE'), primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, server_default=func.now())
    user = db.relationship('User')


class DeckShareLink(db.Model):
    """Opaque unlisted URL granting read-only access, optionally with copying."""
    token = db.Column(db.String(64), primary_key=True)
    deck_id = db.Column(db.Integer, db.ForeignKey('deck.deck_id', ondelete='CASCADE'), nullable=False, index=True)
    permission = db.Column(db.String(10), nullable=False, default='view', server_default=db.text("'view'"))
    created_at = db.Column(db.DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("permission IN ('view', 'copy')", name='ck_deck_share_link_permission'),
    )


# Card question plus one or more answers.
class Card(db.Model):
    card_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    deck_id = db.Column(db.Integer, db.ForeignKey('deck.deck_id', ondelete='CASCADE'), nullable=False, index=True)
    question = db.Column(db.Text, nullable=False)
    position = db.Column(db.Integer, nullable=False)
    answers = db.relationship('CardAnswer', backref='card', lazy=True, cascade='all, delete-orphan', passive_deletes=True)
    mastery_progress = db.relationship('CardMasteryProgress', backref='card', lazy=True, cascade='all, delete-orphan', passive_deletes=True)

    __table_args__ = (
        db.Index('ix_card_deck_id_position', 'deck_id', 'position'),
        db.UniqueConstraint('deck_id', 'position', name='uq_card_deck_position'),
        CheckConstraint('position > 0', name='ck_card_position_positive'),
    )


class CardAnswer(db.Model):
    answer_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    card_id = db.Column(db.Integer, db.ForeignKey('card.card_id', ondelete='CASCADE'), nullable=False, index=True)
    answer = db.Column(db.Text, nullable=False)
    match_progress = db.relationship('MatchPairProgress', backref='answer', lazy=True, cascade='all, delete-orphan', passive_deletes=True)


# Progress-tracking models.
class CardMasteryProgress(db.Model):
    progress_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.user_id', ondelete='CASCADE'), nullable=False, index=True)
    card_id = db.Column(db.Integer, db.ForeignKey('card.card_id', ondelete='CASCADE'), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default='new', server_default=db.text("'new'"))
    understood_count = db.Column(db.Integer, nullable=False, default=0, server_default=db.text('0'))
    learning_count = db.Column(db.Integer, nullable=False, default=0, server_default=db.text('0'))
    dont_know_count = db.Column(db.Integer, nullable=False, default=0, server_default=db.text('0'))
    reviewed_count = db.Column(db.Integer, nullable=False, default=0, server_default=db.text('0'))
    last_rating = db.Column(db.String(20), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    created_at = db.Column(db.DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        db.UniqueConstraint('user_id', 'card_id', name='uq_card_mastery_user_card'),
        CheckConstraint("status IN ('new', 'learning', 'mastered', 'unknown')", name='ck_card_mastery_status'),
        CheckConstraint('understood_count >= 0', name='ck_card_mastery_understood_nonnegative'),
        CheckConstraint('learning_count >= 0', name='ck_card_mastery_learning_nonnegative'),
        CheckConstraint('dont_know_count >= 0', name='ck_card_mastery_dont_know_nonnegative'),
        CheckConstraint('reviewed_count >= 0', name='ck_card_mastery_reviewed_nonnegative'),
        CheckConstraint("last_rating IS NULL OR last_rating IN ('understood', 'still_learning', 'dont_know')", name='ck_card_mastery_last_rating'),
    )


class MatchPairProgress(db.Model):
    progress_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.user_id', ondelete='CASCADE'), nullable=False, index=True)
    answer_id = db.Column(db.Integer, db.ForeignKey('card_answer.answer_id', ondelete='CASCADE'), nullable=False, index=True)
    correct_count = db.Column(db.Integer, nullable=False, default=0, server_default=db.text('0'))
    incorrect_count = db.Column(db.Integer, nullable=False, default=0, server_default=db.text('0'))
    last_outcome = db.Column(db.String(20), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    created_at = db.Column(db.DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        db.UniqueConstraint('user_id', 'answer_id', name='uq_match_pair_user_answer'),
        CheckConstraint('correct_count >= 0', name='ck_match_pair_correct_nonnegative'),
        CheckConstraint('incorrect_count >= 0', name='ck_match_pair_incorrect_nonnegative'),
        CheckConstraint("last_outcome IS NULL OR last_outcome IN ('correct', 'incorrect')", name='ck_match_pair_last_outcome'),
    )

# Quiz authoring models.
class Quiz(db.Model):
    quiz_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    owned_by = db.Column(db.Integer, db.ForeignKey('user.user_id', ondelete='CASCADE'), nullable=False, index=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    tags = db.Column(db.String(255), nullable=True)
    is_public = db.Column(db.Boolean, nullable=False, default=False, server_default=db.text('false'), index=True)
    questions = db.relationship('QuizQuestion', backref='quiz', lazy=True, cascade='all, delete-orphan', passive_deletes=True)

    __table_args__ = (
        CheckConstraint('is_public IS TRUE OR is_public IS FALSE', name='ck_quiz_is_public_boolean'),
    )

class QuizQuestion(db.Model):
    question_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.quiz_id', ondelete='CASCADE'), nullable=False, index=True)
    question = db.Column(db.Text, nullable=False)
    # Static uses fixed options; dynamic pulls distractors from other quiz questions.
    type = db.Column(db.String(50), nullable=False, default='dynamic', server_default=db.text("'dynamic'"))
    options = db.relationship('QuizOption', backref='question', lazy=True, cascade='all, delete-orphan', passive_deletes=True)

    __table_args__ = (
        CheckConstraint("type IN ('dynamic', 'static')", name='ck_quiz_question_type'),
    )

class QuizOption(db.Model):
    option_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    question_id = db.Column(db.Integer, db.ForeignKey('quiz_question.question_id', ondelete='CASCADE'), nullable=False, index=True)
    text = db.Column(db.Text, nullable=False)
    is_correct = db.Column(db.Boolean, nullable=False, default=False, server_default=db.text('false'))

    __table_args__ = (
        CheckConstraint('is_correct IS TRUE OR is_correct IS FALSE', name='ck_quiz_option_is_correct_boolean'),
    )


# Runtime quiz-session storage.
class QuizAttempt(db.Model):
    attempt_token = db.Column(db.String(64), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.user_id', ondelete='SET NULL'), nullable=True, index=True)
    session_id = db.Column(db.String(64), nullable=True, index=True)
    correct_answers_json = db.Column(db.Text, nullable=False)
    question_count = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, server_default=func.now(), index=True)

    __table_args__ = (
        CheckConstraint('question_count > 0', name='ck_quiz_attempt_question_count_positive'),
    )


# ``db.create_all()`` is used by the lightweight local/test setup.  Deployed
# databases use the equivalent Alembic migration; this hook is not run during
# application construction or ordinary requests.
register_metadata_hooks(db.metadata)


@event.listens_for(User, 'before_insert')
@event.listens_for(User, 'before_update')
def _populate_canonical_identity(_mapper, _connection, user):
    """Keep direct ORM writes subject to the same identity and recovery policy as services."""
    user.username = display_username(user.username)
    user.canonical_username = canonical_username(user.username)
    user.canonical_email = canonical_email(user.email)
    user.recovery_email_digest = recovery_email_digest(user.canonical_email)
