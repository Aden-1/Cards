# Security Controls

## Account identity

Username and email identity comparisons use one canonical policy: trim,
Unicode NFKC normalization, and casefold. Canonical username/email columns are
backfilled and uniquely constrained by migration `20260711080000`. The
migration fails closed on collisions or invalid legacy values and never merges
accounts. The username display column preserves the submitted case.

Registration, login, account edits, CLI provisioning and role lookup, password
recovery, worker digest lookup, and reset-token validation all use canonical
identity values. Database uniqueness races are rolled back and exposed as
safe domain errors.

## Authorization

Inactive users have no authority. Only active admins can manage accounts or
roles. Active moderators can unpublish public decks and quizzes through the
dedicated moderation route; they cannot access account administration or
private-content mutation on behalf of another user. Public registration always
creates a standard user.

Account and moderation mutations emit structured audit events containing IDs,
resource types, outcome, and bounded metadata only. Passwords, reset tokens,
email addresses, and other secrets are excluded.

## Browser policy

The response CSP uses `style-src 'self'` with no `unsafe-inline`. Templates
contain no inline style attributes or style blocks. Styles are served from
content-versioned static CSS; script blocks retain per-response nonces.
