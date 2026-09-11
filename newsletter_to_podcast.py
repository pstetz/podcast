#!/usr/bin/env python3
"""
newsletter_to_podcast.py

Turn a folder of newsletter emails (.eml or Outlook .msg) into natural-sounding
MP3 "episodes". Two TTS engines are supported:

  --engine edge     Microsoft's free neural voices (default). Good quality,
                     zero setup beyond pip install, but calls out to
                     Microsoft's service for every episode.

  --engine kokoro    hexgrad/Kokoro-82M, an open-weight model hosted on
                     Hugging Face. Downloads once (~330MB) then runs fully
                     offline on your own CPU/GPU — no per-request network
                     calls, no rate limits, good fit for a 50-episode batch.

ONE-TIME SETUP
    pip install edge-tts beautifulsoup4 extract-msg mutagen
    # only if you want the kokoro engine too:
    pip install kokoro soundfile
    # you'll also need ffmpeg on your PATH (brew install ffmpeg / apt install ffmpeg)

USAGE
    # See natural-sounding Edge voices to choose from
    python newsletter_to_podcast.py --list-voices

    # Try it on just the first 2 emails before running all 50
    python newsletter_to_podcast.py --input ./emails --output ./episodes --limit 2

    # Convert everything with Edge (cloud, default)
    python newsletter_to_podcast.py --input ./emails --output ./episodes \
        --voice en-US-AndrewNeural --rate +0%

    # Convert everything with Kokoro (local, offline after first download)
    python newsletter_to_podcast.py --input ./emails --output ./episodes \
        --engine kokoro --voice af_heart

Put every .eml / .msg file you want turned into an episode into one folder
(--input) and run the script. Numbered, tagged MP3s land in --output, ready
to AirDrop to your phone or drag into Apple Music/Files.
"""

import argparse
import asyncio
import email
from datetime import datetime
from email import policy
from email.utils import parsedate_to_datetime
import re
from pathlib import Path

from bs4 import BeautifulSoup
import edge_tts

try:
    from kokoro import KPipeline
    import soundfile as sf
    HAVE_KOKORO = True
except ImportError:
    HAVE_KOKORO = False

try:
    import extract_msg
    HAVE_MSG = True
except ImportError:
    HAVE_MSG = False

try:
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, TRCK, USLT
    HAVE_MUTAGEN = True
except ImportError:
    HAVE_MUTAGEN = False


ABBREVIATIONS = ["Mr.", "Mrs.", "Ms.", "Dr.", "U.S.", "U.K.", "Inc.", "Corp.",
                  "Ltd.", "vs.", "etc.", "e.g.", "i.e.", "Jr.", "Sr.", "St.", "No.", "Co."]


def _protect_abbrevs(text):
    for i, a in enumerate(ABBREVIATIONS):
        text = text.replace(a, a.replace(".", f"@@{i}@@"))
    return text


def _restore_abbrevs(text):
    for i, a in enumerate(ABBREVIATIONS):
        text = text.replace(f"@@{i}@@", ".")
    return text


def clean_text(text):
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def split_sentences(text):
    text = clean_text(text)
    protected = _protect_abbrevs(text)
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z0-9"\u201c\'])', protected)
    return [_restore_abbrevs(p).strip() for p in parts if p.strip()]


def extract_sections(soup):
    """
    Best-effort generic extraction: bold/heading tags become section titles
    (this is how Bloomberg's Money Stuff and many similar newsletters mark
    their section breaks). Falls back to a single unnamed section if none
    are found. Also trims common footer junk (link roundups, subscribe/
    unsubscribe boilerplate) so it doesn't get read aloud.
    """
    headers = []
    for tag in soup.find_all(["h1", "h2", "h3", "strong", "b"]):
        t = tag.get_text(strip=True)
        if t and 3 < len(t) < 70 and not t.lower().startswith(
            ("subscribe", "unsubscribe", "follow us", "want to sponsor")
        ):
            headers.append(t)

    full_text = soup.get_text(separator=" ", strip=True)

    seen = []
    used = set()
    for h in headers:
        if h in used:
            continue
        idx = full_text.find(h)
        if idx != -1:
            seen.append((h, idx))
            used.add(h)
    seen.sort(key=lambda x: x[1])

    cutoffs = ["Things happen", "Follow Us", "Like getting this newsletter"]
    cutoff_idx = len(full_text)
    for c in cutoffs:
        i = full_text.find(c)
        if i != -1:
            cutoff_idx = min(cutoff_idx, i)

    seen = [(h, i) for h, i in seen if i < cutoff_idx]

    if not seen:
        return [("", full_text[:cutoff_idx].strip())]

    sections = []
    for i, (title, start) in enumerate(seen):
        content_start = start + len(title)
        content_end = seen[i + 1][1] if i + 1 < len(seen) else cutoff_idx
        body = full_text[content_start:content_end].strip()
        if body:
            sections.append((title, body))
    return sections


