"""
Tests for the language classifier.

These run entirely offline - no Spotify account and no internet needed.
MusicBrainz is replaced with a stub so the tests are fast and deterministic.
"""

from __future__ import annotations

import pytest

import classifier as lang
from classifier import (
    ENGLISH,
    JAPANESE,
    URDU,
    Classification,
    LanguageClassifier,
    detect_scripts,
)
from spotify_client import Track


class StubMusicBrainz:
    """Stands in for MusicBrainz with a fixed name -> country table."""

    def __init__(self, countries: dict[str, str] | None = None) -> None:
        self.countries = {k.lower(): v for k, v in (countries or {}).items()}
        self.asked: list[str] = []

    def country(self, artist_name: str) -> str | None:
        self.asked.append(artist_name)
        return self.countries.get(artist_name.strip().lower())


def track(name, artists=(("a1", "Test Artist"),), album="Album") -> Track:
    return Track(
        track_id=f"id_{abs(hash(name)) % 100000}",
        uri="spotify:track:xxx",
        name=name,
        artist_names=[a[1] for a in artists],
        artist_ids=[a[0] for a in artists],
        album_name=album,
    )


def classify(t, genres=None, countries=None):
    engine = LanguageClassifier(
        artist_genres=genres or {},
        musicbrainz=StubMusicBrainz(countries) if countries is not None else None,
    )
    return engine.classify(t)


# ------------------------------------------------------------------ scripts


def test_detects_japanese_kana():
    assert "kana" in detect_scripts("アイドル")
    assert "kana" in detect_scripts("よるにかける")


def test_detects_urdu_but_not_plain_arabic():
    # 'ٹ' and 'ے' exist in Urdu and not in Arabic.
    assert "urdu_arabic" in detect_scripts("تجھے کیا")
    # Pure Arabic has no Urdu-only letters.
    scripts = detect_scripts("الحمد لله")
    assert "arabic" in scripts and "urdu_arabic" not in scripts


def test_detects_devanagari_and_hangul():
    assert "devanagari" in detect_scripts("केसरिया")
    assert "hangul" in detect_scripts("아이돌")


# ------------------------------------------------------- confident languages


def test_japanese_script_is_japanese_only():
    result = classify(track("アイドル", [("a1", "YOASOBI")]))
    assert result.languages == [JAPANESE]


def test_japanese_genre_beats_romanized_title():
    result = classify(
        track("Idol", [("a1", "YOASOBI")]),
        genres={"a1": ["j-pop", "anime"]},
    )
    assert JAPANESE in result.languages


def test_urdu_script_is_urdu():
    result = classify(track("تجھے کیا خبر", [("a1", "Some Artist")]))
    assert URDU in result.languages
    assert JAPANESE not in result.languages


def test_plain_english_song():
    result = classify(track("Blinding Lights", [("a1", "The Weeknd")]))
    assert result.languages == [ENGLISH]


def test_english_with_no_recognisable_words_still_english():
    # "Levitating" is not in the word list, so this relies on the fallback.
    result = classify(track("Levitating", [("a1", "Dua Lipa")]))
    assert result.languages == [ENGLISH]


# ------------------------------------------------- the Hindi / Urdu problem


def test_romanized_hindustani_alone_is_unknown():
    """
    "Tu Hai Kahan" in Roman letters could be Urdu or Hindi. With no other
    evidence the correct answer is Unknown, NOT a guess.
    """
    result = classify(track("Tu Hai Kahan", [("a1", "Mystery Artist")]))
    assert result.languages == []
    assert result.label == "Unknown"


def test_hindustani_becomes_urdu_when_artist_is_pakistani():
    result = classify(
        track("Tu Hai Kahan", [("a1", "AUR")]),
        genres={"a1": ["pakistani indie"]},
    )
    assert URDU in result.languages


def test_hindustani_with_bollywood_genre_is_never_urdu():
    """A Hindi film song must not be silently filed under Urdu."""
    result = classify(
        track("Kesariya", [("a1", "Arijit Singh")]),
        genres={"a1": ["filmi", "modern bollywood"]},
    )
    assert URDU not in result.languages
    assert result.languages == []


def test_devanagari_is_never_urdu():
    result = classify(track("केसरिया", [("a1", "Arijit Singh")]))
    assert URDU not in result.languages


def test_musicbrainz_pakistan_resolves_to_urdu():
    result = classify(
        track("Tu Hai Kahan", [("a1", "AUR")]),
        countries={"AUR": "PK"},
    )
    assert URDU in result.languages


def test_musicbrainz_india_does_not_resolve_to_urdu():
    result = classify(
        track("Tera Ghata", [("a1", "Gajendra Verma")]),
        countries={"Gajendra Verma": "IN"},
    )
    assert URDU not in result.languages
    assert result.languages == []


def test_musicbrainz_is_not_consulted_when_the_answer_is_already_clear():
    stub = StubMusicBrainz({"YOASOBI": "JP"})
    engine = LanguageClassifier(artist_genres={}, musicbrainz=stub)
    engine.classify(track("アイドル", [("a1", "YOASOBI")]))
    assert stub.asked == [], "a kana title needs no lookup"


# ------------------------------------------------------ multiple languages


def test_song_can_be_english_and_urdu_at_once():
    result = classify(
        track("Love You Zindagi Forever", [("a1", "Some Pakistani Artist")]),
        genres={"a1": ["pakistani pop"]},
    )
    assert ENGLISH in result.languages
    assert URDU in result.languages


def test_song_can_be_english_and_japanese_at_once():
    result = classify(
        track("Never Give Up Kimi", [("a1", "Some Artist")]),
        genres={"a1": ["j-rock"]},
    )
    assert ENGLISH in result.languages
    assert JAPANESE in result.languages


def test_language_order_is_stable():
    result = classify(
        track("Love You Zindagi Forever Kimi", [("a1", "X")]),
        genres={"a1": ["pakistani pop", "j-pop"]},
    )
    assert result.languages == sorted(result.languages, key=lang.SUPPORTED.index)


# ----------------------------------------------------------------- unknown


def test_korean_song_is_unknown_not_english():
    result = classify(track("아이돌", [("a1", "IU")]))
    assert result.languages == []


def test_spanish_song_is_not_called_english():
    result = classify(track("Bailando contigo toda la noche", [("a1", "Enrique")]))
    assert ENGLISH not in result.languages


def test_every_result_is_a_valid_destination():
    """No classification may ever produce a language we have no playlist for."""
    samples = [
        track("アイドル"), track("Blinding Lights"), track("Tu Hai Kahan"),
        track("केसरिया"), track("아이돌"), track(""), track("123"),
    ]
    for sample in samples:
        result = classify(sample)
        assert all(l in lang.SUPPORTED for l in result.languages)


def test_empty_and_weird_titles_do_not_crash():
    for name in ["", " ", "???", "!!!", "🎵🎵", "\n\t"]:
        result = classify(track(name, [("a1", "")], album=""))
        assert isinstance(result, Classification)


# ------------------------------------------------------------ cache format


def test_classification_survives_a_round_trip():
    original = Classification(languages=[ENGLISH, URDU], reasons=["because"], scores={})
    restored = Classification.from_json(original.to_json())
    assert restored is not None
    assert restored.languages == [ENGLISH, URDU]


def test_cached_result_from_an_older_ruleset_is_discarded():
    stale = {"languages": [ENGLISH], "reasons": [], "scores": {}, "version": -1}
    assert Classification.from_json(stale) is None


def test_label_reads_nicely():
    assert Classification(languages=[]).label == "Unknown"
    assert Classification(languages=[ENGLISH, URDU]).label == "English + Urdu"
