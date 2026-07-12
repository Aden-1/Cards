"""Helpers for content-versioned static asset URLs and cache policy."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from flask import current_app, url_for
from werkzeug.utils import safe_join


def _asset_path(filename: str) -> Path:
    static_folder = current_app.static_folder
    if not static_folder:
        raise FileNotFoundError(f"Static asset folder is not configured: {filename}")

    joined_path = safe_join(static_folder, filename)
    if not joined_path:
        raise FileNotFoundError(f"Static asset path is invalid: {filename}")

    path = Path(joined_path)
    if not path.is_file():
        raise FileNotFoundError(f"Static asset does not exist: {filename}")
    return path


def asset_version(filename: str) -> str:
    """Return a short content hash for a checked-in static asset.

    The cache is invalidated when the file's size or nanosecond mtime changes,
    which keeps development responsive without making every template render
    reread the asset. The hash is derived from bytes, so a deploy that changes
    an asset always produces a new URL even if the release number is unchanged.
    """
    path = _asset_path(filename)
    signature = (path.stat().st_size, path.stat().st_mtime_ns)
    cache = current_app.extensions.setdefault("cards_static_asset_versions", {})
    cached = cache.get(filename)
    if cached and cached[0] == signature:
        return cached[1]

    digest = sha256(path.read_bytes()).hexdigest()[:16]
    cache[filename] = (signature, digest)
    return digest


def asset_url(filename: str) -> str:
    """Build the canonical URL for a content-versioned static asset."""
    return url_for("static", filename=filename, v=asset_version(filename))


def is_current_asset_version(filename: str, version: str | None) -> bool:
    """Return whether a static request carries the current content hash."""
    return bool(version) and version == asset_version(filename)
