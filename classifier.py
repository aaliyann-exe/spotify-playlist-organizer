"""
Decides which language(s) a song is in.

There is no single trick that works, so this file gathers several independent
pieces of evidence and combines them. Each piece of evidence is a number
between 0 and 1 saying "how strongly does this suggest language X".

The evidence, strongest first:

  1. Writing system.  Japanese kana, Urdu-specific Arabic letters and Hindi
     Devanagari are near-proof. This is instant and free.
  2. Spotify artist genres.  Tags like 'j-pop', 'anime', 'pakistani pop' or
     'filmi' come free with the track data we already downloaded.
  3. Word lists.  For Romanized titles we look for recognisable English words
     and recognisable Hindustani/Japanese words.
  4. langdetect.  A statistical guess. Weak on short titles, so it only ever
     nudges the result.
  5. MusicBrainz artist country.  Free, no signup, no API key. Only consulted
     when the signals above left the song ambiguous.
  6. Claude (optional, off by default, costs money).

THE HINDI PROBLEM
-----------------
Written in their own scripts, Urdu and Hindi are obviously different. Written
in Roman letters - "tu hai kahan" - they are effectively the same language,
and no text-based detector on earth can reliably tell them apart. So Hindi is
tracked as its own hidden category and a song is only ever called Urdu when
the Urdu evidence clearly beats the Hindi evidence. Otherwise it goes to
"Unknown". We would rather say "I don't know" than mislabel a Hindi song.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field

import requests

try:
    from langdetect import DetectorFactory, detect_langs
    from langdetect.lang_detect_exception import LangDetectException

    DetectorFactory.seed = 0  # makes langdetect give the same answer every run
    _LANGDETECT_OK = True
except Exception:  # pragma: no cover - only if the optional install failed
    _LANGDETECT_OK = False

ENGLISH = "English"
URDU = "Urdu"
JAPANESE = "Japanese"
UNKNOWN = "Unknown"

#: The three real languages, in the order we display them.
SUPPORTED = (ENGLISH, URDU, JAPANESE)

#: "hindi" is deliberately NOT in SUPPORTED. It exists only to hold Urdu back.
_HINDI = "hindi"

#: A song joins a language playlist when its score reaches this.
THRESHOLD = 0.60

#: Evidence at or below this level is too weak to rule anything out.
WEAK_EVIDENCE = 0.35

#: Urdu additionally has to beat Hindi by this much, or we say Unknown.
URDU_OVER_HINDI_MARGIN = 0.15

#: Bump this when the rules change, so old cached answers are thrown away.
RULES_VERSION = 3


# --------------------------------------------------------------------------
# Writing systems
# --------------------------------------------------------------------------

# Letters that exist in Urdu but not in Arabic. Seeing any of these means the
# Arabic-looking text is Urdu (or Persian), not Arabic.
_URDU_ONLY_LETTERS = set("ٹڈڑںےہھگچپژکیۓۃ")

_SCRIPT_RANGES = {
    "hiragana": [(0x3040, 0x309F)],
    "katakana": [(0x30A0, 0x30FF), (0x31F0, 0x31FF), (0xFF66, 0xFF9D)],
    "cjk": [(0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF)],
    "arabic": [(0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)],
    "devanagari": [(0x0900, 0x097F)],
    "hangul": [(0xAC00, 0xD7AF), (0x1100, 0x11FF), (0x3130, 0x318F)],
    "cyrillic": [(0x0400, 0x04FF)],
    "thai": [(0x0E00, 0x0E7F)],
    "hebrew": [(0x0590, 0x05FF)],
    "greek": [(0x0370, 0x03FF)],
}


def detect_scripts(text: str) -> set[str]:
    """Return the set of writing systems present in `text`."""
    found: set[str] = set()
    for char in text:
        code = ord(char)
        if char.isascii():
            if char.isalpha():
                found.add("latin")
            continue
        for name, ranges in _SCRIPT_RANGES.items():
            if any(low <= code <= high for low, high in ranges):
                found.add(name)
                break
        else:
            # Accented Latin such as é or ü still counts as Latin.
            if "LATIN" in unicodedata.name(char, ""):
                found.add("latin")
    if found & {"hiragana", "katakana"}:
        found.add("kana")
    if "arabic" in found and any(c in _URDU_ONLY_LETTERS for c in text):
        found.add("urdu_arabic")
    return found


# --------------------------------------------------------------------------
# Spotify genre tags
# --------------------------------------------------------------------------

_JAPANESE_GENRES = (
    "j-pop", "j-rock", "j-rap", "j-idol", "j-metal", "j-core", "j-pixie",
    "j-division", "j-poprock", "j-ambient", "j-indie", "japanese", "anime",
    "vocaloid", "city pop", "visual kei", "kayokyoku", "shibuya-kei", "denpa",
    "seiyu", "enka", "japanoise", "otacore", "touhou",
)

# Tags that are specifically Pakistani / Urdu-language.
_URDU_GENRES_STRONG = ("pakistani", "urdu", "lollywood", "coke studio")

# Tags shared across Pakistan and India - suggestive, never conclusive.
_URDU_GENRES_WEAK = ("qawwali", "ghazal", "sufi")

_HINDI_GENRES = (
    "bollywood", "filmi", "desi", "hindi", "indian", "punjabi", "haryanvi",
    "bhojpuri", "tollywood", "kollywood", "sandalwood", "bhangra", "ghazal indian",
)

# Tags that come from English-speaking music scenes.
_ENGLISH_GENRES = (
    "uk ", "british", "american", "canadian", "australian", "irish",
    "new zealand", "scottish", "welsh", "atl ", "detroit", "chicago",
    "brooklyn", "seattle", "nashville", "memphis", "boston", "toronto",
    "melbourne", "manchester", "liverpool", "country", "americana",
    "bluegrass", "alt z", "indie pop", "pop punk", "emo", "grunge",
    "hyperpop", "singer-songwriter", "west coast rap", "east coast hip hop",
    "southern hip hop", "boy band", "girl group", "adult standards",
    "classic rock", "soft rock", "yacht rock", "britpop", "drill",
    "uk garage", "trip hop", "motown", "doo-wop",
)

# Tags that rule Japanese out even when Chinese characters are present.
_OTHER_CJK_GENRES = ("mandopop", "c-pop", "cantopop", "chinese", "taiwan", "k-pop", "korean")


def _genre_hit(genres: list[str], needles: tuple[str, ...]) -> str | None:
    for genre in genres:
        for needle in needles:
            if needle in genre:
                return genre
    return None


# --------------------------------------------------------------------------
# Word lists for Romanized titles
# --------------------------------------------------------------------------

_ENGLISH_WORDS = {
    "a", "about", "after", "again", "against", "ain't", "aint", "all", "alone",
    "already", "always", "am", "an", "and", "another", "answer", "any", "anymore",
    "are", "around", "as", "at", "away", "baby", "back", "bad", "be", "beautiful",
    "because", "become", "been", "before", "believe", "best", "better", "between",
    "beyond", "big", "black", "blood", "blue", "body", "born", "boy", "break",
    "breathe", "bring", "broken", "but", "by", "call", "came", "can", "can't",
    "cant", "care", "carry", "catch", "chance", "change", "chasing", "child",
    "close", "cold", "come", "coming", "could", "crazy", "cry", "dance", "dark",
    "day", "days", "dead", "dear", "deep", "did", "die", "different", "do",
    "does", "don't", "dont", "door", "down", "dream", "dreams", "drive", "drown",
    "each", "early", "easy", "end", "enough", "even", "ever", "every",
    "everybody", "everything", "eyes", "face", "fade", "fall", "falling", "far",
    "fast", "feel", "feeling", "fever", "find", "fire", "first", "follow", "for",
    "forever", "forget", "forgive", "found", "free", "friend", "from", "full",
    "game", "get", "girl", "give", "go", "god", "gold", "gone", "gonna", "good",
    "got", "gotta", "great", "green", "hand", "hands", "happy", "hard", "has",
    "hate", "have", "he", "head", "hear", "heart", "heaven", "hello", "help",
    "her", "here", "hero", "hers", "high", "him", "his", "hold", "home", "hope",
    "hot", "hour", "house", "how", "hurt", "i", "i'll", "i'm", "ill", "im",
    "in", "inside", "into", "is", "it", "it's", "its", "just", "keep", "kill",
    "kind", "king", "kiss", "know", "lady", "last", "late", "laugh", "leave",
    "left", "let", "let's", "lets", "life", "light", "lights", "like", "listen",
    "little", "live", "lonely", "long", "look", "lose", "losing", "lost", "love",
    "lover", "made", "make", "man", "many", "matter", "may", "maybe", "me",
    "mean", "memory", "mind", "mine", "miss", "moment", "money", "moon", "more",
    "morning", "most", "mother", "move", "much", "music", "must", "my", "myself",
    "name", "near", "need", "never", "new", "next", "nice", "night", "no",
    "nobody", "not", "nothing", "now", "ocean", "of", "off", "oh", "old", "on",
    "once", "one", "only", "open", "or", "other", "our", "out", "over", "own",
    "pain", "paradise", "part", "party", "people", "perfect", "place", "play",
    "please", "power", "pretty", "promise", "pull", "put", "queen", "rain",
    "read", "ready", "real", "reason", "red", "remember", "return", "right",
    "rise", "river", "road", "rock", "roll", "room", "run", "running", "sad",
    "said", "same", "save", "say", "sea", "see", "seem", "she", "should", "show",
    "sick", "side", "sing", "sky", "sleep", "slow", "small", "smile", "so",
    "some", "somebody", "someone", "something", "sometimes", "song", "soon",
    "sorry", "soul", "sound", "speak", "stand", "star", "stars", "start", "stay",
    "still", "stop", "storm", "story", "strange", "street", "strong", "such",
    "summer", "sun", "sunshine", "sweet", "take", "talk", "tears", "tell",
    "than", "thank", "that", "the", "their", "them", "then", "there", "these",
    "they", "thing", "things", "think", "this", "those", "though", "thought",
    "three", "through", "throw", "time", "to", "today", "together", "tomorrow",
    "tonight", "too", "touch", "town", "train", "true", "truth", "try", "turn",
    "two", "under", "until", "up", "upon", "us", "use", "very", "wait", "wake",
    "walk", "wall", "wanna", "want", "war", "warm", "was", "watch", "water",
    "way", "we", "wear", "well", "were", "what", "when", "where", "which",
    "while", "white", "who", "why", "wild", "will", "wind", "wish", "with",
    "without", "woman", "won't", "wont", "wonder", "word", "words", "work",
    "world", "would", "wrong", "yeah", "year", "years", "yes", "yet", "you",
    "young", "your", "yours", "yourself",
}

# Romanized Urdu/Hindi. These words are shared by BOTH languages - that is
# exactly the point. Hitting them proves "Hindustani", not "Urdu".
_HINDUSTANI_WORDS = {
    "aa", "aaja", "aankhein", "aankhon", "aao", "aasman", "ab", "abhi", "adhoora",
    "aashiqui", "afsana", "ajnabi", "akela", "alvida", "andhera", "apna", "apne", "apni",
    "armaan", "aur", "awara", "baarish", "baat", "badal", "bahar", "bana", "banda",
    "bas", "bewafa", "bhi", "bina", "chahat", "chahta", "chal", "chalo", "chand",
    "chehra", "chupke", "dard", "dekha", "dekho", "dhadkan", "dhoop", "dil",
    "deewana", "diwana", "dilbar", "dooba", "dooriyan", "dost", "dua", "duniya",
    "ek", "gaana", "gaane", "gham", "ghar",
    "gulzar", "har", "hai", "hain", "haseen", "ho", "hoon", "hum", "humsafar",
    "husn", "intezaar", "ishq", "jaan", "jaana", "jaane", "jab", "jag", "jahan",
    "jaise", "jaanam", "judaai", "kaise", "kab", "kabhi", "kahan", "kahani",
    "kaisi", "khamoshi", "khuda", "khwab", "kismat", "koi", "kuch", "kya", "kyun",
    "kyu", "labon", "lamha", "lehron", "log", "mahi", "main", "manzil", "mausam",
    "mahiya", "mehfil", "mein", "mera", "mere", "meri", "mila", "milne", "mohabbat",
    "mujhe", "mushkil", "naam", "nahi", "nahin", "naina", "nazar", "nazm", "nigah",
    "pal", "phir", "pyaar", "pyar", "raat", "rab", "raha", "rahe", "rahi", "ranjish",
    "rasta", "roshni", "saanson", "saath", "sab", "sadqay", "safar", "sajna",
    "saiyaan", "sajni", "sanam", "sapne", "sara", "sath", "shaam", "sitaron",
    "socha", "subah", "sun",
    "suno", "tanha", "tera", "tere", "teri", "thoda", "tu", "tum", "tumhe",
    "tumse", "vaada", "waqt", "woh", "yaad", "yaar", "yeh", "zara", "zindagi",
    "zaalim", "zakhm",
}

# Romanized Japanese. Only distinctive multi-letter words - short particles
# like "no" or "wa" collide with English and are deliberately excluded.
_JAPANESE_ROMAJI = {
    "aishiteru", "arigatou", "asa", "ashita", "boku", "bokura", "chikai",
    "daisuki", "dakara", "deai", "gomen", "hajimari", "hanabi", "hikari",
    "hitori", "hoshi", "ima", "itsumo", "kaze", "kanashii", "kanata", "kibou",
    "kimi", "kimochi", "kioku", "kiseki", "kizuna", "kokoro", "koibito",
    "konayuki", "kotoba", "mirai", "mune", "namida", "natsu", "negai", "omoi",
    "onegai", "sakura", "sayonara", "seishun", "senpai", "shiawase", "sekai",
    "sora", "subarashii", "sukoshi", "tabidachi", "tatoeba", "tenshi",
    "tsubasa", "tsuki", "unmei", "utsukushii", "wasurenai", "watashi",
    "yakusoku", "yoake", "yozora", "yume", "yuki", "zutto",
}

# A handful of very common words in other languages. Their only job is to stop
# us lazily calling a Spanish or French song "English".
_OTHER_LANGUAGE_WORDS = {
    # Spanish / Portuguese
    "que", "para", "como", "pero", "muy", "todo", "nada", "amor", "corazon",
    "vida", "noche", "quiero", "contigo", "bailando", "despacito", "eres",
    "voce", "coracao", "saudade", "nao",
    # French
    "les", "des", "une", "avec", "pour", "mais", "toujours", "amour", "coeur",
    "jamais", "moi", "toi", "nuit", "rien",
    # German / Italian
    "nicht", "ich", "und", "der", "die", "das", "wir", "sono", "sei", "cuore",
    "amore", "notte", "sempre", "perche",
}

# Languages we are willing to let langdetect talk us out of English for.
# Deliberately excludes the ones langdetect hallucinates on short strings
# (Romanian, Indonesian, Tagalog, Afrikaans, Catalan and friends).
_MAJOR_OTHER_LANGUAGES = frozenset(
    {"es", "fr", "pt", "it", "de", "nl", "ru", "ko", "zh-cn", "zh-tw", "tr", "ar"}
)

_WORD_RE = re.compile(r"[a-z']+")


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


# --------------------------------------------------------------------------
# Combining evidence
# --------------------------------------------------------------------------


def _combine(weights: list[float]) -> float:
    """
    Merge independent pieces of evidence.

    Two weak-ish clues should add up to something stronger than either alone,
    but no amount of evidence should ever exceed certainty. The standard way
    to express that is: chance that ALL clues are wrong = product of each
    being wrong; confidence = 1 - that.
    """
    remaining = 1.0
    for weight in weights:
        remaining *= 1.0 - max(0.0, min(1.0, weight))
    return 1.0 - remaining


@dataclass
class Classification:
    """The verdict for one song."""

    languages: list[str]
    reasons: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return " + ".join(self.languages) if self.languages else UNKNOWN

    def to_json(self) -> dict:
        return {
            "languages": self.languages,
            "reasons": self.reasons,
            "scores": {k: round(v, 3) for k, v in self.scores.items()},
            "version": RULES_VERSION,
        }

    @staticmethod
    def from_json(data: dict) -> "Classification | None":
        if not isinstance(data, dict) or data.get("version") != RULES_VERSION:
            return None
        langs = data.get("languages")
        if not isinstance(langs, list):
            return None
        return Classification(
            languages=[l for l in langs if l in SUPPORTED],
            reasons=list(data.get("reasons") or []),
            scores=dict(data.get("scores") or {}),
        )


# --------------------------------------------------------------------------
# MusicBrainz - free artist country lookup, no signup, no API key
# --------------------------------------------------------------------------

_MB_URL = "https://musicbrainz.org/ws/2/artist/"
_MB_HEADERS = {
    "User-Agent": (
        "spotify-language-sorter/1.0 "
        "( https://github.com/aaliyann-exe/spotify-playlist-organizer )"
    )
}

_COUNTRY_TO_LANGUAGE = {
    "JP": JAPANESE,
    "PK": URDU,
    "IN": _HINDI,
    "US": ENGLISH, "GB": ENGLISH, "CA": ENGLISH, "AU": ENGLISH,
    "NZ": ENGLISH, "IE": ENGLISH,
}


class MusicBrainzLookup:
    """
    Asks MusicBrainz which country an artist is from.

    MusicBrainz asks that clients make at most one request per second and send
    a User-Agent identifying themselves. Both are respected here. Every answer
    is written to the local cache so an artist is only ever looked up once.
    """

    def __init__(self, cache: dict, enabled: bool = True) -> None:
        self.cache = cache
        self.enabled = enabled
        self._last_request = 0.0
        self.failures = 0

    def country(self, artist_name: str) -> str | None:
        if not self.enabled or not artist_name:
            return None
        key = artist_name.strip().lower()
        if key in self.cache:
            return self.cache[key]
        if self.failures >= 5:
            return None  # MusicBrainz looks down; stop bothering it

        # Be a good citizen: at most one request per second.
        wait = 1.1 - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

        try:
            escaped = artist_name.replace('"', "").replace("\\", "")
            response = requests.get(
                _MB_URL,
                params={"query": f'artist:"{escaped}"', "fmt": "json", "limit": 1},
                headers=_MB_HEADERS,
                timeout=15,
            )
            if response.status_code != 200:
                self.failures += 1
                return None
            artists = response.json().get("artists") or []
        except (requests.RequestException, ValueError):
            self.failures += 1
            return None

        country = None
        if artists:
            best = artists[0]
            # Only trust a confident, closely-matching hit.
            if best.get("score", 0) >= 90:
                country = best.get("country") or (best.get("area") or {}).get("iso-3166-1-codes", [None])[0]
        self.cache[key] = country
        return country


# --------------------------------------------------------------------------
# The classifier itself
# --------------------------------------------------------------------------


class LanguageClassifier:
    def __init__(
        self,
        artist_genres: dict[str, list[str]] | None = None,
        musicbrainz: MusicBrainzLookup | None = None,
        ai: "ClaudeClassifier | None" = None,
    ) -> None:
        self.artist_genres = artist_genres or {}
        self.musicbrainz = musicbrainz
        self.ai = ai
        self.ai_calls = 0

    # -- the individual evidence gatherers ---------------------------------

    def _genres_for(self, artist_ids: list[str]) -> list[str]:
        genres: list[str] = []
        for artist_id in artist_ids:
            genres.extend(self.artist_genres.get(artist_id, []))
        return genres

    def classify(self, track) -> Classification:
        """Work out the language(s) of one track."""
        title = track.name or ""
        artists = " ".join(track.artist_names)
        album = track.album_name or ""
        # Artist names are included because a kana or Urdu-script artist name
        # is strong evidence even when the song title is Romanized.
        all_text = f"{title} {artists} {album}".strip()

        scores: dict[str, list[float]] = {ENGLISH: [], URDU: [], JAPANESE: [], _HINDI: []}
        reasons: list[str] = []
        scripts = detect_scripts(all_text)
        genres = self._genres_for(track.artist_ids)

        # -- 1. Writing system ---------------------------------------------
        if "kana" in scripts:
            scores[JAPANESE].append(0.97)
            reasons.append("Japanese kana in the text")
        elif "cjk" in scripts:
            if _genre_hit(genres, _OTHER_CJK_GENRES):
                reasons.append("Chinese characters, but the artist is not Japanese")
            else:
                scores[JAPANESE].append(0.55)
                reasons.append("Chinese/Japanese characters (no kana, so not certain)")

        if "urdu_arabic" in scripts:
            scores[URDU].append(0.95)
            reasons.append("Urdu-specific letters in the text")
        elif "arabic" in scripts:
            reasons.append("Arabic script, but no Urdu-specific letters")

        if "devanagari" in scripts:
            scores[_HINDI].append(0.95)
            reasons.append("Devanagari (Hindi) script in the text")

        foreign_script = bool(
            scripts & {"hangul", "cyrillic", "thai", "hebrew", "greek", "arabic", "devanagari", "kana", "cjk"}
        )

        # -- 2. Spotify artist genres --------------------------------------
        hit = _genre_hit(genres, _JAPANESE_GENRES)
        if hit:
            scores[JAPANESE].append(0.90)
            reasons.append(f"artist genre '{hit}'")

        hit = _genre_hit(genres, _URDU_GENRES_STRONG)
        if hit:
            scores[URDU].append(0.90)
            reasons.append(f"artist genre '{hit}'")
        else:
            hit = _genre_hit(genres, _URDU_GENRES_WEAK)
            if hit:
                # Qawwali and ghazal are sung in both Pakistan and India.
                scores[URDU].append(0.50)
                scores[_HINDI].append(0.40)
                reasons.append(f"artist genre '{hit}' (Pakistani and Indian both)")

        hit = _genre_hit(genres, _HINDI_GENRES)
        if hit:
            scores[_HINDI].append(0.90)
            reasons.append(f"artist genre '{hit}' (Indian)")

        hit = _genre_hit(genres, _ENGLISH_GENRES)
        if hit:
            scores[ENGLISH].append(0.55)
            reasons.append(f"artist genre '{hit}'")

        # -- 3. Word lists --------------------------------------------------
        # Only the title and album are matched against the word lists. Artist
        # names are proper nouns and produce nonsense hits - "Dua Lipa" would
        # otherwise register "dua" as an Urdu/Hindi word. Artist names still
        # feed the writing-system check above, where they are genuinely useful.
        #
        # Words are de-duplicated first, because for a single the album name
        # is usually identical to the track name, which would otherwise count
        # every word twice.
        words = set(_words(f"{title} {album}"))

        english_hits = sorted(words & _ENGLISH_WORDS)
        hindustani_hits = sorted(words & _HINDUSTANI_WORDS)
        romaji_hits = sorted(words & _JAPANESE_ROMAJI)
        other_hits = sorted(words & _OTHER_LANGUAGE_WORDS)

        # A word in both lists (e.g. "aur" is Hindustani, "sun" is English)
        # proves nothing, so ignore overlaps.
        english_hits = [w for w in english_hits if w not in _HINDUSTANI_WORDS]
        hindustani_hits = [w for w in hindustani_hits if w not in _ENGLISH_WORDS]

        if len(english_hits) >= 2:
            scores[ENGLISH].append(0.70)
            reasons.append(f"English words in the title ({', '.join(english_hits[:3])})")
        elif len(english_hits) == 1:
            scores[ENGLISH].append(0.40)
            reasons.append(f"one English word in the title ('{english_hits[0]}')")

        if len(hindustani_hits) >= 2:
            # Deliberately equal: this is Hindustani, we cannot tell which.
            scores[URDU].append(0.50)
            scores[_HINDI].append(0.50)
            reasons.append(f"Urdu/Hindi words ({', '.join(hindustani_hits[:3])}) - shared by both")
        elif len(hindustani_hits) == 1:
            scores[URDU].append(0.30)
            scores[_HINDI].append(0.30)
            reasons.append(f"one Urdu/Hindi word ('{hindustani_hits[0]}')")

        if len(romaji_hits) >= 2:
            scores[JAPANESE].append(0.60)
            reasons.append(f"Japanese romaji ({', '.join(romaji_hits[:3])})")
        elif len(romaji_hits) == 1:
            scores[JAPANESE].append(0.30)
            reasons.append(f"one romaji word ('{romaji_hits[0]}')")

        # -- 4. langdetect --------------------------------------------------
        # Fed the title and album only, for the same proper-noun reason as
        # the word lists above.
        lexicon_text = f"{title} {album}".strip()
        detected = _langdetect(lexicon_text)
        if detected:
            code, probability = detected
            mapping = {"en": ENGLISH, "ur": URDU, "hi": _HINDI, "ja": JAPANESE}
            if code in mapping and probability >= 0.90:
                scores[mapping[code]].append(0.45)
                reasons.append(f"langdetect says {code} ({probability:.0%})")

        # -- 5. MusicBrainz, only if we are still unsure ---------------------
        interim = {lang: _combine(weights) for lang, weights in scores.items()}
        ambiguous = _is_ambiguous(interim)
        if ambiguous and self.musicbrainz is not None:
            for artist_name in track.artist_names[:2]:
                country = self.musicbrainz.country(artist_name)
                language = _COUNTRY_TO_LANGUAGE.get((country or "").upper())
                if language:
                    scores[language].append(0.75 if language != ENGLISH else 0.50)
                    reasons.append(f"MusicBrainz: {artist_name} is from {country}")
                    break

        # -- 6. The "plain English" fallback ---------------------------------
        final = {lang: _combine(weights) for lang, weights in scores.items()}
        no_foreign_evidence = (
            not foreign_script
            and not other_hits
            and max(final[URDU], final[JAPANESE], final[_HINDI]) <= WEAK_EVIDENCE
        )
        if no_foreign_evidence and final[ENGLISH] < THRESHOLD and "latin" in scripts:
            # langdetect gets a veto here, but only a narrow one. On a short,
            # name-heavy string it happily returns things like Romanian or
            # Tagalog with high confidence, so it may only veto in favour of
            # a language we actually expect to meet, on a long enough string.
            veto = (
                detected is not None
                and detected[0] in _MAJOR_OTHER_LANGUAGES
                and detected[1] >= 0.95
                and len(lexicon_text) >= 20
            )
            if veto:
                reasons.append(f"looks like {detected[0]}, not English")
            else:
                scores[ENGLISH].append(0.60)
                reasons.append("Latin script with no sign of another language")
        if other_hits:
            reasons.append(f"words from another language ({', '.join(other_hits[:3])})")

        final = {lang: _combine(weights) for lang, weights in scores.items()}

        # -- 7. Optional Claude pass for anything still unclear --------------
        if self.ai is not None and _is_ambiguous(final):
            verdict = self.ai.classify(track)
            self.ai_calls += 1
            if verdict:
                for language, confidence in verdict.items():
                    if language in scores and confidence >= 0.6:
                        scores[language].append(min(confidence, 0.85))
                reasons.append("Claude was asked about this one")
                final = {lang: _combine(weights) for lang, weights in scores.items()}

        return _decide(final, reasons)


def _is_ambiguous(scores: dict[str, float]) -> bool:
    """True when the evidence so far would not produce a confident answer."""
    if scores[JAPANESE] >= THRESHOLD:
        return False
    if scores[ENGLISH] >= THRESHOLD and max(scores[URDU], scores[_HINDI]) <= WEAK_EVIDENCE:
        return False
    urdu_clear = (
        scores[URDU] >= THRESHOLD and scores[URDU] - scores[_HINDI] >= URDU_OVER_HINDI_MARGIN
    )
    return not urdu_clear


def _decide(scores: dict[str, float], reasons: list[str]) -> Classification:
    """Turn the final scores into the list of playlists this song belongs in."""
    languages: list[str] = []

    if scores[ENGLISH] >= THRESHOLD:
        languages.append(ENGLISH)

    # Urdu must clear the bar AND clearly out-score Hindi. If Hindi is close
    # behind, we genuinely cannot tell, so Urdu is withheld.
    if scores[URDU] >= THRESHOLD:
        if scores[URDU] - scores[_HINDI] >= URDU_OVER_HINDI_MARGIN:
            languages.append(URDU)
        else:
            reasons.append(
                "could be Urdu or Hindi - too close to call, so not added to Urdu"
            )

    if scores[JAPANESE] >= THRESHOLD:
        languages.append(JAPANESE)

    # Keep the display order stable: English, Urdu, Japanese.
    languages.sort(key=SUPPORTED.index)

    if not languages:
        if scores[_HINDI] >= THRESHOLD:
            reasons.append("looks like Hindi, which is not one of the four playlists")
        return Classification(languages=[], reasons=reasons, scores=scores)
    return Classification(languages=languages, reasons=reasons, scores=scores)


def _langdetect(text: str) -> tuple[str, float] | None:
    if not _LANGDETECT_OK or len(text.strip()) < 6:
        return None
    try:
        results = detect_langs(text)
    except (LangDetectException, Exception):
        return None
    if not results:
        return None
    return results[0].lang, results[0].prob


# --------------------------------------------------------------------------
# Optional: ask Claude about the leftovers
# --------------------------------------------------------------------------

_AI_SYSTEM = """You identify the language(s) a song is SUNG in.

