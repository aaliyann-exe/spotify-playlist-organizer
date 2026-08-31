"""
Everything that talks to Spotify lives in this file.

The most important thing in here is the SOURCE PLAYLIST GUARD. The source
playlist you pick is treated as strictly read-only: every single method that
could change something on Spotify refuses to run if it is pointed at the
source playlist, and refuses to run at all in dry-run mode. There is no way
to reach the Spotify write endpoints except through those guarded methods.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth, SpotifyPKCE, SpotifyOauthError
from spotipy.cache_handler import CacheFileHandler

# Where the OAuth token gets stored so you don't log in every single time.
# This file holds tokens, so .gitignore excludes it from Git.
TOKEN_CACHE_PATH = os.path.join("cache", "spotify_token.json")

# The smallest set of permissions that can do the job:
#   playlist-read-private        -> see and read your own playlists
#   playlist-read-collaborative  -> read collaborative playlists you're in
#   playlist-modify-private      -> create/add to the private destination playlists
#   playlist-modify-public       -> only needed if a destination playlist is public
SCOPES = (
    "playlist-read-private "
    "playlist-read-collaborative "
    "playlist-modify-private "
    "playlist-modify-public"
)

# Spotify's own hard limits.
_PAGE_SIZE = 100          # max items per playlist page
_ARTIST_BATCH = 50        # max artist IDs per /artists request
_ADD_BATCH = 100          # max tracks per "add to playlist" request


class FriendlyError(Exception):
    """An error we already translated into plain English for the user."""


@dataclass
class Track:
    """One song, reduced to just the bits we actually need."""

    track_id: str
    uri: str
    name: str
    artist_names: list[str] = field(default_factory=list)
    artist_ids: list[str] = field(default_factory=list)
    album_name: str = ""

    @property
    def display(self) -> str:
        artists = ", ".join(self.artist_names) if self.artist_names else "Unknown artist"
        return f"{self.name} - {artists}"


def extract_playlist_id(value: str) -> str:
    """Accept a raw ID, a spotify: URI, or a web URL and return the bare ID."""
    value = (value or "").strip()
    if not value:
        raise FriendlyError("No playlist was given.")
    match = re.search(r"playlist[:/]([A-Za-z0-9]+)", value)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9]{22}", value):
        return value
    raise FriendlyError(
        f"'{value}' does not look like a Spotify playlist. Paste the playlist "
        "link from Spotify (Share -> Copy link to playlist), or pick a number "
        "from the list instead."
    )


def _retry(call: Callable[[], Any], *, what: str, attempts: int = 4) -> Any:
    """
    Run a Spotify call, retrying the failures that are worth retrying.

    Spotify sends HTTP 429 when we're going too fast, and tells us how many
    seconds to wait in the Retry-After header. 5xx means Spotify itself is
    having a moment. Everything else is a real error and is raised straight away.
    """
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return call()
        except SpotifyException as exc:
            last_error = exc
            if exc.http_status == 429:
                wait = 2
                if exc.headers:
                    try:
                        wait = int(exc.headers.get("Retry-After", 2))
                    except (TypeError, ValueError):
                        wait = 2
                wait = min(max(wait, 1), 60)
                print(f"   (Spotify asked us to slow down; waiting {wait}s...)")
                time.sleep(wait)
                continue
            if exc.http_status and exc.http_status >= 500:
                time.sleep(2 ** attempt)
                continue
            raise
        except (ConnectionError, TimeoutError, OSError) as exc:
            last_error = exc
            time.sleep(2 ** attempt)
            continue
    raise FriendlyError(
        f"Could not {what} after several tries. This is usually a temporary "
        f"internet or Spotify problem - please try again in a minute.\n"
        f"Technical detail: {last_error}"
    )


def build_spotify(open_browser: bool = True) -> spotipy.Spotify:
    """
    Log in to Spotify using the official OAuth flow.

    Your password never touches this program: Spotify opens in your browser,
    you approve there, and Spotify hands us back a token.
    """
    client_id = (os.getenv("SPOTIPY_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("SPOTIPY_CLIENT_SECRET") or "").strip()
    redirect_uri = (os.getenv("SPOTIPY_REDIRECT_URI") or "").strip()

    if not client_id or client_id.startswith("paste_your"):
        raise FriendlyError(
            "SPOTIPY_CLIENT_ID is missing.\n\n"
            "Fix: open the file named '.env' in this folder and put your Client ID\n"
            "after 'SPOTIPY_CLIENT_ID='. See the Setup section of README.md."
        )
    if not redirect_uri:
        raise FriendlyError(
            "SPOTIPY_REDIRECT_URI is missing.\n\n"
            "Fix: add this line to your .env file:\n"
            "SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback"
        )

    os.makedirs(os.path.dirname(TOKEN_CACHE_PATH), exist_ok=True)
    cache_handler = CacheFileHandler(cache_path=TOKEN_CACHE_PATH)

    # If no secret is provided we use PKCE, which is designed exactly for
    # apps like this one that cannot keep a secret safe. Both are official.
    if client_secret and not client_secret.startswith("paste_your"):
        auth_manager = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=SCOPES,
            cache_handler=cache_handler,
            open_browser=open_browser,
        )
    else:
        auth_manager = SpotifyPKCE(
            client_id=client_id,
            redirect_uri=redirect_uri,
            scope=SCOPES,
            cache_handler=cache_handler,
            open_browser=open_browser,
        )

    try:
        sp = spotipy.Spotify(auth_manager=auth_manager, requests_timeout=30, retries=0)
        sp.current_user()  # forces the login to actually happen now
        return sp
    except SpotifyOauthError as exc:
        raise FriendlyError(
            "Spotify would not let us log in.\n\n"
            "The two usual causes are:\n"
            "  1. The Client ID or Client Secret in your .env file has a typo.\n"
            "  2. The Redirect URI in your Spotify app settings is not EXACTLY\n"
            "     http://127.0.0.1:8888/callback\n\n"
            f"Technical detail: {exc}"
        ) from exc
    except SpotifyException as exc:
        if exc.http_status in (401, 403):
            raise FriendlyError(
                "Spotify rejected our access token.\n\n"
                f"Fix: delete the file '{TOKEN_CACHE_PATH}' and run the program "
                "again to log in fresh.\n\n"
                f"Technical detail: {exc}"
            ) from exc
        raise FriendlyError(f"Spotify returned an error while logging in: {exc}") from exc


class SpotifyGateway:
    """
    A thin, safety-checked wrapper around the Spotify client.

    Two safety rails are built into this class and cannot be bypassed:

      1. `source_playlist_id` - once set, no write method will touch it.
      2. `dry_run` - when True, every write method refuses to call Spotify.
    """

    def __init__(self, sp: spotipy.Spotify, dry_run: bool = False) -> None:
        self._sp = sp
        self.dry_run = dry_run
        self.source_playlist_id: str | None = None
        self._artist_genre_cache: dict[str, list[str]] = {}
        self._playlists_cache: list[dict] | None = None

    # ---------------------------------------------------------------- safety

    def mark_source_playlist(self, playlist_id: str) -> None:
        """Lock in which playlist is the untouchable source."""
        self.source_playlist_id = playlist_id

    def _assert_writable(self, playlist_id: str, action: str) -> None:
        """The single choke point every write goes through."""
        if self.source_playlist_id and playlist_id == self.source_playlist_id:
            raise FriendlyError(
                "SAFETY STOP: the program tried to "
                f"{action} the SOURCE playlist. The source playlist is read-only "
                "and must never be changed. Nothing was modified. This is a bug - "
                "please report it."
            )
        if self.dry_run:
            raise FriendlyError(
                f"SAFETY STOP: tried to {action} while in dry-run mode. "
                "Nothing was modified. This is a bug - please report it."
            )

    # ----------------------------------------------------------------- reads

    def current_user_id(self) -> str:
        user = _retry(lambda: self._sp.current_user(), what="read your Spotify profile")
        return user["id"]

    def list_my_playlists(self, refresh: bool = False) -> list[dict]:
        """
        Every playlist you own or follow, following Spotify's pagination.

        The answer is remembered for the rest of the run, because we look up
        all four destination playlists in a row and there is no point in
        downloading the same list four times.
        """
        if self._playlists_cache is not None and not refresh:
            return self._playlists_cache
        playlists: list[dict] = []
        offset = 0
        while True:
            page = _retry(
                lambda o=offset: self._sp.current_user_playlists(limit=50, offset=o),
                what="list your playlists",
            )
            items = [p for p in (page.get("items") or []) if p]
            playlists.extend(items)
            if len(items) < 50 or page.get("next") is None:
                break
            offset += 50
        self._playlists_cache = playlists
        return playlists

    def get_playlist_name(self, playlist_id: str) -> str:
        data = _retry(
            lambda: self._sp.playlist(playlist_id, fields="name"),
            what="read that playlist",
        )
        return data.get("name", "(unnamed playlist)")

    def fetch_all_tracks(
        self, playlist_id: str, progress: bool = True
    ) -> tuple[list[Track], list[str]]:
        """
        Read every track in a playlist, 100 at a time.

        This is a pure read - it never modifies the playlist. Returns the
        usable tracks plus a list of human-readable notes about anything we
        had to skip (removed songs, local files, podcast episodes).
        """
        tracks: list[Track] = []
        skipped: list[str] = []
        seen_ids: set[str] = set()
        offset = 0
        total: int | None = None

        fields = (
            "total,next,items(is_local,track("
            "id,uri,name,type,is_local,artists(id,name),album(name)))"
        )

        while True:
            try:
                page = _retry(
                    lambda o=offset: self._sp.playlist_items(
                        playlist_id,
                        limit=_PAGE_SIZE,
                        offset=o,
                        fields=fields,
                        additional_types=("track",),
                    ),
                    what="read the songs in that playlist",
                )
            except SpotifyException as exc:
                if exc.http_status == 404:
                    raise FriendlyError(
                        "That playlist could not be found. It may have been "
                        "deleted, or the link may be wrong."
                    ) from exc
                raise

            if total is None:
                total = page.get("total", 0) or 0
                if total == 0:
                    return [], ["That playlist is empty - there is nothing to sort."]

            items = page.get("items") or []
            for item in items:
                note = _note_for_unusable(item)
                if note:
                    skipped.append(note)
                    continue
                raw = item["track"]
                if raw["id"] in seen_ids:
                    continue  # the same song listed twice in the source
                seen_ids.add(raw["id"])
                tracks.append(
                    Track(
                        track_id=raw["id"],
                        uri=raw["uri"],
                        name=raw.get("name") or "(untitled)",
                        artist_names=[
                            a["name"] for a in (raw.get("artists") or []) if a and a.get("name")
                        ],
                        artist_ids=[
                            a["id"] for a in (raw.get("artists") or []) if a and a.get("id")
                        ],
                        album_name=((raw.get("album") or {}).get("name") or ""),
                    )
                )

            if progress:
                print(f"   read {min(offset + len(items), total)} of {total} items...")

            if not items or page.get("next") is None:
                break
            offset += _PAGE_SIZE

        return tracks, skipped

    def artist_genres(self, artist_ids: Iterable[str]) -> dict[str, list[str]]:
        """
        Look up the genre tags Spotify assigns to artists, 50 at a time.

        Genres like 'j-pop' or 'pakistani pop' are one of our strongest and
        completely free language signals.
        """
        wanted = [
            a for a in dict.fromkeys(artist_ids) if a and a not in self._artist_genre_cache
        ]
        for i in range(0, len(wanted), _ARTIST_BATCH):
            batch = wanted[i : i + _ARTIST_BATCH]
            try:
                result = _retry(
                    lambda b=batch: self._sp.artists(b), what="look up artist details"
                )
            except (SpotifyException, FriendlyError):
                # Genres are a bonus signal - never let this break the run.
                for artist_id in batch:
                    self._artist_genre_cache.setdefault(artist_id, [])
                continue
            for artist in result.get("artists") or []:
                if artist and artist.get("id"):
                    self._artist_genre_cache[artist["id"]] = [
                        g.lower() for g in (artist.get("genres") or [])
                    ]
            for artist_id in batch:
                self._artist_genre_cache.setdefault(artist_id, [])
        return self._artist_genre_cache

    def playlist_track_ids(self, playlist_id: str) -> set[str]:
        """Which songs are already in a destination playlist (so we never duplicate)."""
        existing: set[str] = set()
        offset = 0
        while True:
            page = _retry(
                lambda o=offset: self._sp.playlist_items(
                    playlist_id,
                    limit=_PAGE_SIZE,
                    offset=o,
                    fields="next,items(track(id))",
                    additional_types=("track",),
                ),
                what="check what is already in a destination playlist",
            )
            items = page.get("items") or []
            for item in items:
                track = (item or {}).get("track") or {}
                if track.get("id"):
                    existing.add(track["id"])
            if not items or page.get("next") is None:
                break
            offset += _PAGE_SIZE
        return existing

    def find_playlist_by_name(self, name: str, owner_id: str) -> dict | None:
        """Find an existing destination playlist so we never create a duplicate."""
        for playlist in self.list_my_playlists():
            if not playlist:
                continue
            same_name = (playlist.get("name") or "").strip() == name
            mine = ((playlist.get("owner") or {}).get("id")) == owner_id
            if same_name and mine:
                return playlist
        return None

    # ---------------------------------------------------------------- writes

    def create_playlist(self, user_id: str, name: str, description: str) -> dict:
        if self.dry_run:
            raise FriendlyError(
                "SAFETY STOP: tried to create a playlist in dry-run mode. "
                "Nothing was modified. This is a bug - please report it."
            )
        created = _retry(
            lambda: self._sp.user_playlist_create(
                user=user_id, name=name, public=False, description=description
            ),
            what="create a playlist",
        )
        self._playlists_cache = None  # the list we remembered is now out of date
        return created

    def add_tracks(self, playlist_id: str, uris: list[str]) -> int:
        """Add songs to a DESTINATION playlist, 100 at a time. Never removes anything."""
        self._assert_writable(playlist_id, "add songs to")
        added = 0
        for i in range(0, len(uris), _ADD_BATCH):
            batch = uris[i : i + _ADD_BATCH]
            _retry(
                lambda b=batch: self._sp.playlist_add_items(playlist_id, b),
                what="add songs to a destination playlist",
            )
            added += len(batch)
        return added


def _note_for_unusable(item: dict | None) -> str | None:
    """Return a friendly reason string if this playlist entry cannot be sorted."""
    if not item:
        return "Skipped one empty playlist entry."
    track = item.get("track")
    if not track:
        return "Skipped a song that is no longer available on Spotify."
    if item.get("is_local") or track.get("is_local"):
        name = track.get("name") or "a local file"
        return f"Skipped '{name}' because it is a local file, not a Spotify track."
    if track.get("type") and track.get("type") != "track":
        kind = track["type"]
        return f"Skipped '{track.get('name', 'an item')}' because it is a {kind}, not a song."
    if not track.get("id") or not track.get("uri"):
        return f"Skipped '{track.get('name', 'a song')}' because Spotify gave it no ID."
    return None
