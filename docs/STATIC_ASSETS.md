# Static assets and cache invalidation

The app serves checked-in CSS and JavaScript through Flask's built-in static
handler. Templates call `asset_url(...)`, which appends the first 16
characters of a SHA-256 content hash as the `v` query parameter. For example,
the URL for `app.css` changes whenever its bytes change; no frontend build
system or release-specific environment variable is required.

Current versioned assets are cached for one year with:

```text
Cache-Control: public, max-age=31536000, immutable
```

Flask continues to provide the file ETag and conditional `304 Not Modified`
behavior. Direct or unversioned `/static/...` requests receive
`no-cache, must-revalidate`. A versioned URL with an old or invalid hash is
redirected to the current content-hash URL, preventing an origin response
from associating new bytes with an old immutable URL.

HTML responses are `no-store, private` because pages include a per-response
CSP nonce and may include CSRF tokens, authenticated navigation, or user data.
This prevents shared caches from serving one user's page or token to another
user.

## Deployment workflow

1. Change the asset files and run the audit:

   ```powershell
   .\.venv\Scripts\python.exe scripts/audit_static_assets.py
   ```

2. Deploy the same application release as usual. The first request after the
   deploy computes the new content hash and the rendered HTML references the
   new URL.

3. Do not rename or manually reuse an old `v` value for changed asset bytes.
   The application computes hashes from the deployed files and redirects stale
   versioned requests to the canonical URL.

The audit is intentionally small and dependency-free so it works locally,
under SQLite development, and on Heroku's Python runtime.