Reply with JSON only, no other text, in exactly this shape:
{"English": 0.0, "Urdu": 0.0, "Japanese": 0.0, "Hindi": 0.0}

Each value is your confidence from 0.0 to 1.0 that the song contains
substantial lyrics in that language. A song may score high in more than one.

Critical rule: Hindi and Urdu are separate answers. Romanized Hindustani
lyrics are ambiguous. Only score Urdu high if you have real reason to believe
the artist and song are Pakistani/Urdu rather than Indian/Hindi. When you are
unsure between them, give both a LOW score rather than guessing.

If you do not recognise the song at all, return all zeros."""


class ClaudeClassifier:
    """
    Optional extra step: ask Claude about songs the free signals could not
    resolve. Costs money, so this is off unless you pass --use-ai.
    """

    def __init__(self, model: str | None = None) -> None:
        import anthropic  # imported here so the package is only needed if used

        self.client = anthropic.Anthropic()
        self.model = model or os.getenv("ANTHROPIC_MODEL") or "claude-opus-5"
        self._anthropic = anthropic

    def classify(self, track) -> dict[str, float] | None:
        question = (
            f"Song title: {track.name}\n"
            f"Artist(s): {', '.join(track.artist_names) or 'unknown'}\n"
            f"Album: {track.album_name or 'unknown'}"
        )
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=256,
                system=_AI_SYSTEM,
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": question}],
            )
        except Exception as exc:  # a failed AI call must never stop the run
            print(f"   (Claude lookup failed, carrying on without it: {exc})")
            return None

        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            raw = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

        result: dict[str, float] = {}
        for key, value in raw.items():
            name = {"english": ENGLISH, "urdu": URDU, "japanese": JAPANESE, "hindi": _HINDI}.get(
                str(key).strip().lower()
            )
            if name and isinstance(value, (int, float)):
                result[name] = float(value)
        return result or None
