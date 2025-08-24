# Haiku Lyrics Tweeter

This folder contains a ready-to-run Python bot that scans a CSV of song lyrics for accidental haiku (5-7-5 syllables) and posts one per day to X (Twitter) using Tweepy.

See the chat for the full step-by-step guide. Quick start:

1. Python 3.10+ is recommended.
2. `python3 -m venv .venv && source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. Copy `.env.example` to `.env` and fill in your X API keys.
5. Put your lyrics in `lyrics.csv` in this folder, headers: title,artist,lyrics
6. Scan: `python haiku_tweeter.py scan --csv lyrics.csv`
7. Dry run a tweet: `python haiku_tweeter.py tweet`
8. Remove `DRY_RUN` or set it to `false` in `.env` when ready to actually post.
9. Use cron to run `python haiku_tweeter.py tweet` once per day.

**Legal**: Posting lyrics you do not own can infringe copyright. Get the right to post, or keep DRY_RUN enabled for testing.
