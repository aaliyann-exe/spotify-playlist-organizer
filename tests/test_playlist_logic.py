"""
Tests for the playlist side of things.

The most important tests in this file are the ones that prove the source
playlist is never modified and that dry-run mode cannot write to Spotify.
"""

from __future__ import annotations

import copy

import pytest

import classifier as lang
import spotify_sorter
from cache_store import Cache
from classifier import Classification
from spotify_client import FriendlyError, SpotifyGateway, Track, extract_playlist_id
from tests.fake_spotify import (
    FakeSpotify,
    make_episode_item,
    make_item,
    make_local_item,
    make_removed_item,
)

SOURCE = "source_playlist_id"


def build(tracks=None, extra_playlists=None):
    playlists = {
        SOURCE: {"name": "My Big Playlist", "owner": "testuser", "tracks": tracks or []},
    }
    playlists.update(extra_playlists or {})
    return FakeSpotify(playlists)


# ------------------------------------------------------ the source is sacred


def test_gateway_refuses_to_add_songs_to_the_source():
    gateway = SpotifyGateway(build())
    gateway.mark_source_playlist(SOURCE)
    with pytest.raises(FriendlyError, match="SAFETY STOP"):
        gateway.add_tracks(SOURCE, ["spotify:track:abc"])


def test_reading_the_source_leaves_it_completely_unchanged():
    items = [make_item(f"t{i}", f"Song {i}") for i in range(250)]
    fake = build(items)
    before = fake.snapshot(SOURCE)
    original = copy.deepcopy(fake.playlists[SOURCE])

    gateway = SpotifyGateway(fake)
    gateway.mark_source_playlist(SOURCE)
    gateway.fetch_all_tracks(SOURCE, progress=False)

    assert fake.snapshot(SOURCE) == before
    assert fake.playlists[SOURCE] == original
    assert fake.mutations == [], "reading must not cause a single mutation"


def test_a_whole_normal_run_never_mutates_the_source():
    """End to end: classify, plan, apply - and the source is still identical."""
    items = [
        make_item("t1", "Blinding Lights", [("a1", "The Weeknd")]),
        make_item("t2", "アイドル", [("a2", "YOASOBI")]),
        make_item("t3", "Tu Hai Kahan", [("a3", "AUR")]),
    ]
    fake = build(items)
    before = copy.deepcopy(fake.playlists[SOURCE])

    gateway = SpotifyGateway(fake)
    gateway.mark_source_playlist(SOURCE)
    tracks, _ = gateway.fetch_all_tracks(SOURCE, progress=False)

    results = {
        "t1": Classification(languages=[lang.ENGLISH]),
        "t2": Classification(languages=[lang.JAPANESE]),
        "t3": Classification(languages=[]),
    }
    buckets = spotify_sorter.group_by_destination(tracks, results)
    plan = spotify_sorter.plan_changes(gateway, "testuser", buckets)
    spotify_sorter.apply_changes(gateway, "testuser", plan)

    assert fake.playlists[SOURCE] == before
    touched = {pid for _, pid, _ in fake.mutations}
    assert SOURCE not in touched
    assert not any(action == "remove" for action, _, _ in fake.mutations)
    assert not any(action == "change_details" for action, _, _ in fake.mutations)
    assert not any(action == "reorder" for action, _, _ in fake.mutations)


def test_a_destination_named_like_the_source_is_refused():
    fake = build([], {"other": {"name": "Language - English", "owner": "testuser", "tracks": []}})
    fake.playlists[SOURCE]["name"] = "Language - English"
    gateway = SpotifyGateway(fake)
    gateway.mark_source_playlist(SOURCE)
    # Make find_playlist_by_name return the source itself.
    del fake.playlists["other"]
    with pytest.raises(FriendlyError, match="SAFETY STOP"):
        spotify_sorter.plan_changes(gateway, "testuser", {k: [] for k in spotify_sorter.DESTINATIONS})


# ----------------------------------------------------------------- dry run


def test_dry_run_cannot_create_a_playlist():
    gateway = SpotifyGateway(build(), dry_run=True)
    with pytest.raises(FriendlyError, match="SAFETY STOP"):
        gateway.create_playlist("testuser", "Language - English", "")


