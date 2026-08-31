"""
A small local memory so repeat runs are fast.

Working out a song's language can involve a MusicBrainz lookup (one second
each) or an AI call (costs money). Doing that again for the same song every
single run would be wasteful, so the answers are written to a plain JSON file
in the cache/ folder.

What goes in the file:
  * classifications - song ID -> which languages we decided on, and why
  * artist_countries - artist name -> the country MusicBrainz reported

What NEVER goes in the file: your Client ID, Client Secret, or any Spotify
token. Those live elsewhere (.env and cache/spotify_token.json), and both are
excluded from Git. Deleting the cache file is always safe - the program will
simply work everything out again.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

CACHE_PATH = os.path.join("cache", "classifications.json")

# Anything that looks like a secret is refused entry, as a belt-and-braces
# guard against a future edit accidentally storing credentials here.
_FORBIDDEN_KEYS = (
    "token", "secret", "client_id", "clientid", "password", "refresh",
    "access", "api_key", "apikey", "authorization",
)


class Cache:
    def __init__(self, path: str = CACHE_PATH) -> None:
        self.path = path
        self.classifications: dict[str, Any] = {}
        self.artist_countries: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            # A corrupted cache is not worth crashing over - start fresh.
            print("   (The cache file was unreadable, so it is being rebuilt.)")
            return
        if isinstance(data, dict):
            self.classifications = dict(data.get("classifications") or {})
            self.artist_countries = dict(data.get("artist_countries") or {})

    def save(self) -> None:
        payload = {
            "classifications": self.classifications,
            "artist_countries": self.artist_countries,
        }
        _assert_no_secrets(payload)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        # Write to a temporary file first, then move it into place, so an
        # interrupted run can never leave a half-written cache behind.
        directory = os.path.dirname(self.path) or "."
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=directory, delete=False, suffix=".tmp"
        )
        try:
            with handle:
                json.dump(payload, handle, ensure_ascii=False, indent=1)
            os.replace(handle.name, self.path)
        except OSError as exc:
            print(f"   (Could not save the cache, continuing anyway: {exc})")
            try:
                os.unlink(handle.name)
            except OSError:
                pass


def _assert_no_secrets(payload: dict) -> None:
    """Refuse to write anything that looks like a credential."""

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = str(key).lower()
                if any(bad in lowered for bad in _FORBIDDEN_KEYS):
                    raise RuntimeError(
                        f"Refusing to write '{key}' to the cache file - it looks "
                        "like a secret. This is a bug; please report it."
                    )
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
