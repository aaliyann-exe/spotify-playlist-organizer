"""
A pretend Spotify that the tests can talk to instead of the real thing.

It behaves like the real API in the ways that matter - it paginates in pages
of 100, it enforces the 100-track add limit, it returns removed songs as None
- and it writes down every change anyone makes to it, so a test can prove
that the source playlist was never touched.
"""

from __future__ import annotations

from spotipy.exceptions import SpotifyException


class FakeSpotify:
    def __init__(self, playlists: dict[str, dict], user_id: str = "testuser") -> None:
        # playlists: {id: {"name": str, "owner": str, "tracks": [track dicts]}}
        self.playlists = playlists
        self.user_id = user_id
        #: Every mutating call ever made, as (action, playlist_id, detail).
        self.mutations: list[tuple] = []
        self.artist_data: dict[str, list[str]] = {}

    # ------------------------------------------------------------- reading

    def current_user(self) -> dict:
        return {"id": self.user_id, "display_name": "Test User"}

    def current_user_playlists(self, limit: int = 50, offset: int = 0) -> dict:
        entries = [
            {
                "id": pid,
                "name": data["name"],
                "owner": {"id": data.get("owner", self.user_id)},
                "tracks": {"total": len(data["tracks"])},
            }
            for pid, data in self.playlists.items()
        ]
        page = entries[offset : offset + limit]
        has_more = offset + limit < len(entries)
        return {"items": page, "next": "more" if has_more else None, "total": len(entries)}

    def playlist(self, playlist_id: str, fields: str | None = None) -> dict:
        if playlist_id not in self.playlists:
            raise SpotifyException(404, -1, "Not found")
        return {"name": self.playlists[playlist_id]["name"]}

    def playlist_items(
        self,
        playlist_id: str,
        limit: int = 100,
        offset: int = 0,
        fields: str | None = None,
        additional_types=("track",),
    ) -> dict:
        if playlist_id not in self.playlists:
            raise SpotifyException(404, -1, "Not found")
        items = self.playlists[playlist_id]["tracks"]
        assert limit <= 100, "Spotify never allows more than 100 items per page"
        page = items[offset : offset + limit]
        has_more = offset + limit < len(items)
        return {"items": page, "next": "more" if has_more else None, "total": len(items)}

    def artists(self, artist_ids: list[str]) -> dict:
        assert len(artist_ids) <= 50, "Spotify never allows more than 50 artist IDs"
        return {
            "artists": [
                {"id": a, "name": a, "genres": self.artist_data.get(a, [])} for a in artist_ids
            ]
        }

    # ------------------------------------------------------------- writing

    def user_playlist_create(
        self, user: str, name: str, public: bool = True, description: str = ""
    ) -> dict:
        new_id = f"created_{len(self.playlists)}"
        self.playlists[new_id] = {"name": name, "owner": user, "tracks": []}
        self.mutations.append(("create", new_id, name))
        return {"id": new_id, "name": name, "owner": {"id": user}}

    def playlist_add_items(self, playlist_id: str, items: list[str]) -> dict:
        assert len(items) <= 100, "Spotify never allows more than 100 tracks per add"
        if playlist_id not in self.playlists:
            raise SpotifyException(404, -1, "Not found")
        for uri in items:
            track_id = uri.split(":")[-1]
            self.playlists[playlist_id]["tracks"].append(make_item(track_id, track_id))
        self.mutations.append(("add", playlist_id, list(items)))
        return {"snapshot_id": "x"}

    # These exist purely so a test can prove we never call them.
    def playlist_remove_all_occurrences_of_items(self, playlist_id, items):
        self.mutations.append(("remove", playlist_id, list(items)))

    def playlist_change_details(self, playlist_id, **kwargs):
        self.mutations.append(("change_details", playlist_id, kwargs))

    def playlist_reorder_items(self, playlist_id, **kwargs):
        self.mutations.append(("reorder", playlist_id, kwargs))

    # ------------------------------------------------------------- helpers

    def snapshot(self, playlist_id: str) -> dict:
        """A deep-ish copy used to prove a playlist is byte-for-byte unchanged."""
        data = self.playlists[playlist_id]
        return {
            "name": data["name"],
            "owner": data.get("owner"),
            "track_ids": [
                (i.get("track") or {}).get("id") for i in data["tracks"]
            ],
        }


def make_item(
    track_id: str,
    name: str,
    artists: list[tuple[str, str]] | None = None,
    album: str = "Some Album",
) -> dict:
    """Build one playlist entry in the shape Spotify returns."""
    artists = artists or [("artist1", "Test Artist")]
    return {
        "is_local": False,
        "track": {
            "id": track_id,
            "uri": f"spotify:track:{track_id}",
            "name": name,
            "type": "track",
            "is_local": False,
            "artists": [{"id": a_id, "name": a_name} for a_id, a_name in artists],
            "album": {"name": album},
        },
    }


def make_removed_item() -> dict:
    """A song that has been pulled from Spotify - the API returns a null track."""
    return {"is_local": False, "track": None}


def make_local_item(name: str = "My Local File") -> dict:
    return {
        "is_local": True,
        "track": {
            "id": None,
            "uri": f"spotify:local:::{name}:180",
            "name": name,
            "type": "track",
            "is_local": True,
            "artists": [{"id": None, "name": "Local"}],
            "album": {"name": ""},
        },
    }


def make_episode_item(track_id: str = "ep1") -> dict:
    return {
        "is_local": False,
        "track": {
            "id": track_id,
            "uri": f"spotify:episode:{track_id}",
            "name": "A Podcast Episode",
            "type": "episode",
            "is_local": False,
            "artists": [],
            "album": {"name": ""},
        },
    }
