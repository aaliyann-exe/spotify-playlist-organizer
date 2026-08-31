"""
Spotify Language Sorter
=======================

Reads ONE playlist of yours and copies each song into up to four other
playlists depending on the language it is sung in:

    Language - English
    Language - Urdu
    Language - Japanese
    Language - Unknown

Run it like this:

    python spotify_sorter.py --dry-run     (look, change nothing)
    python spotify_sorter.py               (look, ask, then change)

THE PLAYLIST YOU PICK IS NEVER MODIFIED. Nothing is added to it, nothing is
removed from it, and its name, description and settings are left alone. The
program only ever reads it. See spotify_client.py for the guard that enforces
this.
"""

from __future__ import annotations

import argparse
import sys
import textwrap

# Make sure Japanese and Urdu titles can be printed on a Windows terminal
# without crashing the program.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from dotenv import load_dotenv

import classifier as lang
from cache_store import Cache
from classifier import (
    ClaudeClassifier,
    Classification,
    LanguageClassifier,
    MusicBrainzLookup,
)
from spotify_client import (
    FriendlyError,
    SpotifyGateway,
    build_spotify,
    extract_playlist_id,
)

#: The four destination playlists, and which classification fills each one.
DESTINATIONS = {
    lang.ENGLISH: "Language - English",
    lang.URDU: "Language - Urdu",
    lang.JAPANESE: "Language - Japanese",
    lang.UNKNOWN: "Language - Unknown",
}

DESTINATION_DESCRIPTION = (
    "Created automatically by Spotify Language Sorter. Songs are copied here; "
    "the original playlist is never changed."
)

