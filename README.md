# Spotify Language Sorter

Reads **one** of your Spotify playlists and copies each song into up to four
other playlists, depending on the language it is sung in:

```
Language - English
Language - Urdu
Language - Japanese
Language - Unknown
```

A song can land in more than one of them. A song sung in both English and
Urdu goes into **both** playlists.

> ### Your original playlist is never changed
>
> The playlist you pick is opened read-only. Nothing is added to it, nothing
> is removed from it, it is not reordered, and its name, description and
> privacy setting are left exactly as they were. The program is built so that
> writing to the source playlist is *impossible*, not merely unlikely - see
> [Safety](#safety) below.

---

## Table of contents

1. [Setup, step by step](#setup-step-by-step) - start here if you have never used Python
2. [Running it](#running-it)
3. [How the language detection works](#how-the-language-detection-works)
4. [Why Hindi songs often end up in Unknown](#why-hindi-songs-often-end-up-in-unknown)
5. [Do I need to pay for an AI API?](#do-i-need-to-pay-for-an-ai-api)
6. [The cache](#the-cache)
7. [Safety](#safety)
8. [All the options](#all-the-options)
9. [Something went wrong](#something-went-wrong)
10. [For developers](#for-developers)

---

## Setup, step by step

You only have to do this once. Take it slowly; every step is spelled out.

### Step 1 - Check whether Python is installed

Python is the language this program is written in. Your computer needs it in
order to run the program.

Open **PowerShell**: press the Windows key, type `powershell`, press Enter.

Copy and paste this, then press Enter:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python3127\python.exe" --version
```

If you see `Python 3.12.7`, **Python is already installed** and you can skip
to Step 3.

If you see an error instead, do Step 2.

### Step 2 - Install Python (only if Step 1 failed)

1. Go to <https://www.python.org/downloads/windows/>
2. Click the yellow **Download Python 3.12.x** button.
3. Run the file you downloaded.
4. **Important:** on the first screen of the installer, tick the box at the
   bottom that says **"Add python.exe to PATH"**. It is easy to miss, and
   skipping it causes most beginner problems.
5. Click **Install Now** and wait for it to finish.
6. Close PowerShell completely, open it again, and type `python --version`.
   You should see `Python 3.12.x`.

### Step 3 - Open the project folder in PowerShell

In PowerShell, paste this and press Enter:

```powershell
cd "C:\Users\aaliy\OneDrive\Desktop\spotify-playlist-organizer"
```

Nothing visible happens - that is correct. You have just told PowerShell to
work inside the project folder.

### Step 4 - Install the program's dependencies

The program uses a few free, open-source libraries. They are installed into a
private folder called `.venv` ("virtual environment"), so they cannot
interfere with anything else on your computer.

**This has already been done for you.** If you ever need to redo it:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python3127\python.exe" -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Step 5 - Create a Spotify Developer application

This gives the program permission to talk to Spotify **on your behalf**. It is
free, and it does not require Spotify Premium.

1. Go to <https://developer.spotify.com/dashboard> and log in with your normal
   Spotify account.
2. Accept the Developer Terms of Service if you are asked to.
3. Click **Create app**.
4. Fill the form in like this:

   | Field | What to put |
   |---|---|
   | **App name** | `My Playlist Sorter` (any name is fine) |
   | **App description** | `Sorts my playlist by language` |
   | **Redirect URI** | `http://127.0.0.1:8888/callback` |
   | **Which API/SDKs are you planning to use?** | tick **Web API** |

5. After typing the Redirect URI you must click the **Add** button next to it,
   or it will not be saved.

   > The Redirect URI has to match **character for character**. It is
   > `http`, not `https`. It is `127.0.0.1`, not `localhost` - Spotify no
   > longer accepts `localhost`. There is no trailing slash.

6. Tick the terms checkbox and click **Save**.

### Step 6 - Find your Client ID and Client Secret

1. You should now be looking at your new app. Click **Settings** (top right).
2. **Client ID** is shown directly on this page. It is a long string of
   letters and numbers.
3. Under it, click **View client secret** to reveal the **Client Secret**.

Treat the Client Secret like a password. Do not post it anywhere, do not put
it in a screenshot, and do not send it to anyone - including to me.

### Step 7 - Put them into your `.env` file

`.env` is a plain text file that holds your settings. It is listed in
`.gitignore`, so it can never be uploaded to GitHub by accident.

In PowerShell, run this to create it from the template:

```powershell
Copy-Item .env.example .env
notepad .env
```

Notepad opens. Replace the placeholder text so it looks like this (using your
own values):

```
SPOTIPY_CLIENT_ID=1a2b3c4d5e6f7g8h9i0j
SPOTIPY_CLIENT_SECRET=0j9i8h7g6f5e4d3c2b1a
SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback
```

Save (`Ctrl+S`) and close Notepad.

- No quote marks, no spaces around the `=`.
- The Client Secret is **optional**. If you leave it as-is or delete the line,
  the program automatically uses a secretless login method called PKCE, which
  works just as well. One less secret on your disk.

**Setup is now complete.**

---

## Running it

### Always do a dry run first

A dry run reads your playlist and shows you exactly what it *would* do, while
changing absolutely nothing on Spotify.

```powershell
cd "C:\Users\aaliy\OneDrive\Desktop\spotify-playlist-organizer"
.venv\Scripts\activate
python spotify_sorter.py --dry-run
```

> If `.venv\Scripts\activate` gives a red error about scripts being disabled,
> skip it and use this instead - it does the same thing:
>
> ```powershell
> .venv\Scripts\python.exe spotify_sorter.py --dry-run
> ```

**The first time you run it**, your web browser opens on a Spotify page asking
you to allow the app. Click **Agree**. The page will then look like it failed
to load - that is normal and expected. The program has already caught what it
needed. You can close the tab.

You are never asked for your Spotify password by this program. You type it
into Spotify's own website, if at all.

### Choosing your playlist

Next you get a numbered list of your playlists:

```
You have 12 playlists:

    1. Liked Songs Backup  (327 songs)
    2. Study Music  (58 songs)
    3. Road Trip  (91 songs)

Type the NUMBER of the playlist you want to sort, then press Enter.
```

Type the number - for example `1` - and press Enter. (You can also paste a
playlist link copied from Spotify via *Share -> Copy link to playlist*.)

### Watching it work

```
[  1/327] Blinding Lights - The Weeknd → English
[  2/327] アイドル - YOASOBI → Japanese
[  3/327] Tu Hai Kahan - AUR → Urdu
[  4/327] Kesariya - Arijit Singh → Unknown
[  5/327] Love You Zindagi - Ali Sethi → English + Urdu
```

Then a summary and a plan:

```
SUMMARY
327 songs scanned.

  Language - English       214
  Language - Urdu           97
  Language - Japanese       56
  Language - Unknown        18

12 songs are in more than one language and will be added to each.

The source playlist will NOT be modified.
```

A dry run stops here. Nothing was changed.

### Doing it for real

Run the same command **without** `--dry-run`:

```powershell
python spotify_sorter.py
```

It does everything the dry run did, then shows the plan and asks:

```
This will add 385 songs across the four destination playlists.
It will NOT change your source playlist and will NOT remove anything.
------------------------------------------------------------------
Proceed? [y/N]:
```

Type `y` and press Enter to go ahead. **Anything else** - `n`, Enter on its
own, a typo, `Ctrl+C` - cancels, and nothing is changed.

### Running it again later

Just run it again. Adding songs to the source playlist and re-running is the
normal way to use this program:

```powershell
cd "C:\Users\aaliy\OneDrive\Desktop\spotify-playlist-organizer"
.venv\Scripts\activate
python spotify_sorter.py
```

The second run only adds what is **new**. Songs already in a destination
playlist are left alone, never duplicated, and never removed. If nothing has
changed, it tells you everything is already sorted and exits.

Songs you have added to the destination playlists by hand are also safe -
this program only ever adds.

---

## How the language detection works

There is no single reliable trick, so the program gathers several independent
clues and weighs them up. Each clue is a confidence score from 0 to 1, and a
song joins a playlist when its total for that language reaches **0.60**.

| # | Clue | Strength | Cost |
|---|------|----------|------|
| 1 | **Writing system.** Japanese kana (`アイドル`), Urdu-specific Arabic letters (`ٹ ڈ ڑ ں ے`), Hindi Devanagari (`केसरिया`) | Near proof | Free, instant |
| 2 | **Spotify artist genres.** `j-pop`, `anime`, `vocaloid`, `pakistani pop`, `coke studio`, `filmi`, `bollywood` | Strong | Free, already downloaded |
| 3 | **Word lists.** Recognisable English, Hindustani and Japanese-romaji words in the title | Moderate | Free, instant |
| 4 | **langdetect.** A statistical language guesser | Weak on short titles, so it only nudges | Free, instant, offline |
| 5 | **MusicBrainz artist country.** Pakistan → Urdu, Japan → Japanese, India → Hindi | Strong tie-breaker | Free, no signup, no key |
| 6 | **Claude** (optional, off by default) | Strong | Costs money |

Clue 5 is what makes this work well, and it is completely free.
[MusicBrainz](https://musicbrainz.org) is an open music encyclopaedia with no
API key and no account. The program only asks it about songs the earlier clues
could not settle, waits a full second between requests to be a polite
visitor, and remembers every answer so an artist is only ever looked up once.

Two deliberate design choices:

- **Artist names are not run through the word lists.** They are proper nouns
  and produce nonsense - "Dua Lipa" would otherwise register `dua` as an
  Urdu word. Artist names *are* used for the writing-system check, where a
  name in kana is genuinely strong evidence.
- **"I don't know" is a valid answer.** When the evidence is thin the song
  goes to `Language - Unknown` instead of being guessed at. You can review
  that playlist by hand; a wrong confident answer is much harder to spot.

---

## Why Hindi songs often end up in Unknown

This is intentional, and it is worth understanding.

Written in their own scripts, Urdu (`تجھے کیا خبر`) and Hindi (`केसरिया`) are
instantly distinguishable, and the program handles both perfectly.

Written in Roman letters, they are effectively **the same language**.
"Tu hai kahan" is valid Urdu and valid Hindi, spelled identically. No text
detector in existence can reliably separate them, and any program claiming
otherwise is guessing.

So the program tracks Hindi as a hidden fourth category and only files a song
under Urdu when the Urdu evidence **clearly beats** the Hindi evidence - by a
margin of 0.15. In practice:

| Situation | Result |
|---|---|
| Urdu script | **Urdu** |
| Romanized, artist genre `pakistani pop` | **Urdu** |
| Romanized, MusicBrainz says the artist is Pakistani | **Urdu** |
| Romanized, artist genre `bollywood` / `filmi` | **Unknown** (never Urdu) |
| Romanized, MusicBrainz says the artist is Indian | **Unknown** (never Urdu) |
| Devanagari script | **Unknown** (never Urdu) |
| Romanized, nothing known about the artist | **Unknown** |

Hindi has no playlist of its own because you asked for four playlists, so
Hindi songs land in `Language - Unknown`. If you would like a
`Language - Hindi` playlist as well, that is a small change - just ask.

---

## Do I need to pay for an AI API?

**No.** Everything described above is free and runs without any AI service.
The program works completely out of the box with no paid account of any kind.

For completeness, here are the options that were considered:

| Option | Cost | Verdict |
|---|---|---|
| **Unicode script detection** | Free | ✅ Used. Decisive for Japanese, Urdu and Hindi scripts. |
| **Spotify artist genres** | Free | ✅ Used. Comes with data we already fetch. |
| **MusicBrainz** | Free, no key, no signup | ✅ Used. The Hindi/Urdu tie-breaker. |
| **langdetect** | Free, offline | ✅ Used, but only as a nudge - it is unreliable on short titles. |
| **Google Cloud Translation language detection** | ~$20 per million characters, needs a credit card and a billing account | ❌ Rejected. Costs money and cannot tell romanized Urdu from Hindi anyway. |
| **Genius / Musixmatch lyrics APIs** | Free tiers exist but are heavily rate-limited; Musixmatch returns 30% of lyrics only | ❌ Rejected. Fiddly signup, slow, and the free tiers are too restrictive for 300+ songs. |
| **Claude (Anthropic API)** | Pay per use | ⚠️ Available but **off by default** - see below. |

### The optional Claude layer

If you ever want a second opinion on the songs that landed in Unknown, the
program can ask Claude about *only those songs*. It is disabled unless you
explicitly ask for it.

1. Get an API key from <https://console.anthropic.com> (needs a payment method).
2. Add these two lines to your `.env` file:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ANTHROPIC_MODEL=claude-opus-5
   ```
3. Install the library and run with `--use-ai`:
   ```powershell
   .venv\Scripts\python.exe -m pip install anthropic
   python spotify_sorter.py --dry-run --use-ai
   ```

**Rough cost.** Each song is about 200 tokens in and 30 out. Claude Opus 5 is
$5 per million input tokens and $25 per million output. If 40 songs out of 327
are ambiguous, that is roughly **1-2 US cents for the whole playlist**, and
answers are cached so a repeat run costs nothing. For a cheaper model still,
set `ANTHROPIC_MODEL=claude-haiku-4-5` ($1 / $5 per million).

Even with `--use-ai` on, Claude is instructed that Hindi and Urdu are separate
answers and to score both *low* when unsure - so it cannot reintroduce the
guessing that the rest of the program carefully avoids.

---

## The cache

Working out a song's language can involve a one-second MusicBrainz lookup, so
the answers are saved to `cache/classifications.json`. On later runs, songs
already in the cache are reused instantly.

The file contains only:

- song ID → the languages decided on, and the reasons why
- artist name → the country MusicBrainz reported

It contains **no credentials of any kind**. The code actively refuses to write
anything whose key looks like a secret, and the file is gitignored.

Deleting it is always safe - the program simply works everything out again.
To ignore it for one run without deleting it, use `--no-cache`.

Your Spotify login token is stored separately in `cache/spotify_token.json`,
which is also gitignored. Delete that file to force a fresh login.

---

## Safety

You asked for the source playlist to be untouchable. Here is exactly how that
is enforced, so you can verify it rather than take my word for it.

1. **Only two functions in the entire program can change anything on Spotify**:
   `create_playlist` and `add_tracks`, both in
   [`spotify_client.py`](spotify_client.py). There is no delete function, no
   reorder function and no rename function anywhere in the codebase - the
   capability simply does not exist.

2. **Both go through one guard.** As soon as you pick a playlist, its ID is
   recorded. Every write checks the target against it and raises a
   `SAFETY STOP` error rather than proceed:

   ```python
   def _assert_writable(self, playlist_id, action):
       if self.source_playlist_id and playlist_id == self.source_playlist_id:
           raise FriendlyError("SAFETY STOP: ...")
       if self.dry_run:
           raise FriendlyError("SAFETY STOP: ...")
   ```

3. **Dry-run mode blocks writes at the same choke point**, not merely by
   skipping the call. Even a bug that reached a write in dry-run mode would
   raise rather than write.

4. **The four destination names are rejected as a source.** You cannot pick
   `Language - English` as the playlist to sort.

5. **It is tested.** The suite runs the whole program against a fake Spotify
   that records every change made to it, and asserts the source playlist is
   byte-for-byte identical afterwards and that no removal, reorder or rename
   call was ever made. Run `python -m pytest` to see it.

---

## All the options

| Option | What it does |
|---|---|
| *(nothing)* | Normal run. Shows a plan and asks before changing anything. |
| `--dry-run` | Read and classify everything, then stop. Makes **zero** changes. |
| `--source LINK` | Skip the menu; use this playlist link, URI or ID as the source. |
| `--yes` | Answer yes to the confirmation prompt. Ignored during `--dry-run`. |
| `--show-reasons` | Print *why* each song was classified the way it was. Best way to check accuracy. |
| `--no-musicbrainz` | Skip artist-country lookups. Much faster, noticeably more Unknowns. |
| `--no-cache` | Ignore saved results and classify every song from scratch. |
| `--use-ai` | Also ask Claude about songs nothing else could identify. Costs money. |
| `--help` | Show all of this in the terminal. |

Checking the accuracy of a particular song:

```powershell
python spotify_sorter.py --dry-run --show-reasons
```

---

## Something went wrong

| What you see | What to do |
|---|---|
| `Python was not found` | Python is not on your PATH. Use the full form: `.venv\Scripts\python.exe spotify_sorter.py` |
| `running scripts is disabled on this system` | Skip `activate` and use `.venv\Scripts\python.exe spotify_sorter.py` instead. |
| `SPOTIPY_CLIENT_ID is missing` | Your `.env` file is missing or still has the placeholder text. Redo Step 7. |
| `INVALID_CLIENT: Invalid redirect URI` | The Redirect URI in your Spotify app settings does not match. It must be exactly `http://127.0.0.1:8888/callback`, and you must have clicked **Add** then **Save**. |
| Browser shows "site can't be reached" after you click Agree | **This is normal.** The login already worked. Close the tab and look back at PowerShell. |
| `Spotify rejected our access token` | Delete `cache\spotify_token.json` and run again to log in fresh. |
| `Spotify asked us to slow down` | Nothing to do - the program is waiting and will continue on its own. |
| Too many songs in Unknown | Run with `--show-reasons` to see why. Most will be romanized Hindi, which is deliberate - see [the Hindi section](#why-hindi-songs-often-end-up-in-unknown). |
| A song is in the wrong playlist | Remove it from the destination playlist in Spotify by hand. Be aware that the **next run will put it back**, because the program re-checks every song in the source playlist and adds anything missing. If that becomes annoying, ask me to add a "never add these" exceptions list. |

---

## For developers

```
spotify-playlist-organizer/
├── spotify_sorter.py      # the command-line program you run
├── spotify_client.py      # Spotify API + OAuth + the read-only guard
├── classifier.py          # language detection
├── cache_store.py         # local JSON cache
├── conftest.py            # lets the tests find the modules
├── requirements.txt
├── .env.example           # template; copy to .env and fill in
├── .gitignore
├── cache/                 # token + classification cache (gitignored)
└── tests/
    ├── fake_spotify.py       # a pretend Spotify API
    ├── test_classifier.py    # language detection
    └── test_playlist_logic.py# pagination, dedup, dry-run, immutability
```

Run the tests - no Spotify account or internet connection needed:

```powershell
.venv\Scripts\python.exe -m pytest -v
```

**Spotify scopes used** (the minimum for the job):
`playlist-read-private`, `playlist-read-collaborative`,
`playlist-modify-private`, `playlist-modify-public`.
The last is only needed in case one of your destination playlists is public;
newly created ones are private.

**Branches:** `main` is the stable version, `dev` is where work happens.

**Tuning the classifier:** the thresholds are the constants at the top of
[`classifier.py`](classifier.py) - `THRESHOLD`, `WEAK_EVIDENCE` and
`URDU_OVER_HINDI_MARGIN`. If you change any scoring rule, bump
`RULES_VERSION` so stale cached answers are discarded automatically.
