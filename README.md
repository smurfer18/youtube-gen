# ytgen — automatic faceless YouTube videos

`ytgen` turns a topic (or your own script or voiceover) into a finished, publishable
YouTube video. It does the research, writing, fact-checking, narration, licensed footage,
motion graphics, word-synced captions, an original score and SFX, the final mix, the
render, three thumbnails, and a complete YouTube pack (title options, description with
chapters, sources and footage credits, tags, SRT/VTT). It makes long-form 16:9
documentaries and 9:16 Shorts. It is a pipeline of small, replaceable parts: writers,
voices, media sources, graphics and themes are all plug-ins.

The design comes from studying four commercial tools (Kutly, ViCut, GoMotion, Wyrlo) with
[REA](https://github.com/morluto/rea). See [docs/research.md](docs/research.md) for the
evidence and the decisions it led to.

```bash
pip install -e .            # Python 3.10+, plus ffmpeg on PATH
ytgen doctor                # checks ffmpeg, keys, network

ytgen make "The Apollo 13 rescue" --minutes 8            # long-form documentary
ytgen make "Why octopuses are so smart" --format short    # 9:16 Short
ytgen make --script examples/apollo13.md --theme orbit    # your own script
```

Output lands in `projects/<date>-<slug>/output/`: the MP4, `thumbnail_A/B/C.jpg`,
`description.txt`, `title.txt`, `metadata.json` (YouTube API-ready), `.srt` and `.vtt`.

## What makes the videos good

| | |
|---|---|
| **The word clock drives the edit** | Every spoken word is timed from TTS word boundaries or Whisper. Cuts land just before the word that starts a shot. Captions highlight each word as it's spoken. A counter starts counting on the number's word, and bars and list items land on the words that name them. |
| **Not a slideshow** | Shot lengths vary (3.5–7 s long-form, 1.6–3 s Shorts) and snap to sentence starts. Ken Burns moves aim at each image's visual focus. Portrait or low-res images become tilted photo prints on a blurred copy of themselves. Footage, motion graphics and kinetic typography are mixed. |
| **Motion graphics from the script** | Eight graphic types: big number, bar chart, line chart, timeline, list, quote, compare and kinetic type. The writer declares them, or the planner detects figures and year runs in the narration. They show only numbers the narrator says. |
| **Licensed footage with provenance** | Sources are Wikimedia Commons, the NASA library, Openverse, Pexels (photos and video), Pixabay and your own folder. NC/ND licenses are rejected. Every shot keeps its author, license and page URL, and they go into the description. Shots are de-duplicated across the video, and weak matches are flagged for review. |
| **Themes are data** | Six original looks: `noir`, `archive`, `orbit`, `ledger`, `atlas`, `pulse`. Palette, fonts (Google Fonts, cached), grade, grain, vignette, background, frame style, caption style, transitions and motion are all YAML. Drop a new file in `ytgen/themes/` to add a look. |
| **Original sound** | A procedural score with a mood per chapter (tension, wonder, somber, driving, uplifting, neutral) and synthesized whooshes, pops, ticks, impacts and risers on the edit's cues. The voice chain is high-pass, compression and presence. Music ducks under the voice by sidechain, and two-pass loudness normalization brings the mix to −14 LUFS / −1.5 dBTP. |
| **Storyboard before render** | `--stop-after storyboard` writes `storyboard/index.html`. It shows each scene's frame, narration, timing, source, license, alternatives and review flags. Edit `plan.json`, then `ytgen render`. |
| **Ready to publish** | Title options, a description with valid YouTube chapters, sources and credits, tags, captions, A/B/C thumbnails, and an optional upload through the YouTube Data API with scheduling. |

## Pipeline

```
research ─► script ─► voice ─► plan ─► media ─► storyboard ─► audio ─► render ─► package
Wikipedia   Claude /   Edge /    scenes,  licensed   HTML        score,    parallel   thumbnails,
            extractive ElevenLabs captions shots +    review      SFX, mix  x264       SEO, SRT,
            / your file / file   graphics provenance             −14 LUFS  chunks     metadata
```

Every stage writes its output into the project directory (`script.json`, `timing.json`,
`plan.json`, `audio/mix.wav`, …). Re-running skips finished stages, so you can edit any
artifact and continue:

```bash
ytgen make "The Tunguska event" --stop-after storyboard
# open projects/…/storyboard/index.html, edit projects/…/plan.json
ytgen render projects/20261103-the-tunguska-event
ytgen render projects/… --force voice      # e.g. after changing --voice
```

## Providers and keys (all optional)

| Capability | Default (no key) | With a key |
|---|---|---|
| Script | Extractive writer: sourced sentences from Wikipedia | `ANTHROPIC_API_KEY`: Claude writes the full storyboard script with graphics, moods, lower thirds and packaging, then fact-checks it against the research (`pip install 'ytgen[claude]'`) |
| Voice | Edge neural voices with word timing (`ytgen voices`) | `ELEVENLABS_API_KEY` → `--voice elevenlabs:<voice_id>` |
| Your narration | — | `--voice file:narration.wav` (aligned with faster-whisper, `pip install 'ytgen[whisper]'`) |
| Footage | Wikimedia Commons, NASA, Openverse (`--media …,openverse`) | `PEXELS_API_KEY` (photos and video b-roll), `PIXABAY_API_KEY` |
| Publishing | — | `YOUTUBE_CLIENT_ID/SECRET/REFRESH_TOKEN` (`ytgen youtube-auth client_secret.json`) |

## Your own script

Markdown with optional directives. See [examples/apollo13.md](examples/apollo13.md):

```markdown
---
title: "Apollo 13: The 87 Hours That Saved Three Lives"
thumbnail_text: "248,655 MILES FROM HOME"
tags: [apollo 13, nasa]
---
## The Explosion {mood=tension}

[[visual: Apollo 13 service module damage]]
[[place: Mission Control · Houston, Texas]]
During a routine stir of oxygen tank number two, damaged wiring inside the tank sparked.

[[number: 248,655 | miles from Earth | anchor=two]]
On April 14, the crew was two hundred forty-eight thousand miles from Earth.
```

Directives: `visual`, `place` (lower third), `emphasis`, `number`, `chart`, `line`,
`timeline`, `list`, `quote`, `compare` and `kinetic`. A directive on its own line applies
to the paragraph that follows it. JSON or YAML scripts that follow the `Script` model in
`ytgen/models.py` also work.

## Extending

```python
from ytgen.media import register            # new footage source (AI images, AI video, a DAM, …)

@register("mysource")
class MySource:
    name = "mysource"
    def __init__(self, settings): ...
    def enabled(self): return True
    def search(self, query, vertical, limit=12): return [Asset(...)]
```

Writers (`ytgen.writers.register`) and voices (`ytgen.voice.register`) use the same
pattern. Graphic renderers live in `ytgen/render/scenes.py` (`GRAPHICS`). Themes are YAML
files.

## Commands

```
ytgen make [topic] [--script F] [--format long|short] [--minutes N] [--theme T] [--voice V]
           [--captions boxed|karaoke|pop|minimal] [--media a,b] [--channel NAME] [--stop-after STAGE]
ytgen render PROJECT [--force STAGE…]     continue / re-render after edits
ytgen batch topics.txt [--format short]  one video per line
ytgen preview PROJECT --at 42.0          one frame as JPEG
ytgen upload PROJECT [--privacy …] [--publish-at RFC3339]
ytgen themes | voices [--lang en] | doctor | youtube-auth client_secret.json
```

## Notes

* Rendering is CPU-bound. Frames are composited with Pillow/NumPy in parallel processes,
  each feeding its own x264 encoder, and the segments are joined losslessly. Use
  `--workers` and `--preset` to trade speed for quality.
* Wikimedia rate-limits aggressive clients. `ytgen` paces requests, never sleeps on
  `Retry-After`, and briefly rests a throttled provider instead of stalling.
* Check facts and licenses before publishing. The storyboard flags weak shot matches, and
  `metadata.json` lists the scenes that need review.
