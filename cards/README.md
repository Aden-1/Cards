# Application package

The repository-root `app.py` is intentionally the only web application
entrypoint. The implementation is grouped here by responsibility:

- Core modules (`config.py`, `extensions.py`, `database.py`, `models.py`) own
  configuration, Flask extensions, database safety, and ORM definitions.
- `routes.py`, `api_contract.py`, and `static_assets.py` own HTTP behavior,
  request/response rules, and browser asset delivery.
- `services/` contains request-independent domain operations.
- `workers/` contains queue jobs and the password-reset worker.
- `operations/` contains explicit administrative or repair commands.
- `content/` contains reviewed seed data, separate from production logic.

Root-level modules such as `models.py` and `services/` are intentionally thin
compatibility imports for existing callers. New code should import from this
package directly.
