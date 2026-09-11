# Future work

Not implemented yet — logged here per request instead of rushed. Existing
episodes in `episodes/` should not be touched by either of these; only test
against emails that haven't been converted yet.

## 1. Multi-voice narration (detect quoted speech, switch voice)

Goal: when the newsletter text quotes another person/article, read that
span in a second voice instead of the narrator voice.

Rough approach:
- In `build_script`/a new pass, split each sentence into (speaker, text)
  segments. Heuristic: text inside `"..."` or `“...”` that is preceded/
  followed by an attribution pattern (`, X said`, `X said,`, `according to
  X`, etc.) gets tagged as "quote"; everything else is "narrator".
- Kokoro (`synthesize_kokoro_sync`) already takes a `voice` param per call —
  synthesize narrator segments with `af_bella` and quote segments with a
  second voice (e.g. `am_michael`), then concatenate the resulting audio
  arrays in order before writing the mp3 (silence gap between segments to
  avoid clipped transitions).
- Edge case to watch: Money Stuff quotes long block excerpts from other
  articles/filings — those should probably also switch voice even without
  a "X said" attribution right next to them (may need a simpler heuristic:
  any blockquote/indented HTML block = quote voice).
- Test only on not-yet-converted episodes before considering it done.

## 2. iOS listening app

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
