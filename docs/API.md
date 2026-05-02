# Cards App API Documentation

## Base URL
`http://localhost:5000`

## Request Notes

- `POST` routes accept either form data or JSON.
- Field names use `snake_case` in the current codebase.
- Most UI routes assume the default local user id `1`.
- Success redirects may include `notice` and `level` query parameters for the global toast handler.

---

## Page Routes

### Home
**GET** `/`

### Deck Editor
**GET** `/edit`
- Query params: `deck_id`

### Study View
**GET** `/view`
- Query params: `deck_id`

### Matching Game
**GET** `/match`
- Query params: `deck_id`, `selected_question`, `error`

### Reorder Game
**GET** `/reorder`
- Query params: `deck_id`

### Search
**GET** `/search`
- Query params: `q`

### Quiz Launcher
**GET** `/quiz`
- Query params:
  - `quiz_source=deck:<deck_id>`
  - `quiz_source=custom:<quiz_id>`
  - Legacy fallback: `deck_id` or `custom_quiz_id`

### Custom Quiz Editor
**GET** `/edit_quiz`
- Query params: `quiz_id`

---

## Deck Routes

### Create Deck
**POST** `/create_deck`

Fields:
- `user_id`
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
- `user_id`

Response:
```json
{
  "success": true,
  "decks": [
    { "deck_id": 1, "description": "Spanish Vocabulary", "sortable": false, "card_count": 5 }
  ]
}
```

### Edit Deck
**POST** `/edit_deck`

Fields:
- `deck_id`
- `description`
- `detailed_description` optional
- `tags` optional
- `sortable` optional
- `is_public` optional

### Delete Deck
**POST** `/delete_deck`

Fields:
- `deck_id`

---

## Card Routes

### Add Card
**POST** `/add_card`

Fields:
- `deck_id`
- `question`
- `answers`

`answers` may be a comma-separated string or a list.

### List Cards
**POST** `/list_cards`

Fields:
- `deck_id`
- `shuffle` optional
- `detailed` optional

### Get Card
**POST** `/get_card`

Fields:
- `card_id`

### Edit Card
**POST** `/edit_card`

Fields:
- `card_id`
- `deck_id` optional for redirects
- `question`
- `answers`

If `answers` is empty, the card is deleted.

### Delete Card
**POST** `/delete_card`

Fields:
- `card_id`
- `deck_id` optional for redirects

### Delete One Answer
**POST** `/delete_answer`

Fields:
- `answer_id`
- `deck_id` optional
- `selected_question_id` optional
- `context` optional

If the last answer is removed, the parent card is deleted too.

### Match an Answer
**POST** `/match_answer`

Fields:
- `answer_id`
- `selected_question_id`

This only validates the match; it does not remove the answer row itself.

### Move a Card
**POST** `/move_card`

Fields:
- `card_id`
- `deck_id` optional
- `direction` must be `up` or `down`

### Swap Two Cards
**POST** `/swap_cards`

Fields:
- `card_id`
- `target_card_id`

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

---

## Search Routes

### Public Content Search
**GET** `/search?q=...`

- Searches public decks and public quizzes.
- Uses the full-text index when available.
- Falls back to `LIKE` matching if the index cannot be used.

### Search Result Fields
- Deck results include `description`, `detailed_description`, `tags`, `sortable`, `is_public`, `card_count`, `score`, and `match_reasons`.
- Quiz results include `title`, `description`, `tags`, `is_public`, `question_count`, `score`, and `match_reasons`.

---

## Quiz Routes

### Create Custom Quiz
**POST** `/create_custom_quiz`

Fields:
- `title`
- `description` optional
- `tags` optional
- `is_public` optional

### Edit Custom Quiz
**POST** `/edit_custom_quiz`

Fields:
- `quiz_id`
- `title`
- `description` optional
- `tags` optional
- `is_public` optional

### Delete Custom Quiz
**POST** `/delete_custom_quiz`

Fields:
- `quiz_id`

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

### Edit Quiz Question
**POST** `/edit_quiz_question`

Fields match `add_quiz_question`, plus:
- `question_id`

### Delete Quiz Question
**POST** `/delete_quiz_question`

Fields:
- `quiz_id`
- `question_id`

### Score Quiz
**POST** `/score_quiz`

Fields:
- `answers`
- `quiz_data`

Response:
```json
{
  "success": true,
  "score": 3,
  "total": 5,
  "results": []
}
```

---

## Common Status Codes

| Code | Meaning |
|---|---|
| 200 | Request successful |
| 400 | Missing or invalid input |
| 404 | Resource not found |