def test_dry_run_cannot_add_songs_anywhere():
    fake = build([], {"dest": {"name": "Language - English", "owner": "testuser", "tracks": []}})
    gateway = SpotifyGateway(fake, dry_run=True)
    gateway.mark_source_playlist(SOURCE)
    with pytest.raises(FriendlyError, match="SAFETY STOP"):
        gateway.add_tracks("dest", ["spotify:track:abc"])
    assert fake.mutations == []


def test_dry_run_planning_makes_no_changes():
    items = [make_item("t1", "Blinding Lights")]
    fake = build(items)
    gateway = SpotifyGateway(fake, dry_run=True)
    gateway.mark_source_playlist(SOURCE)
    tracks, _ = gateway.fetch_all_tracks(SOURCE, progress=False)
    buckets = spotify_sorter.group_by_destination(
        tracks, {"t1": Classification(languages=[lang.ENGLISH])}
    )
    plan = spotify_sorter.plan_changes(gateway, "testuser", buckets)
    assert fake.mutations == []
    assert sum(len(e["to_add"]) for e in plan) == 1  # it planned, it just didn't act


# --------------------------------------------------------------- paginating


def test_reads_a_playlist_of_more_than_300_songs():
    items = [make_item(f"t{i}", f"Song {i}") for i in range(327)]
    gateway = SpotifyGateway(build(items))
    tracks, skipped = gateway.fetch_all_tracks(SOURCE, progress=False)
    assert len(tracks) == 327
    assert skipped == []


def test_reads_an_exact_multiple_of_the_page_size():
    items = [make_item(f"t{i}", f"Song {i}") for i in range(300)]
    gateway = SpotifyGateway(build(items))
    tracks, _ = gateway.fetch_all_tracks(SOURCE, progress=False)
    assert len(tracks) == 300


def test_empty_playlist_is_handled_kindly():
    gateway = SpotifyGateway(build([]))
    tracks, notes = gateway.fetch_all_tracks(SOURCE, progress=False)
    assert tracks == []
    assert any("empty" in n.lower() for n in notes)


def test_missing_playlist_gives_a_friendly_error():
    gateway = SpotifyGateway(build())
    with pytest.raises(FriendlyError, match="could not be found"):
        gateway.fetch_all_tracks("does_not_exist", progress=False)


def test_artist_lookups_are_batched_in_fifties():
    fake = build()
    gateway = SpotifyGateway(fake)
    genres = gateway.artist_genres([f"artist{i}" for i in range(120)])
    assert len(genres) == 120  # the fake asserts the batch size for us


# ------------------------------------------------------------- odd entries


def test_removed_local_and_podcast_entries_are_skipped_with_a_reason():
    items = [
        make_item("t1", "A Real Song"),
        make_removed_item(),
        make_local_item(),
        make_episode_item(),
    ]
    gateway = SpotifyGateway(build(items))
    tracks, notes = gateway.fetch_all_tracks(SOURCE, progress=False)
    assert [t.track_id for t in tracks] == ["t1"]
    assert len(notes) == 3
    assert any("no longer available" in n for n in notes)
    assert any("local file" in n for n in notes)
    assert any("episode" in n for n in notes)


def test_a_song_listed_twice_in_the_source_is_read_once():
    items = [make_item("t1", "Song"), make_item("t1", "Song"), make_item("t2", "Other")]
    gateway = SpotifyGateway(build(items))
    tracks, _ = gateway.fetch_all_tracks(SOURCE, progress=False)
    assert [t.track_id for t in tracks] == ["t1", "t2"]


def test_missing_metadata_does_not_crash():
    item = make_item("t1", "Song")
    item["track"]["album"] = None
    item["track"]["artists"] = []
    item["track"]["name"] = None
    gateway = SpotifyGateway(build([item]))
    tracks, _ = gateway.fetch_all_tracks(SOURCE, progress=False)
    assert tracks[0].name == "(untitled)"
    assert tracks[0].artist_names == []


# ------------------------------------------------------------ no duplicates


