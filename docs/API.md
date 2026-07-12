# Cards App API Documentation

## Base URL
`http://localhost:5000`

## Request Notes

- `POST` routes accept either form data or JSON.
- Field names use `snake_case` in the current codebase.
- Session-based authentication is now used for account-aware routes.
- All state-changing requests require CSRF protection via `csrf_token` form data or an `X-CSRFToken` header.
- Success redirects may include `notice` and `level` query parameters for the global toast handler.
- Some routes are HTML-first and redirect on success even when they are not classic JSON APIs.

## JSON Contract

JSON clients should send `Content-Type: application/json` and may send
`Accept: application/json`. JSON responses always use
`Content-Type: application/json` and retain the documented success fields.
Successful mutations use the existing `success: true` envelope where one was
already documented; failures use:

```json
{ "error": "public message" }
```

The standard statuses are `200` for success, `400` for malformed or invalid
input (including CSRF), `401` for missing authentication, `403` for denied
access, `404` for missing resources, `405` for unsupported methods, `413`
for oversized bodies, `415` for unsupported request media types, and `429`
for rate limits. Rate-limited responses preserve `Retry-After`.

Malformed JSON is never treated as an empty form. JSON-only reorder and quiz
actions reject non-JSON bodies. API errors and unexpected 500 errors use the
JSON envelope without exposing exception details. Browser form requests retain
their rendered pages, redirects, and flash/toast behavior.

---

## Page Routes

### Home
**GET** `/`

Returns featured public decks and popular public tags.

Response:
- Rendered HTML page.

### Register
**GET, POST** `/register`

Fields:
- `username`
- `email` optional
- `password`
- `confirm_password`

Notes:
- Username must be 3-40 characters using Unicode letters/numbers, dots, dashes, or underscores. Identity comparisons trim, NFKC-normalize, and casefold the value; the display username keeps its submitted case.
- Email is optional during alpha, but accounts with an email can use password recovery later.
- New passwords must be at least 12 characters and contain a letter and a number.
- Public registration always creates a `standard` account. Administrators are provisioned through the controlled CLI workflow.

Response:
- `GET`: rendered HTML registration page.
- `POST`: redirect to `/edit` on success, or rendered HTML with validation errors on failure.

### Login
**GET, POST** `/login`

Fields:
- `username`
- `password`
- `next` optional

Response:
- `GET`: rendered HTML login page.
- `POST`: redirect to the safe `next` URL or `/` on success, or rendered HTML with an error message on failure.

### Forgot Password
**GET, POST** `/forgot-password`

Fields:
- `email`

Response:
- `GET`: rendered HTML reset-request page.
- `POST`: for a syntactically valid email, always renders the same generic success page whether an account exists or the queue/provider is unavailable. Delivery is asynchronous and is never reflected in the public response.

### Reset Password
**GET, POST** `/reset-password`

Fields:
- `token`
- `password`
- `confirm_password`

Response:
- `GET`: rendered HTML page for a signed reset token.
- `POST`: redirect to `/login` on success, or rendered HTML with validation errors on failure.

### Logout
**POST** `/logout`

Response:
- Redirect to `/` with a logout notice.

### Account
**GET, POST** `/account`

Fields:
- `username`
- `email` optional
- `current_password`
- `new_password` optional
- `confirm_password` optional

Response:
- `GET`: rendered HTML account page.
- `POST`: rendered HTML account page with `success` or `error` state.

### Delete Account
**POST** `/account/delete`

Fields:
- `current_password`
- `confirmation` must equal `DELETE`

Response:
- Redirect to `/` after deleting the signed-in user's account and owned content.

### Update Theme
**POST** `/theme`

Fields:
- `theme` must be `light` or `dark`

Response:
```json
{ "success": true, "theme": "dark" }
```

### Admin Users
**GET, POST** `/admin/users`

Admin-only route.

POST fields:
- `user_id`
- `action` one of `promote_admin`, `promote_moderator`, `demote_standard`, or `delete`

Response:
- `GET`: rendered, paginated HTML admin user list. `page` and `page_size` are accepted; page size is capped at 50.
- `POST`: redirect back to `/admin/users` with a notice describing the result.

### Public-content moderation
**POST** `/moderation/unpublish`

Moderator- and admin-only. Accepts `content_type` (`deck` or `quiz`) and
`content_id`. It can only change an already-public item to private. It cannot
edit private content, accounts, or roles. Inactive sessions have no authority.
Successful changes emit an audit-safe event containing only actor/target IDs,
type, and outcome.

