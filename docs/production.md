# Production engine: research → implementation

What was researched, what was adopted, and where it lives in the code.

## Rendering & motion graphics

| Option | Finding | Decision |
|---|---|---|
| **FFmpeg** | The industry baseline for encode, concat, filters, loudness and analysis | Core: x264 chunk encodes, lossless concat, `loudnorm` 2-pass, `sidechaincompress`, `blackdetect`/`freezedetect`/`ebur128` QC (`render/compositor.py`, `audio/mix.py`, `qc.py`) |
| **HyperFrames** (HeyGen, Apache-2.0) | HTML + GSAP → MP4 with headless Chrome. Its seek model only animates DOM properties (opacity, x, y, scale, rotation, …), so canvas callbacks don't run during a seek | Integrated for DOM compositions. The cinematic title sequence is `render/html/title.html`, rendered by the pinned CLI (`hyperframes@0.9.6`, installed into the cache) |
| **Playwright frame-stepping** | Pages that expose `renderAt(t)` render deterministically, one screenshot per frame | Integrated for procedural canvas/D3 scenes: the 3-D globe flight (`render/html/globe.html` + `stepper.cjs`, D3 + world-atlas / Natural Earth) |
| **Remotion** | High quality, but a paid company license is required once 4+ people are involved | Not adopted, to keep the project license-clean. The HTML engine above covers the same ground |
| **MoviePy / MoneyPrinterTurbo-style stacks** | Simple pipelines: stock clips, a music bed, subtitles | Their weakest points (slideshow feel, generic footage) are what ytgen is designed to beat |
| **Pillow/NumPy compositor** | Fast, dependency-light, fully controllable | Core frame engine: Ken Burns, photo prints with parallax, 8 data-graphic types, captions, overlays, 10 transitions |

## Editing techniques implemented

These come from how professional documentary channels cut. Each one maps to code:

- **Cut on the word.** Shots start 80 ms before the word that begins them. Graphic beats, such as a counter starting or a bar growing, fire on the word that says them (`planner.py`).
- **Varied shot rhythm.** Shot length is drawn from the preset's range and snapped to sentence starts. Transitions come from a weighted mix (crossfade, cut, whip, light leak, zoom-through, push, dip) rather than one effect repeated (`presets/*.yaml`).
- **Cold open → title sequence → chapters.** The hook plays first, then a title sequence in a deliberate pause (riser + impact), then chapter cards with a dip-to-black (`planner.py`, `voice/__init__.py` title gap).
- **Place establishing shots.** When the story moves to a new place, the globe flies there before the footage (`maps` in presets).
- **Parallax and camera life.** Photo prints drift against their blurred backdrop. Tense and driving chapters get a subtle handheld drift (cinematic preset). Letterboxing is 2.39:1.
- **Retention devices.** Chapter progress bar, lower thirds, a subscribe banner around 22% in, word-highlighted captions, and context SFX: radio chirps, launch rumble, alarms and impacts on the words that call for them.
- **Sound design.** Procedural score per chapter mood. An ambience bed fills dead air. The music ducks under the voice by sidechain, and the mix is normalized to −14 LUFS with a −1.5 dBTP ceiling.

## Media intelligence

- **Sources:** Wikimedia Commons, NASA, Internet Archive (public domain or CC only), Openverse; Pexels, Pixabay, Unsplash and Smithsonian Open Access with keys; a local folder.
- **Semantic matching:** CLIP ViT-B/32 (quantized ONNX, no PyTorch) re-ranks candidates by how well the image actually shows the query. Below a similarity floor, the selector tries a broader query instead of using a wrong shot (`media/semantic.py`).
- **Licenses:** every asset is classified (public_domain, cc0, attribution, share_alike, platform, owned, ai_generated, uncertain, rejected). Uncertain and rejected assets are never used unless you pass `--allow-uncertain`. `output/assets_manifest.json` and `CREDITS.md` list every asset with its provenance and the times it appears (`media/license.py`, `media/manifest.py`).
- **Library:** a shared SQLite + FTS5 library reuses footage across projects and catches near-duplicates with perceptual hashes (`media/library.py`).
- **AI fallback:** free Pollinations output (flagged for review), or paid OpenAI/Replicate within `--ai-budget`. Every call goes into `costs.json` (`media/generate.py`).

## Long-form architecture (15–60+ minutes)

- **Script:** Claude plans an outline (arc, facts per chapter, word budgets), then writes each chapter with a running summary and a list of facts already used. Repeated beats are removed and each chapter is fact-checked separately (`writers/claude.py`).
- **Render:** chunks are aligned to chapter boundaries and at most 20 s long. Each chunk's file name is a hash of everything that affects its pixels, so after an edit only the changed chunks are redrawn. Chunks are retried, a failing frame never kills a chunk, progress goes to `render/progress.json`, and `--chapters 3,4` renders and exports chapters on their own.
- **QC:** every run ends with automated checks and `qc_report.json`. Run it any time with `ytgen qc PROJECT`.
- **Scaling:** the same chunk scheduler can be spread across machines, because chunks are independent and content-addressed. The parts are already in place; a 60-minute film is 180 chunks.
