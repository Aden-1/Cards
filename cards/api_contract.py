"""Small request/response primitives shared by API-style route handlers.

The application intentionally supports both browser forms and JSON clients.
These helpers make the distinction explicit without turning every Flask error
page into JSON.
"""

from flask import g, jsonify, request
from werkzeug.exceptions import BadRequest, UnsupportedMediaType


JSON_ONLY_ENDPOINTS = frozenset({'swap_cards', 'check_reorder', 'score_quiz'})
JSON_RESPONSE_ENDPOINTS = frozenset({
    'healthz',
    'readyz',
    'get_decks',
    'list_cards',
    'get_card',
    'match_answer',
    'match_attempt',
})
API_PATHS = frozenset({
    '/theme', '/quiz/start', '/score_quiz', '/master/rate', '/master/reset',
    '/create_custom_quiz', '/edit_custom_quiz', '/delete_custom_quiz',
    '/add_quiz_question', '/edit_quiz_question', '/delete_quiz_question',
    '/create_deck', '/import_deck', '/get_decks', '/delete_deck', '/edit_deck',
    '/add_card', '/delete_card', '/cards/bulk', '/decks/duplicate', '/match_answer', '/match_attempt',
    '/delete_answer', '/list_cards', '/get_card', '/edit_card', '/move_card',
    '/swap_cards', '/check_reorder',
})
FORM_CONTENT_TYPES = frozenset({'application/x-www-form-urlencoded', 'multipart/form-data'})


def wants_json_response():
    """Return whether this request explicitly asks for a JSON response."""
    return request.is_json or request.accept_mimetypes.best == 'application/json'


def is_api_request():
    """Classify only API-oriented requests as JSON error responses."""
    return bool(
        getattr(g, 'api_request', False)
        or wants_json_response()
        or request.endpoint in JSON_RESPONSE_ENDPOINTS
        or request.path in API_PATHS
    )


def api_response(payload, status=200):
    """Return a JSON response with a predictable status and content type."""
    response = jsonify(payload)
    response.status_code = status
    return response


def api_error(message, status):
    """Return the public error envelope used by JSON clients."""
    return api_response({'error': message}, status)


def request_payload():
    """Parse a JSON object or a supported browser form without silent fallback.

    A request labelled as JSON that cannot be decoded is a client error; it is
    never reinterpreted as an empty form. JSON-only endpoints also reject
    unsupported body media types before route validation runs.
    """
    endpoint = request.endpoint
    if request.is_json:
        g.api_request = True
        try:
            payload = request.get_json(silent=False)
        except BadRequest as exc:
            raise BadRequest(description='Malformed JSON request body.') from exc
        if not isinstance(payload, dict):
            raise BadRequest(description='JSON request body must be an object.')
        return payload

    if endpoint in JSON_ONLY_ENDPOINTS:
        g.api_request = True
        if request.mimetype != 'application/json':
            raise UnsupportedMediaType(description='This endpoint requires application/json.')

    if request.mimetype and request.mimetype not in FORM_CONTENT_TYPES:
        # Hybrid mutation endpoints still accept normal browser forms, but an
        # explicitly unsupported media type should produce a JSON-safe error.
        if endpoint not in (None, 'static'):
            g.api_request = True
            raise UnsupportedMediaType(description='Unsupported request Content-Type.')

    return request.values.to_dict(flat=True)