### Deck Editor
**GET** `/edit`
- Query params: `deck_id`, `page`, `page_size` (maximum 50)

Response:
- Rendered HTML page.

### Study View
**GET** `/view`
- Query params: `deck_id`

Response:
- Rendered HTML page.

### Matching Game
**GET** `/match`
- Query params: `deck_id`, `selected_question`, `error`, `strategy`

Response:
- Rendered HTML page.

### Reorder Game
**GET** `/reorder`
- Query params: `deck_id`

Response:
- Rendered HTML page.

### Mastery Mode
**GET** `/master`

- Query params: `deck_id`, `strategy`
- Requires login.

Response:
- Rendered HTML page.

### Search
**GET** `/search`
- Query params: `q`, `page`, `page_size` (maximum 50)

Response:
- Rendered HTML search results page.

### Public Deck Detail
**GET** `/public_deck`

- Query params: `deck_id`

Response:
- Rendered HTML page for a visible deck, or redirect to `/search` if the deck is missing/inaccessible.

### Public Quiz Detail
**GET** `/public_quiz`

- Query params: `quiz_id`

Response:
- Rendered HTML page for a visible quiz, or redirect to `/search` if the quiz is missing/inaccessible.

### Quiz Launcher
**GET** `/quiz`
- Query params:
  - `quiz_source=deck:<deck_id>`
  - `quiz_source=custom:<quiz_id>`
  - Legacy fallback: `deck_id` or `custom_quiz_id`
  - `page`, `page_size` (maximum 50)

Response:
- Rendered HTML launcher page. Selecting or opening a source does not create a
  server-side attempt.

### Start Quiz
**POST** `/quiz/start`

Fields:
- `quiz_source=deck:<deck_id>` or `quiz_source=custom:<quiz_id>`
- `csrf_token`

Response:
- Rendered quiz page containing the bounded question set and a one-time attempt token.

Notes:
- The endpoint is rate limited.
- Active attempts are capped per authenticated user or guest session; displaced
  attempts are deleted immediately.

### Custom Quiz Editor
**GET** `/edit_quiz`
- Query params: `quiz_id`, `page`, `page_size` (maximum 50)

Response:
- Rendered HTML page.

---

## Deck Routes

### Create Deck
**POST** `/create_deck`

Fields:
- `description`
- `detailed_description` optional
- `tags` optional
- `sortable` optional
- `is_public` optional

Response:
```json
{ "success": true, "deck_id": 1, "description": "Spanish Vocabulary" }
```

### List Decks
**POST** `/get_decks`

Fields:
- No request fields are required.

Notes:
- Returns decks owned by the currently signed-in user.
- Accepts `page` and `page_size`; responses include pagination metadata and never return more than 50 decks.

Response:
```json
{
  "success": true,
  "decks": [
    { "deck_id": 1, "description": "Spanish Vocabulary", "sortable": false, "card_count": 5 }
  ]
}
```

### Import Deck
**POST** `/import_deck`

Fields:
- `description`
- `import_text`
- `detailed_description` optional
- `tags` optional
- `sortable` optional
- `is_public` optional

Notes:
- `import_text` accepts pasted CSV or tab-delimited rows.
- Each valid line should contain at least `question,answer`.
- Duplicate answers for the same question are deduplicated during import.
- Server-side limits are 2 MiB of raw text, 500 cards, 10 answers per card,
  5,000 characters per question, and 2,000 characters per answer.
- Validation finishes before the deck graph is written. The deck, cards,
  answers, normalized tags, and public-search row commit atomically.

Response:
- Redirect to `/edit?deck_id=<new_id>#deck-editor` on success.
- Redirect back to the import section with an error notice if parsing fails.

### Edit Deck
**POST** `/edit_deck`

Fields:
- `deck_id`
- `description`
- `detailed_description` optional
- `tags` optional
- `sortable` optional
- `is_public` optional

Response:
```json
{ "success": true, "deck_id": 1 }
```

Browser form response:
- Redirect to `/edit?deck_id=<deck_id>#deck-editor` on success.

### Delete Deck
**POST** `/delete_deck`

Fields:
- `deck_id`

Response:
```json
{ "success": true, "deck_id": 1 }
```

Browser form response:
- Redirect to `/edit#decks-section` on success.

---

## Card Routes

### Add Card
**POST** `/add_card`

Fields:
- `deck_id`
- `question`
- `answers`

`answers` may be a comma-separated string or a list.

Response:
```json
{ "success": true, "card_id": 12 }
```

Browser form response:
- Redirect to `/edit?deck_id=<deck_id>#deck-editor` on success.

