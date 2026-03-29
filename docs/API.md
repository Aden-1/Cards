# Cards App - API Documentation

## Base URL
`http://localhost:5000`

## Overview
The Cards App provides a REST API for managing decks and flashcards. All API endpoints expect JSON requests and return JSON responses.

---

## Page Routes

### Home Page
**GET** `/`
- Landing page with app information

### Edit Decks & Cards Page
**GET** `/edit`
- Deck and card management interface

### View Cards Page
**GET** `/view`
- Card study interface

---

## Deck Endpoints

### Create Deck
**POST** `/create_deck`

```json
{
    "userId": 1,
    "description": "Spanish Vocabulary",
    "sortable": false
}
```

Response:
```json
{
    "success": true,
    "deckID": 1,
    "description": "Spanish Vocabulary"
}
```

---

### Get All Decks
**POST** `/get_decks`

```json
{
    "userId": 1
}
```

Response:
```json
{
    "success": true,
    "decks": [
        {
            "deckID": 1,
            "description": "Spanish Vocabulary",
            "sortable": false,
            "cardCount": 5
        }
    ]
}
```

---

### Delete Deck
**POST** `/delete_deck`

```json
{
    "deckId": 1
}
```

Response:
```json
{
    "success": true,
    "deckId": 1
}
```

---

### Edit Deck
**POST** `/edit_deck`

```json
{
    "deckId": 1,
    "description": "Updated Deck Name",
    "sortable": true
}
```

Response:
```json
{
    "success": true,
    "deckID": 1
}
```

---

## Card Endpoints

### Add Card
**POST** `/add_card`

```json
{
    "deckId": 1,
    "question": "What is 'hello' in Spanish?",
    "answers": ["Hola", "Buenos días"]
}
```

Response:
```json
{
    "success": true,
    "cardID": 5
}
```

---

### List Cards
**POST** `/list_cards`

```json
{
    "deckId": 1
}
```

Response:
```json
{
    "success": true,
    "cards": [
        {
            "cardID": 1,
            "question": "What is 'hello' in Spanish?",
            "answers": ["Hola", "Buenos días"],
            "position": 1
        }
    ]
}
```

---

### Get Card
**POST** `/get_card`

```json
{
    "cardId": 1
}
```

Response:
```json
{
    "success": true,
    "card": {
        "cardID": 1,
        "question": "What is 'hello' in Spanish?",
        "answers": ["Hola", "Buenos días"],
        "deckID": 1,
        "position": 1
    }
}
```

---

### Delete Card
**POST** `/delete_card`

```json
{
    "cardId": 1
}
```

Response:
```json
{
    "success": true,
    "cardId": 1
}
```

---

### Edit Card
**POST** `/edit_card`

```json
{
    "cardId": 1,
    "question": "Updated question?",
    "answers": ["Answer 1", "Answer 2"]
}
```

Response:
```json
{
    "success": true,
    "cardID": 1
}
```

---

## Response Codes Summary

| Code | Meaning |
|------|---------|
| 200 | Request successful |
| 400 | Bad request (missing/invalid parameters) |
| 404 | Resource not found |