def test_songs_already_in_a_destination_are_not_added_again():
    existing = {
        "dest_en": {
            "name": "Language - English",
            "owner": "testuser",
            "tracks": [make_item("t1", "Blinding Lights")],
        }
    }
    fake = build([make_item("t1", "Blinding Lights"), make_item("t2", "Levitating")], existing)
    gateway = SpotifyGateway(fake)
    gateway.mark_source_playlist(SOURCE)
    tracks, _ = gateway.fetch_all_tracks(SOURCE, progress=False)
    buckets = spotify_sorter.group_by_destination(
        tracks,
        {
            "t1": Classification(languages=[lang.ENGLISH]),
            "t2": Classification(languages=[lang.ENGLISH]),
        },
    )
    plan = spotify_sorter.plan_changes(gateway, "testuser", buckets)
    english = next(e for e in plan if e["key"] == lang.ENGLISH)
    assert [t.track_id for t in english["to_add"]] == ["t2"]


def test_running_twice_adds_nothing_the_second_time():
    """This is the 'idempotent' requirement: a repeat run is a no-op."""
    fake = build([make_item("t1", "Blinding Lights"), make_item("t2", "Levitating")])
    gateway = SpotifyGateway(fake)
    gateway.mark_source_playlist(SOURCE)
    tracks, _ = gateway.fetch_all_tracks(SOURCE, progress=False)
    results = {
        "t1": Classification(languages=[lang.ENGLISH]),
        "t2": Classification(languages=[lang.ENGLISH]),
    }
    buckets = spotify_sorter.group_by_destination(tracks, results)

    plan = spotify_sorter.plan_changes(gateway, "testuser", buckets)
    spotify_sorter.apply_changes(gateway, "testuser", plan)
    first_run_adds = sum(len(e["to_add"]) for e in plan)

    plan2 = spotify_sorter.plan_changes(gateway, "testuser", buckets)
    assert first_run_adds == 2
    assert sum(len(e["to_add"]) for e in plan2) == 0


def test_the_same_song_twice_in_one_bucket_is_added_once():
    track = Track("t1", "spotify:track:t1", "Song", ["A"], ["a1"], "Album")
    fake = build()
    gateway = SpotifyGateway(fake)
    gateway.mark_source_playlist(SOURCE)
    buckets = {k: [] for k in spotify_sorter.DESTINATIONS}
    buckets[lang.ENGLISH] = [track, track]
    plan = spotify_sorter.plan_changes(gateway, "testuser", buckets)
    english = next(e for e in plan if e["key"] == lang.ENGLISH)
    assert len(english["to_add"]) == 1


def test_adding_more_than_100_songs_is_split_into_batches():
    fake = build([], {"dest": {"name": "Language - English", "owner": "testuser", "tracks": []}})
    gateway = SpotifyGateway(fake)
    gateway.mark_source_playlist(SOURCE)
    uris = [f"spotify:track:t{i}" for i in range(250)]
    added = gateway.add_tracks("dest", uris)
    assert added == 250
    assert len([m for m in fake.mutations if m[0] == "add"]) == 3


# ------------------------------------------------------- multiple playlists


def test_a_bilingual_song_goes_into_both_playlists():
    fake = build([make_item("t1", "Love You Zindagi")])
    gateway = SpotifyGateway(fake)
    gateway.mark_source_playlist(SOURCE)
    tracks, _ = gateway.fetch_all_tracks(SOURCE, progress=False)
    buckets = spotify_sorter.group_by_destination(
        tracks, {"t1": Classification(languages=[lang.ENGLISH, lang.URDU])}
    )
    assert [t.track_id for t in buckets[lang.ENGLISH]] == ["t1"]
    assert [t.track_id for t in buckets[lang.URDU]] == ["t1"]
    assert buckets[lang.UNKNOWN] == []
    assert buckets[lang.JAPANESE] == []


def test_an_unclassified_song_goes_to_unknown_only():
    fake = build([make_item("t1", "?????")])
    gateway = SpotifyGateway(fake)
    tracks, _ = gateway.fetch_all_tracks(SOURCE, progress=False)
    buckets = spotify_sorter.group_by_destination(tracks, {"t1": Classification(languages=[])})
    assert [t.track_id for t in buckets[lang.UNKNOWN]] == ["t1"]
    assert buckets[lang.ENGLISH] == []


def test_an_empty_destination_playlist_is_not_created():
    fake = build()
    gateway = SpotifyGateway(fake)
    gateway.mark_source_playlist(SOURCE)
    buckets = {k: [] for k in spotify_sorter.DESTINATIONS}
    plan = spotify_sorter.plan_changes(gateway, "testuser", buckets)
    spotify_sorter.apply_changes(gateway, "testuser", plan)
    assert fake.mutations == []