### List Cards
**POST** `/list_cards`

Fields:
- `deck_id`
- `shuffle` optional
- `detailed` optional

Notes:
- Public decks can be listed by unauthenticated users.
- Private decks are only available to their owner.

Response:
```json
{
  "success": true,
  "cards": [
    {
      "card_id": 12,
      "question": "Hola",
      "answers": ["Hello"],
      "position": 1
    }
  ]
}
```

When `detailed=true`, each card may also include:
- `answer_objects`: array of `{ "answer_id": 44, "answer": "Hello" }`

### Get Card
**POST** `/get_card`

Fields:
- `card_id`

Response:
```json
{
  "success": true,
  "card": {
    "card_id": 12,
    "question": "Hola",
    "answers": ["Hello"],
    "deck_id": 1,
    "position": 1
  }
}
```

### Edit Card
**POST** `/edit_card`

Fields:
- `card_id`
- `deck_id` optional for redirects
- `question`
- `answers`

If `answers` is empty, the card is deleted.

Response:
```json
{ "success": true, "card_id": 12 }
```

If the edit removes the last answer:
```json
{ "success": true, "card_id": 12, "deleted": true }
```

Browser form response:
- Redirect to `/edit?deck_id=<deck_id>#deck-editor` on success.

### Delete Card
**POST** `/delete_card`

Fields:
- `card_id`
- `deck_id` optional for redirects

Response:
```json
{ "success": true, "card_id": 12 }
```

Browser form response:
- Redirect to the deck editor or deck list after deletion.

### Delete One Answer
**POST** `/delete_answer`

Fields:
- `answer_id`
- `deck_id` optional
- `selected_question_id` optional
- `context` optional

If the last answer is removed, the parent card is deleted too.

Response:
```json
{
  "success": true,
  "answer_deleted": true,
  "card_deleted": false,
  "card_id": 12,
  "deck_id": 1
}
```

Browser form response:
- In `edit` context, redirect to `/edit?deck_id=<deck_id>#deck-editor`.
- In match context, redirect back to `/match` with updated selection state.

### Match an Answer
**POST** `/match_answer`

Fields:
- `answer_id`
- `selected_question_id`

This only validates the match; it does not remove the answer row itself.

Response:
```json
{
  "success": true,
  "answer_deleted": true,
  "card_deleted": false,
  "card_id": 12,
  "remaining_answers": 2
}
```

### Record Match Attempt
**POST** `/match_attempt`

Fields:
- `answer_id`
- `selected_question_id`
- `timed_out` optional, used when a timed-recovery round expires

Notes:
- Persists per-answer match performance when a user is signed in.
- The server computes correctness from the selected question and answer pair; it does not accept a client-claimed success.

Response:
```json
{ "success": true }
```

### Move a Card
**POST** `/move_card`

Fields:
- `card_id`
- `deck_id` optional
- `direction` must be `up` or `down`

Response:
```json
{ "success": true, "moved": true, "deck_id": 1 }
```

Browser form response:
- Redirect to `/edit?deck_id=<deck_id>#deck-editor`.

### Swap Two Cards
**POST** `/swap_cards`

Fields:
- `card_id`
- `target_card_id`

Response:
```json
{ "success": true, "swapped": true, "deck_id": 1 }
```

### Check Reorder
**POST** `/check_reorder`

Fields:
- `deck_id`
- `ordered_card_ids` list

Response includes:
- `is_correct`
- `incorrect_card_ids`
- `expected_order`
- `received_order`

Response:
```json
{
  "success": true,
  "is_correct": false,
  "incorrect_card_ids": [12, 14],
  "expected_order": [12, 13, 14],
  "received_order": [13, 12, 14]
}
```

### Copy Public Deck
**POST** `/copy_public_deck`

Fields:
- `deck_id`

Notes:
- Requires login.
- Creates a private copy owned by the current user.
- The source is preflighted against import and text-size limits before its
  bounded graph is loaded.
- Cards are batch inserted and correlated to answers by their unique
  per-deck positions; the complete private copy commits atomically.

Response:
- Redirect to `/edit?deck_id=<new_id>#deck-editor` on success.
- Redirect to `/search` if the source deck is missing.

---

## Search Routes

### Public Content Search
**GET** `/search?q=...`

- Searches public decks and public quizzes.
- Uses the full-text index when available.
- Falls back to `LIKE` matching if the index cannot be used.
- Both paths use the requested bounded result page with stable ordering; fallback results list decks before quizzes and then by ID.