RULE = "-" * 66


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python spotify_sorter.py",
        description="Sort one Spotify playlist into four playlists by language.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """
            Examples:
              python spotify_sorter.py --dry-run
                  Read everything and show what WOULD happen. Changes nothing.

              python spotify_sorter.py
                  The normal run. Shows a plan and asks before changing anything.

              python spotify_sorter.py --source https://open.spotify.com/playlist/xxxx
                  Skip the menu and use that playlist as the source.
            """
        ).strip(),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read and classify everything, then stop. Makes ZERO changes to Spotify.",
    )
    parser.add_argument(
        "--source",
        metavar="PLAYLIST",
        help="Source playlist link, URI or ID. Without this you get a menu.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Answer yes to the confirmation prompt. Ignored during --dry-run.",
    )
    parser.add_argument(
        "--no-musicbrainz",
        action="store_true",
        help="Do not look artists up on MusicBrainz. Faster, but more Unknowns.",
    )
    parser.add_argument(
        "--use-ai",
        action="store_true",
        help="Also ask Claude about songs nothing else could identify. Costs money "
        "and needs ANTHROPIC_API_KEY in your .env file.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore previously saved results and classify every song again.",
    )
    parser.add_argument(
        "--show-reasons",
        action="store_true",
        help="Print why each song was put where it was. Useful for checking accuracy.",
    )
    return parser


# --------------------------------------------------------------------------
# Choosing the source playlist
# --------------------------------------------------------------------------


def choose_source_playlist(gateway: SpotifyGateway) -> tuple[str, str]:
    """Show a numbered menu and let the user pick. Returns (id, name)."""
    print("\nLoading your playlists...")
    playlists = gateway.list_my_playlists()
    usable = [p for p in playlists if p.get("id")]
    if not usable:
        raise FriendlyError(
            "You do not seem to have any playlists on this Spotify account.\n"
            "Make a playlist in Spotify first, then run this program again."
        )

    print(f"\nYou have {len(usable)} playlists:\n")
    for index, playlist in enumerate(usable, start=1):
        total = (playlist.get("tracks") or {}).get("total", "?")
        name = playlist.get("name") or "(unnamed)"
        marker = "  <- destination playlist" if name in DESTINATIONS.values() else ""
        print(f"  {index:>3}. {name}  ({total} songs){marker}")

    print(
        "\nType the NUMBER of the playlist you want to sort, then press Enter.\n"
        "(You can also paste a playlist link instead.)"
    )
    while True:
        answer = input("Source playlist: ").strip()
        if not answer:
            print("Please type a number from the list above.")
            continue

        chosen = None
        if answer.isdigit() and 1 <= int(answer) <= len(usable):
            chosen = usable[int(answer) - 1]
            playlist_id, name = chosen["id"], chosen.get("name") or "(unnamed)"
        else:
            try:
                playlist_id = extract_playlist_id(answer)
            except FriendlyError as exc:
                print(f"\n{exc}\n")
                continue
            name = gateway.get_playlist_name(playlist_id)

        if name in DESTINATIONS.values():
            print(
                f"\n'{name}' is one of the four playlists this program creates.\n"
                "Please choose a different playlist as the source.\n"
            )
            continue
        return playlist_id, name


# --------------------------------------------------------------------------
# Classifying
# --------------------------------------------------------------------------


def classify_all(
    tracks: list,
    engine: LanguageClassifier,
    cache: Cache,
    use_cache: bool,
    show_reasons: bool,
) -> dict[str, Classification]:
    """Classify every track, printing progress as we go."""
    results: dict[str, Classification] = {}
    total = len(tracks)
    width = len(str(total))
    reused = 0

    for index, track in enumerate(tracks, start=1):
        verdict: Classification | None = None
        if use_cache:
            verdict = Classification.from_json(cache.classifications.get(track.track_id) or {})
            if verdict is not None:
                reused += 1
        if verdict is None:
            verdict = engine.classify(track)
            cache.classifications[track.track_id] = verdict.to_json()

        results[track.track_id] = verdict
        print(f"[{index:>{width}}/{total}] {track.display} → {verdict.label}")
        if show_reasons and verdict.reasons:
            for reason in verdict.reasons:
                print(f"          - {reason}")

    if reused:
        print(f"\n({reused} of {total} songs were already in the local cache.)")
    return results


def group_by_destination(tracks: list, results: dict[str, Classification]) -> dict[str, list]:
    """Work out which songs belong in which destination playlist."""
    buckets: dict[str, list] = {key: [] for key in DESTINATIONS}
    for track in tracks:
        verdict = results[track.track_id]
        if verdict.languages:
            for language in verdict.languages:
                buckets[language].append(track)
        else:
            buckets[lang.UNKNOWN].append(track)
    return buckets


def print_summary(tracks: list, results: dict[str, Classification], buckets: dict[str, list]) -> None:
    total = len(tracks)
    multi = sum(1 for t in tracks if len(results[t.track_id].languages) > 1)

    print(f"\n{RULE}")
    print("SUMMARY")
    print(RULE)
    print(f"{total} songs scanned.\n")
    for key, name in DESTINATIONS.items():
        print(f"  {name:<22} {len(buckets[key]):>5}")
    if multi:
        subject = "1 song is" if multi == 1 else f"{multi} songs are"
        print(f"\n{subject} in more than one language and will be added to each.")
    print("\nThe source playlist will NOT be modified.")


# --------------------------------------------------------------------------
# Applying the changes
# --------------------------------------------------------------------------


def plan_changes(gateway: SpotifyGateway, user_id: str, buckets: dict[str, list]) -> list[dict]:
    """
    Work out exactly what would be added, without changing anything.

    For each destination we read what is already in it and subtract that, so
    a song is never added twice and existing songs are never touched.
    """
    plan: list[dict] = []
    for key, name in DESTINATIONS.items():
        existing_playlist = gateway.find_playlist_by_name(name, user_id)
        already_there: set[str] = set()

        if existing_playlist:
            playlist_id = existing_playlist["id"]
            if playlist_id == gateway.source_playlist_id:
                raise FriendlyError(
                    f"SAFETY STOP: your source playlist is also named '{name}', "
                    "which is a destination name. Rename one of them and try again. "
                    "Nothing was changed."
                )
            already_there = gateway.playlist_track_ids(playlist_id)
        else:
            playlist_id = None

        seen: set[str] = set()
        to_add = []
        for track in buckets[key]:
            if track.track_id in already_there or track.track_id in seen:
                continue
            seen.add(track.track_id)
            to_add.append(track)

        plan.append(
            {
                "key": key,
                "name": name,
                "playlist_id": playlist_id,
                "exists": existing_playlist is not None,
                "already_there": len(already_there),
                "to_add": to_add,
            }
        )
    return plan


def print_plan(plan: list[dict], dry_run: bool) -> None:
    print(f"\n{RULE}")
    print("PLANNED CHANGES" if not dry_run else "PLANNED CHANGES (dry run - none of this will happen)")
    print(RULE)
    for entry in plan:
        status = "already exists" if entry["exists"] else "WILL BE CREATED"
        print(f"\n  {entry['name']}  ({status})")
        print(f"      already contains : {entry['already_there']} songs")
        print(f"      songs to add     : {len(entry['to_add'])}")
        print(f"      songs to remove  : 0  (this program never removes anything)")
    print("\n  Source playlist      : read only, 0 changes")


def apply_changes(gateway: SpotifyGateway, user_id: str, plan: list[dict]) -> None:
    print(f"\n{RULE}")
    print("APPLYING CHANGES")
    print(RULE)
    for entry in plan:
        playlist_id = entry["playlist_id"]
        if playlist_id is None:
            if not entry["to_add"]:
                print(f"  {entry['name']}: nothing to add, so not creating it.")
                continue
            print(f"  Creating '{entry['name']}'...")
            created = gateway.create_playlist(user_id, entry["name"], DESTINATION_DESCRIPTION)
            playlist_id = created["id"]

        if not entry["to_add"]:
            print(f"  {entry['name']}: already up to date.")
            continue

        uris = [t.uri for t in entry["to_add"]]
        added = gateway.add_tracks(playlist_id, uris)
        print(f"  {entry['name']}: added {added} songs.")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    load_dotenv()

    if args.dry_run:
        print("\n*** DRY RUN - nothing on Spotify will be changed. ***")

    print("\nConnecting to Spotify...")
    print("(Your browser will open so you can approve this app. Your password")
    print(" is typed into Spotify's own page and is never seen by this program.)")
    sp = build_spotify()
    gateway = SpotifyGateway(sp, dry_run=args.dry_run)
    user_id = gateway.current_user_id()
    print("Connected.")

    # -- pick the source ---------------------------------------------------
    if args.source:
        source_id = extract_playlist_id(args.source)
        source_name = gateway.get_playlist_name(source_id)
        if source_name in DESTINATIONS.values():
            raise FriendlyError(
                f"'{source_name}' is one of the four playlists this program "
                "creates, so it cannot be used as the source."
            )
    else:
        source_id, source_name = choose_source_playlist(gateway)

    # From this moment on, the gateway physically refuses to write to it.
    gateway.mark_source_playlist(source_id)
    print(f"\nSource playlist: '{source_name}' (read-only)")

    # -- read every song ---------------------------------------------------
    print("\nReading songs...")
    tracks, skipped = gateway.fetch_all_tracks(source_id)
    if not tracks:
        print("\nThere are no sortable songs in that playlist. Nothing to do.")
        for note in skipped[:5]:
            print(f"  {note}")
        return 0
    print(f"Found {len(tracks)} songs to sort.")
    if skipped:
        print(f"({len(skipped)} items were skipped - see the end of this run.)")

    # -- gather the free signals -------------------------------------------
    print("\nLooking up artist genres (this is a free Spotify signal)...")
    artist_ids = [aid for track in tracks for aid in track.artist_ids]
    genres = gateway.artist_genres(artist_ids)

    cache = Cache()
    musicbrainz = None
    if not args.no_musicbrainz:
        musicbrainz = MusicBrainzLookup(cache.artist_countries, enabled=True)

    ai = None
    if args.use_ai:
        try:
            ai = ClaudeClassifier()
            print("Claude will be asked about songs nothing else can identify.")
        except Exception as exc:
            print(f"Could not start the optional AI step, carrying on without it: {exc}")

    engine = LanguageClassifier(artist_genres=genres, musicbrainz=musicbrainz, ai=ai)

    # -- classify ----------------------------------------------------------
    print(f"\n{RULE}")
    print("CLASSIFYING")
    print(RULE)
    if musicbrainz is not None:
        print("(Unclear songs trigger a MusicBrainz lookup, which waits one")
        print(" second per artist to be polite. Later runs reuse the answers.)\n")
    results = classify_all(tracks, engine, cache, not args.no_cache, args.show_reasons)
    cache.save()

    buckets = group_by_destination(tracks, results)
    print_summary(tracks, results, buckets)

    # -- plan --------------------------------------------------------------
    print("\nChecking what is already in the destination playlists...")
    plan = plan_changes(gateway, user_id, buckets)
    print_plan(plan, args.dry_run)

    if skipped:
        print(f"\n{RULE}")
        print(f"SKIPPED ITEMS ({len(skipped)})")
        print(RULE)
        for note in _summarise(skipped):
            print(f"  {note}")

    # -- stop here if this was a dry run -----------------------------------
    if args.dry_run:
        print(f"\n{RULE}")
        print("DRY RUN COMPLETE. Nothing on Spotify was changed.")
        print("Run the same command without --dry-run to actually do it.")
        print(RULE)
        return 0

    total_to_add = sum(len(e["to_add"]) for e in plan)
    if total_to_add == 0:
        print("\nEverything is already sorted. Nothing to do.")
        return 0

    # -- confirm -----------------------------------------------------------
    print(f"\n{RULE}")
    print(f"This will add {total_to_add} songs across the four destination playlists.")
    print("It will NOT change your source playlist and will NOT remove anything.")
    print(RULE)

    if args.yes:
        print("Proceed? [y/N] y   (--yes was passed on the command line)")
    else:
        answer = input("Proceed? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print("\nCancelled. Nothing was changed.")
            return 0

    apply_changes(gateway, user_id, plan)
    print(f"\n{RULE}")
    print("DONE. Your source playlist '%s' was not modified." % source_name)
    print(RULE)
    return 0


def _summarise(notes: list[str], limit: int = 10) -> list[str]:
    """Collapse repeated skip messages so the output stays readable."""
    counts: dict[str, int] = {}
    for note in notes:
        counts[note] = counts.get(note, 0) + 1
    lines = [n if c == 1 else f"{n} (x{c})" for n, c in counts.items()]
    if len(lines) > limit:
        hidden = len(lines) - limit
        lines = lines[:limit] + [f"...and {hidden} more."]
    return lines


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        return run(args)
    except FriendlyError as exc:
        print(f"\n{RULE}\nPROBLEM\n{RULE}\n{exc}\n")
        return 1
    except KeyboardInterrupt:
        print("\n\nStopped by you. Nothing further was changed.")
        return 130
    except Exception as exc:  # last resort - never dump a raw traceback
        print(f"\n{RULE}\nUNEXPECTED PROBLEM\n{RULE}")
        print(f"{type(exc).__name__}: {exc}\n")
        print("If this keeps happening, please open an issue with the text above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
