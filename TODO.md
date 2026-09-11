# Future work

Not implemented yet — logged here per request instead of rushed. Existing
episodes in `episodes/` should not be touched; only test against emails
that haven't been converted yet.

Multi-voice narration for quoted/attributed text (Kokoro only) is done —
see `build_script_segments`, `is_quote_sentence`, and the `--quote-voice`
flag in `newsletter_to_podcast.py`. Current heuristic: a sentence fully
wrapped in quote marks, or one with a large quoted span plus a nearby
attribution word (said/wrote/according to/etc.), is read in `--quote-voice`
(default `am_michael`) instead of the narrator voice. Known gap: quotes
that span multiple sentences without per-sentence quote marks (e.g. a
block excerpt from a filing formatted as its own paragraph) aren't
detected — would need an HTML-level check (blockquote/indented block) to
catch those.

## iOS listening app

Goal: an app (or lightweight web app) that:
- Plays the mp3 episodes
- Shows the transcript text, ideally scrolling/highlighting in sync with
  playback (needs per-sentence or per-word timestamps — Kokoro's pipeline
  yields audio in chunks per input segment, so timestamps can be derived
  by tracking cumulative sample counts per chunk during synthesis and
  saving them as a sidecar `.json`/`.vtt` per episode)
- Lets you change playback speed
- Saves/restores listening progress per episode

Options to weigh later:
- Native SwiftUI app (most control, most work, needs Xcode/Apple dev
  account to install on your phone)
- Simple local web app (HTML/JS `<audio>` element with a synced transcript
  panel) opened in Safari — much less work, no App Store/signing needed,
  but "installed app" feel is weaker (can still add to Home Screen)
- Reuse an existing podcast app that already supports variable speed +
  progress-saving (e.g. Overcast, Apple Podcasts via self-hosted RSS feed)
  and separately solve "see the words" via the embedded lyrics tag
  (already added) rather than building sync-highlighting from scratch

Recommend starting with the self-hosted RSS + existing podcast app route
for speed/progress-saving (free, zero-build), and only build a custom
player if the synced-transcript-highlighting is a hard requirement.
