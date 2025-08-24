#!/usr/bin/env python3
import os
import csv
import re
import sqlite3
import random
import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import List, Tuple, Optional

from unidecode import unidecode
from dotenv import load_dotenv

# Tweepy is only imported if we are going to tweet
try:
    import tweepy  # type: ignore
except Exception:
    tweepy = None  # allows scanning without Tweepy installed

DB_PATH = os.path.join(os.path.dirname(__file__), "haiku_cache.db")

# ---------------------------- Syllable counting ---------------------------- #

VOWELS = "aeiouy"

def count_syllables_in_word(word: str) -> int:
    """Heuristic syllable counter for English words.
    Not perfect, but stable and dependency-free.

    Rules:
    - Count groups of vowels (aeiouy) as 1 syllable.
    - Subtract 1 for silent 'e' at end, unless word ends with 'le' after a consonant.
    - Treat 'ia', 'io' and similar combinations as separate groups sometimes,
      but we leave this to the vowel-group approach.
    - Ensure at least 1 syllable.
    """
    w = word.lower()
    w = re.sub(r"[^a-z]", "", w)

    if not w:
        return 0

    # Special cases that commonly trip heuristics
    special = {
        "queue": 1, "people": 2, "choir": 1, "hour": 1, "our": 1, "fire": 1, "one": 1,
        "two": 1, "once": 1, "blood": 1, "breathe": 1, "breathed": 1, "every": 2,
        "even": 2, "ever": 2, "business": 2, "family": 3, "poem": 2, "poet": 2,
        "quiet": 2, "quietly": 3, "science": 2, "giant": 2
    }
    if w in special:
        return special[w]

    # Count vowel groups
    groups = re.findall(r"[aeiouy]+", w)
    syllables = len(groups)

    # Trailing silent 'e'
    if w.endswith("e") and not w.endswith(("le", "ye")) and syllables > 1:
        syllables -= 1

    # 'le' ending after consonant, e.g., "table" -> +1 if not already counted
    if w.endswith("le") and len(w) > 2 and w[-3] not in VOWELS:
        syllables += 1

    return max(1, syllables)

def count_syllables_in_line(line: str) -> int:
    # Remove annotations like [Chorus], (Verse), etc
    line = re.sub(r"[\[\(].*?[\]\)]", "", line)
    line = unidecode(line)
    words = re.findall(r"[A-Za-z']+", line)
    return sum(count_syllables_in_word(w) for w in words)

# ---------------------------- Haiku detection ----------------------------- #

@dataclass
class Haiku:
    title: str
    artist: str
    lines: Tuple[str, str, str]
    syllables: Tuple[int, int, int]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def signature(self) -> str:
        h = hashlib.sha256()
        h.update(self.title.strip().lower().encode())
        h.update(self.artist.strip().lower().encode())
        h.update(self.text.strip().lower().encode())
        return h.hexdigest()

def is_noise_line(line: str) -> bool:
    if not line.strip():
        return True
    # Filter stage cues like [Chorus], [Verse 1], etc
    if re.search(r"^\s*[\[\(].*?[\]\)]\s*$", line):
        return True
    # Often non-lyrical noise like la la la, ooh, yeah repeated
    if re.fullmatch(r"(la|na|o+h|yeah|ya|uh)+([ ,\-!?.]*\1)*", line.strip().lower()):
        return True
    return False