### Search Result Fields
- Deck results include `description`, `detailed_description`, `tags`, `sortable`, `is_public`, `card_count`, `score`, and `match_reasons`.
- Quiz results include `title`, `description`, `tags`, `is_public`, `question_count`, `score`, and `match_reasons`.
- Search page rendering also receives `query_tokens`, `expanded_tokens`, and `has_exact_match`.

---

## Quiz Routes

### Create Custom Quiz
**POST** `/create_custom_quiz`

Fields:
- `title`
- `description` optional
- `tags` optional
- `is_public` optional

Response:
- Redirect to `/edit_quiz?quiz_id=<new_id>`.

### Edit Custom Quiz
**POST** `/edit_custom_quiz`

Fields:
- `quiz_id`
- `title`
- `description` optional
- `tags` optional
- `is_public` optional

Response:
- Redirect to `/edit_quiz?quiz_id=<quiz_id>`.

### Delete Custom Quiz
**POST** `/delete_custom_quiz`

Fields:
- `quiz_id`

Response:
- Redirect to `/edit_quiz`.

### Copy Public Quiz
**POST** `/copy_public_quiz`

Fields:
- `quiz_id`

Notes:
- Requires login.
- Creates a private copy owned by the current user.
- The source is preflighted to at most 50 questions and 5 options per
  question. Ordered generated-ID correlation preserves question/option
  pairing and dynamic/static semantics in one transaction.

Response:
- Redirect to `/edit_quiz?quiz_id=<new_id>#quiz-editor` on success.
- Redirect to `/search` if the source quiz is missing.

### Add Quiz Question
**POST** `/add_quiz_question`

Fields:
- `quiz_id`
- `question`
- `q_type` optional, `dynamic` or `static`
- `option_1` through `option_5`
- `is_correct_1` through `is_correct_5` for static questions

Rules:
- Dynamic questions need 1-2 correct answers.
- Static questions need 2+ options and 1-2 marked correct.

Response:
- Redirect to `/edit_quiz?quiz_id=<quiz_id>#quiz-editor` with a success or error notice.

### Edit Quiz Question
**POST** `/edit_quiz_question`

Fields match `add_quiz_question`, plus:
- `question_id`

Response:
- Redirect to `/edit_quiz?quiz_id=<quiz_id>#quiz-editor` with a success or error notice.

### Delete Quiz Question
**POST** `/delete_quiz_question`

Fields:
- `quiz_id`
- `question_id`

Response:
- Redirect to `/edit_quiz?quiz_id=<quiz_id>#quiz-editor` on success.

### Score Quiz
**POST** `/score_quiz`

Fields:
- `answers`
- `attempt_token`, generated by `POST /quiz/start`

Response:
```json
{
  "success": true,
  "score": 3,
  "total": 5,
  "results": []
}
```

Notes:
- This route expects JSON in the current UI flow.
- Correct answers remain server-side in a one-time `QuizAttempt`; browser-supplied scoring metadata is ignored.
- An attempt can be scored once and expires after the configured lifetime.
- A question is marked correct only when the submitted option set exactly matches the correct option set.

---

## Mastery Routes

### Save Mastery Rating
**POST** `/master/rate`

Fields:
- `deck_id`
- `card_id`
- `rating`
- `strategy` optional

Notes:
- Requires login.
- Persists one of the mastery ratings used by the UI.

Response:
- Redirect to `/master?deck_id=<deck_id>&strategy=<strategy>#mastery-practice`.

### Reset Mastery Progress
**POST** `/master/reset`

Fields:
- `deck_id`
- `strategy` optional

Notes:
- Requires login.
- Clears the current user's mastery progress for every card in the deck.

Response:
- Redirect to `/master?deck_id=<deck_id>&strategy=<strategy>#mastery-practice`.

### Mastery Strategy Values
- `spaced`
- `weakest_first`
- `mastery_mix`
- `random`
- `linear` for sortable decks

### Match Strategy Values
- `standard_shuffle`
- `retry_misses`
- `progressive_build`
- `reverse_pressure`
- `timed_recovery`
- `weakest_first`
- `mastery_mix`

---

## Common Status Codes

| Code | Meaning |
|---|---|
| 200 | Request successful |
| 400 | Missing or invalid input |
| 404 | Resource not found |
| 401 | Authentication required |
| 403 | Authenticated but not allowed |
| 405 | HTTP method is not supported |
| 413 | Request body exceeds `MAX_CONTENT_LENGTH` |
| 415 | Request `Content-Type` is not supported |
| 429 | Rate limit exceeded; see `Retry-After` |
| 500 | Unexpected server error; implementation details are not returned |