def _received_date(path, header_value):
    """Parse the email's Date header; fall back to the file's mtime if
    missing/unparseable so every episode still gets a usable date."""
    if header_value:
        try:
            dt = parsedate_to_datetime(header_value)
            if dt is not None:
                return dt
        except (TypeError, ValueError):
            pass
    return datetime.fromtimestamp(path.stat().st_mtime)


def load_eml(path):
    with open(path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=policy.default)
    subject = msg["subject"] or path.stem
    date = _received_date(path, msg["date"])
    html = plain = None
    for part in msg.walk():
        if part.get_content_type() == "text/html" and html is None:
            html = part.get_content()
        elif part.get_content_type() == "text/plain" and plain is None:
            plain = part.get_content()
    return subject, html, plain, date


def load_msg(path):
    if not HAVE_MSG:
        raise RuntimeError("Reading .msg files needs: pip install extract-msg")
    m = extract_msg.Message(str(path))
    subject = (m.subject or path.stem).replace("\x00", "").strip()
    date = m.date if isinstance(m.date, datetime) else _received_date(path, m.date)
    html = None
    if m.htmlBody:
        html = m.htmlBody.decode("utf-8", "ignore") if isinstance(m.htmlBody, bytes) else m.htmlBody
    plain = m.body
    m.close()
    return subject, html, plain, date


def build_script(subject, html, plain):
    """Return the full spoken text for one episode."""
    if html:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["style", "script"]):
            tag.decompose()
        sections = extract_sections(soup)
    elif plain:
        sections = [("", plain)]
    else:
        sections = [("", "")]

    spoken = [f"{subject}."]
    for title, body in sections:
        if not body.strip():
            continue
        sentences = split_sentences(body)
        if not sentences:
            continue
        if title:
            spoken.append(title + ".")
        spoken.extend(sentences)
    return " ".join(spoken)


def slugify(text):
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_]+", "-", text)[:80]


async def synthesize(text, out_path, voice, rate):
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    await communicate.save(str(out_path))


_kokoro_pipeline = None
_kokoro_lang_for_voice = None


def _get_kokoro_pipeline(voice):
    """Kokoro needs a pipeline per language; voice prefix picks the language
    (af_/am_ = American English, bf_/bm_ = British English, etc.)."""
    global _kokoro_pipeline, _kokoro_lang_for_voice
    lang_code = "a"  # American English
    if voice.startswith(("bf_", "bm_")):
        lang_code = "b"  # British English
    if _kokoro_pipeline is None or _kokoro_lang_for_voice != lang_code:
        # First call downloads weights from Hugging Face (hexgrad/Kokoro-82M)
        # and caches them locally (~/.cache/huggingface) — only happens once.
        _kokoro_pipeline = KPipeline(lang_code=lang_code, repo_id="hexgrad/Kokoro-82M")
        _kokoro_lang_for_voice = lang_code
    return _kokoro_pipeline


def synthesize_kokoro_sync(text, out_path, voice, speed=1.0):
    """Runs Kokoro (a local model, not a network call) — kept synchronous
    and called via asyncio.to_thread so it doesn't block other episodes."""
    import numpy as np
    pipeline = _get_kokoro_pipeline(voice)
    chunks = []
    for result in pipeline(text, voice=voice, speed=speed):
        if result.audio is not None:
            chunks.append(result.audio.numpy())
    if not chunks:
        raise RuntimeError("Kokoro produced no audio for this text")
    audio = np.concatenate(chunks)

    wav_path = out_path.with_suffix(".wav")
    sf.write(str(wav_path), audio, 24000)  # Kokoro outputs 24kHz

    import subprocess
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path),
         "-codec:a", "libmp3lame", "-qscale:a", "2", str(out_path)],
        check=True,
    )
    wav_path.unlink(missing_ok=True)


async def synthesize_kokoro(text, out_path, voice, speed=1.0):
    await asyncio.to_thread(synthesize_kokoro_sync, text, out_path, voice, speed)


def tag_mp3(path, title, artist="Money Stuff", album="Money Stuff (audio)", track=None, lyrics=None):
    if not HAVE_MUTAGEN:
        return
    try:
        audio = MP3(str(path), ID3=ID3)
        try:
            audio.add_tags()
        except Exception:
            pass
        audio.tags.add(TIT2(encoding=3, text=title))
        audio.tags.add(TPE1(encoding=3, text=artist))
        audio.tags.add(TALB(encoding=3, text=album))
        if track is not None:
            audio.tags.add(TRCK(encoding=3, text=str(track)))
        if lyrics:
            # Unsynced lyrics: readable via the Lyrics button in Apple's
            # Music app while the episode plays (not word-synced).
            audio.tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics))
        audio.save()
    except Exception as e:
        print(f"  (couldn't tag {path.name}: {e})")


def load_email_file(path):
    suffix = path.suffix.lower()
    if suffix == ".eml":
        return load_eml(path)
    elif suffix == ".msg":
        return load_msg(path)
    return None


