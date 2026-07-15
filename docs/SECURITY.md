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

Quiz co-authors may edit metadata and questions but cannot delete the quiz,
manage collaborators, or create/revoke unlisted links. Only the owner has those
permissions. Unlisted quiz tokens grant only their stored `view` or `copy`
capability and do not confer editing access. Quiz reports are visible only to
moderators and admins.

Account and moderation mutations emit structured audit events containing IDs,
resource types, outcome, and bounded metadata only. Passwords, reset tokens,
email addresses, and other secrets are excluded.

## Browser policy

The response CSP uses `style-src 'self'` with no `unsafe-inline`. Templates
contain no inline style attributes or style blocks. Styles are served from
content-versioned static CSS; script blocks retain per-response nonces.

Offline support is fail-closed for private data. Only successful anonymous
pages from an endpoint allowlist receive `X-Cards-Public: 1`, and the service
worker requires that response marker before caching. Authenticated HTML,
unlisted share links, account pages, and mutation responses remain `no-store`.

The unauthenticated `/api/v1` surface is read-only and filters every query to
public records. Public quiz detail omits options, accepted answers, correctness,
and explanations so the API cannot be used as an answer key. All endpoints are
bounded and rate limited.