def find_haikus_in_lyrics(title: str, artist: str, lyrics: str) -> List[Haiku]:
    # Normalise newlines
    raw_lines = [ln.strip() for ln in lyrics.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines = [ln for ln in raw_lines if not is_noise_line(ln)]

    haikus: List[Haiku] = []
    for i in range(len(lines) - 2):
        triplet = lines[i:i+3]
        sylls = tuple(count_syllables_in_line(ln) for ln in triplet)
        if sylls == (5, 7, 5):
            haikus.append(Haiku(title=title, artist=artist, lines=tuple(triplet), syllables=sylls))  # type: ignore
    return haikus

# ---------------------------- Storage (SQLite) ---------------------------- #

def ensure_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS haikus (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               sig TEXT UNIQUE,
               title TEXT,
               artist TEXT,
               line1 TEXT,
               line2 TEXT,
               line3 TEXT,
               s1 INTEGER, s2 INTEGER, s3 INTEGER,
               tweeted_at TEXT
           )"""
    )
    con.commit()
    con.close()

def cache_haiku(h: Haiku):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO haikus (sig, title, artist, line1, line2, line3, s1, s2, s3) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (h.signature(), h.title, h.artist, h.lines[0], h.lines[1], h.lines[2], h.syllables[0], h.syllables[1], h.syllables[2])
    )
    con.commit()
    con.close()

def load_one_unused_haiku() -> Optional[Haiku]:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT title, artist, line1, line2, line3, s1, s2, s3 FROM haikus WHERE tweeted_at IS NULL ORDER BY RANDOM() LIMIT 1")
    row = cur.fetchone()
    con.close()
    if not row:
        return None
    return Haiku(title=row[0], artist=row[1], lines=(row[2], row[3], row[4]), syllables=(row[5], row[6], row[7]))

def mark_tweeted(h: Haiku):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("UPDATE haikus SET tweeted_at=? WHERE sig=?", (datetime.now(timezone.utc).isoformat(), h.signature()))
    con.commit()
    con.close()

# ---------------------------- Twitter/X posting --------------------------- #

def load_keys():
    load_dotenv()
    return dict(
        api_key=os.getenv("X_API_KEY"),
        api_secret=os.getenv("X_API_SECRET"),
        access_token=os.getenv("X_ACCESS_TOKEN"),
        access_secret=os.getenv("X_ACCESS_TOKEN_SECRET"),
        dry_run=os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes", "y")
    )

def make_client(keys):
    if tweepy is None:
        raise RuntimeError("tweepy is not installed. Run: pip install -r requirements.txt")
    client = tweepy.Client(
        consumer_key=keys["api_key"],
        consumer_secret=keys["api_secret"],
        access_token=keys["access_token"],
        access_token_secret=keys["access_secret"],
    )
    return client

def compose_tweet_text(h: Haiku, include_attribution: bool = True) -> str:
    body = h.text.strip()
    if include_attribution:
        attribution = f"\n\n— {h.title} by {h.artist}"
        candidate = body + attribution
        # X hard limit is typically 280 chars for standard accounts
        if len(candidate) <= 280:
            return candidate
        # If too long, try without artist first, then without attribution
        candidate2 = body + f"\n\n— {h.title}"
        if len(candidate2) <= 280:
            return candidate2
    # Fallback to body only
    return body[:280]

def post_tweet(text: str, client) -> str:
    resp = client.create_tweet(text=text)
    # Return the URL-friendly id
    return str(resp.data.get("id"))

# ---------------------------- CSV scanning ------------------------------- #

def scan_csv(csv_path: str) -> int:
    ensure_db()
    found = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = row.get("title") or "Unknown Title"
            artist = row.get("artist") or "Unknown Artist"
            lyrics = row.get("lyrics") or ""
            for h in find_haikus_in_lyrics(title, artist, lyrics):
                cache_haiku(h)
                found += 1
    return found

# ---------------------------- CLI ---------------------------------------- #

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scan lyrics for 5-7-5 haiku and post one to X.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="Scan a CSV for haiku and cache them in SQLite")
    p_scan.add_argument("--csv", default="lyrics.csv", help="Path to CSV with columns: title,artist,lyrics")

    p_tweet = sub.add_parser("tweet", help="Pick one cached haiku and tweet it")
    p_tweet.add_argument("--no-attrib", action="store_true", help="Do not include attribution line")

    args = parser.parse_args()

    if args.cmd == "scan":
        if not os.path.exists(args.csv):
            print(f"CSV not found: {args.csv}")
            print("Tip: copy lyrics_sample.csv to lyrics.csv and try again.")
            return
        count = scan_csv(args.csv)
        print(f"Scanned {args.csv}. Found and cached {count} haiku triplets.")
        print(f"Database: {DB_PATH}")
        return

    if args.cmd == "tweet":
        ensure_db()
        h = load_one_unused_haiku()
        if not h:
            print("No unused haiku found. Run 'scan' first, or add more lyrics.")
            return
        text = compose_tweet_text(h, include_attribution=not args.no_attrib)
        keys = load_keys()
        if keys["dry_run"]:
            print("DRY_RUN is enabled. Here is what would be tweeted:\n")
            print(text)
            mark_tweeted(h)  # mark to avoid repeats even in dry run
            print("\nMarked as tweeted in the local database.")
            return
        client = make_client(keys)
        tweet_id = post_tweet(text, client)
        mark_tweeted(h)
        print(f"Tweeted haiku. Tweet ID: {tweet_id}")

if __name__ == "__main__":
    main()