async def process_file(path, subject, html, plain, date, out_dir, engine, voice, rate, speed, track=None):
    text = build_script(subject, html, plain)
    if len(text) < 40:
        print(f"  skipped {path.name}: not enough text extracted")
        return None

    out_name = f"{date:%Y-%m-%d}-{slugify(subject)}.mp3"
    out_path = out_dir / out_name
    if out_path.exists():
        print(f"  {path.name}  ->  {out_name}  (already done, skipping)")
        return out_path
    print(f"  {path.name}  ->  {out_name}  ({len(text)} chars)")

    if engine == "kokoro":
        await synthesize_kokoro(text, out_path, voice, speed)
    else:
        await synthesize(text, out_path, voice, rate)

    tag_mp3(out_path, title=subject, track=track, lyrics=text)
    return out_path


async def run(args):
    in_dir = Path(args.input)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.engine == "kokoro" and not HAVE_KOKORO:
        print("Kokoro isn't installed. Run: pip install kokoro soundfile")
        return

    paths = sorted(p for p in in_dir.iterdir() if p.suffix.lower() in (".eml", ".msg"))
    if not paths:
        print(f"No .eml or .msg files found in {in_dir}")
        return

    loaded = []
    for path in paths:
        try:
            result = load_email_file(path)
        except Exception as e:
            print(f"  ERROR reading {path.name}: {e}")
            continue
        if result is None:
            continue
        subject, html, plain, date = result
        loaded.append((path, subject, html, plain, date))
    loaded.sort(key=lambda item: item[4])  # chronological by received date
    # Track number = position in the full chronological run, computed before
    # any filtering so it stays stable across repeated/resumed invocations.
    numbered = [(i + 1, *item) for i, item in enumerate(loaded)]

    # Skip episodes already on disk *before* applying --limit, so
    # `--limit 1` run repeatedly (e.g. from a shell loop) always advances
    # to the next unfinished email instead of re-checking the first one.
    numbered = [
        item for item in numbered
        if not (out_dir / f"{item[5]:%Y-%m-%d}-{slugify(item[2])}.mp3").exists()
    ]

    if args.limit:
        numbered = numbered[: args.limit]
    if not numbered:
        print("No emails could be read.")
        return

    concurrency = args.concurrency
    if args.engine == "kokoro" and args.concurrency > 2:
        print("Note: kokoro runs on your CPU/GPU locally — capping concurrency at 2 "
              "to avoid overloading your machine. Pass --concurrency to override.")
        concurrency = min(concurrency, 2)

    print(f"Found {len(numbered)} email(s) to convert. Engine: {args.engine}  Voice: {args.voice}\n")
    sem = asyncio.Semaphore(concurrency)

    async def worker(item):
        track, path, subject, html, plain, date = item
        async with sem:
            try:
                await process_file(path, subject, html, plain, date, out_dir,
                                    args.engine, args.voice, args.rate, args.speed,
                                    track=track)
            except Exception as e:
                print(f"  ERROR on {path.name}: {e}")

    await asyncio.gather(*(worker(item) for item in numbered))
    print(f"\nDone. Episodes saved to {out_dir}/")


async def list_voices():
    voices = await edge_tts.list_voices()
    english = [v for v in voices if v["Locale"].startswith("en")]
    for v in sorted(english, key=lambda v: v["Locale"]):
        print(f"{v['ShortName']:35s} {v['Gender']:8s} {v['Locale']}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--input", help="Folder containing .eml / .msg files")
    parser.add_argument("--output", default="./episodes", help="Folder to write MP3s to")
    parser.add_argument("--engine", choices=["edge", "kokoro"], default="edge",
                         help="'edge' = Microsoft's free cloud voices (needs internet each run). "
                              "'kokoro' = open-weight model from Hugging Face "
                              "(hexgrad/Kokoro-82M), downloaded once and then runs fully "
                              "offline on your machine.")
    parser.add_argument("--voice", default=None,
                         help="Voice name. Edge: see --list-voices (default en-US-AndrewNeural). "
                              "Kokoro: e.g. af_heart, af_bella, am_michael, bf_emma, bm_george "
                              "(default af_heart).")
    parser.add_argument("--rate", default="+0%",
                         help="[edge only] Speech rate, e.g. +10%%, -15%%")
    parser.add_argument("--speed", type=float, default=1.0,
                         help="[kokoro only] Speed multiplier, e.g. 1.1")
    parser.add_argument("--limit", type=int, default=None,
                         help="Only process the first N files (good for testing)")
    parser.add_argument("--concurrency", type=int, default=3,
                         help="How many files to synthesize at once")
    parser.add_argument("--list-voices", action="store_true",
                         help="[edge only] List natural-sounding English voices and exit")
    args = parser.parse_args()

    if args.voice is None:
        args.voice = "af_heart" if args.engine == "kokoro" else "en-US-AndrewNeural"

    if args.list_voices:
        asyncio.run(list_voices())
        return

    if not args.input:
        parser.error("--input is required (unless using --list-voices)")

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