def test_an_existing_destination_is_reused_not_duplicated():
    existing = {
        "dest_en": {"name": "Language - English", "owner": "testuser", "tracks": []}
    }
    fake = build([make_item("t1", "Song")], existing)
    gateway = SpotifyGateway(fake)
    gateway.mark_source_playlist(SOURCE)
    tracks, _ = gateway.fetch_all_tracks(SOURCE, progress=False)
    buckets = spotify_sorter.group_by_destination(
        tracks, {"t1": Classification(languages=[lang.ENGLISH])}
    )
    plan = spotify_sorter.plan_changes(gateway, "testuser", buckets)
    spotify_sorter.apply_changes(gateway, "testuser", plan)
    created = [m for m in fake.mutations if m[0] == "create"]
    assert all(name != "Language - English" for _, _, name in created)


def test_someone_elses_playlist_with_the_same_name_is_ignored():
    others = {
        "theirs": {"name": "Language - English", "owner": "someone_else", "tracks": []}
    }
    fake = build([], others)
    gateway = SpotifyGateway(fake)
    assert gateway.find_playlist_by_name("Language - English", "testuser") is None


# ------------------------------------------------------------ playlist IDs


@pytest.mark.parametrize(
    "value",
    [
        "37i9dQZF1DXcBWIGoYBM5M",
        "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M",
        "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
        "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc123",
    ],
)
def test_playlist_links_of_every_shape_are_understood(value):
    assert extract_playlist_id(value) == "37i9dQZF1DXcBWIGoYBM5M"


@pytest.mark.parametrize("value", ["", "   ", "not a playlist", "https://example.com"])
def test_a_bad_playlist_link_gives_a_friendly_error(value):
    with pytest.raises(FriendlyError):
        extract_playlist_id(value)


# ------------------------------------------------------- command line flags


def test_default_run_is_not_a_dry_run():
    args = spotify_sorter.build_argument_parser().parse_args([])
    assert args.dry_run is False
    assert args.yes is False
    assert args.use_ai is False


def test_dry_run_flag_is_understood():
    args = spotify_sorter.build_argument_parser().parse_args(["--dry-run"])
    assert args.dry_run is True


def test_all_flags_together():
    args = spotify_sorter.build_argument_parser().parse_args(
        ["--dry-run", "--source", "abc", "--yes", "--no-musicbrainz", "--use-ai",
         "--no-cache", "--show-reasons"]
    )
    assert (args.dry_run, args.source, args.yes) == (True, "abc", True)
    assert (args.no_musicbrainz, args.use_ai, args.no_cache, args.show_reasons) == (
        True, True, True, True,
    )


def test_an_unknown_flag_is_rejected():
    with pytest.raises(SystemExit):
        spotify_sorter.build_argument_parser().parse_args(["--delete-everything"])


def test_there_are_exactly_four_destinations():
    assert len(spotify_sorter.DESTINATIONS) == 4
    assert set(spotify_sorter.DESTINATIONS.values()) == {
        "Language - English",
        "Language - Urdu",
        "Language - Japanese",
        "Language - Unknown",
    }


# ----------------------------------------------------------------- caching


def test_cache_round_trips_through_a_file(tmp_path):
    path = tmp_path / "c.json"
    cache = Cache(str(path))
    cache.classifications["t1"] = Classification(languages=[lang.ENGLISH]).to_json()
    cache.artist_countries["yoasobi"] = "JP"
    cache.save()

    reloaded = Cache(str(path))
    assert reloaded.artist_countries["yoasobi"] == "JP"
    assert Classification.from_json(reloaded.classifications["t1"]).languages == [lang.ENGLISH]


def test_cache_refuses_to_store_anything_that_looks_like_a_secret(tmp_path):
    cache = Cache(str(tmp_path / "c.json"))
    cache.classifications["oops"] = {"access_token": "shhh"}
    with pytest.raises(RuntimeError, match="looks like a secret"):
        cache.save()


def test_a_corrupted_cache_file_does_not_crash(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{ this is not json", encoding="utf-8")
    cache = Cache(str(path))
    assert cache.classifications == {}
