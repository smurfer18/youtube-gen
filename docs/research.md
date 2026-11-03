# Reference product research (REA)

This document records what was learned from four commercial faceless-video
generators, and how each finding shaped `ytgen`. Nothing here was copied:
the products' code is closed and lives behind sign-in. We studied only their
public marketing pages and the public JavaScript those pages ship, then
designed an original system.

## Method

1. Headless Chromium was started with a loopback CDP port and each landing page
   was loaded (`networkidle`, full scroll).
2. **REA 6.8.0 `analyze-web-bundle`** captured and statically analyzed every
   script each page shipped (3 s observation window, source maps where
   referenced).
3. Visible page copy was extracted from the same browser session, and the few
   feature-relevant public scripts REA identified were read as data.

| Site | REA Evidence ID | Scripts analyzed | AST nodes | Coverage |
|---|---|---|---|---|
| kutly.io | `ev_b402f43a7f56cad99923fa48733a3de1bad349d7620c4d37d850cb20aa2c79f6` | 15 | 138,560 | partial (3 source maps included) |
| vicut.io | `ev_9cdc2907a20e4c4ef3cd44a8309e43b1ed58a0e60ecd0463f44a07201d139a50` | 6 | 13,694 | complete |
| gomotionai.com | `ev_31f73eae04fd6811bc4e4b701c92f9a27792d7d3f131851392e267d6e5616123` | 6 | 17,207 | partial |
| wyrlo.com | `ev_3777cd65fae039b48d3e936e914ac723c313b4988bfa1e8cb81056598d1b8a45` | 19 | 105,459 | partial |

**What REA observed:** all four public bundles are marketing front-ends (Astro,
Next.js/Turbopack, and hand-written scripts). REA found no application routes
and only one first-party endpoint (`/api/submit-lead` on kutly.io, a lead form).
The generation pipelines run server-side behind authentication.

**What follows from that:** what the pipelines do inside cannot be observed
from public artifacts. Everything below about pipeline stages comes from the
products' own published descriptions, not from code. The one exception is
vicut.io's public `landing/themes/themes-data.js`. It is a declarative theme
specification (palette, fonts, frame/plate/collage/gauge styles, grade,
texture, and a per-element motion spec) and confirms that their looks are
data-driven.

**Not investigated:** anything behind sign-in, server code, and render
internals.

## Feature matrix (as published by each product)

| Capability | Kutly | ViCut | GoMotion | Wyrlo |
|---|---|---|---|---|
| Input | prompt / own voiceover | idea / own voiceover | finished script | topic / script |
| Research + citations | "fact checker" agent | source for every figure | – | optional web research |
| Script length | up to 30 min | 8–35 min | up to 1 h+ | Shorts + long-form |
| Voices | AI + voice clone | 36 voices, EN/ES/DE | 100+ voices, 14 langs | ElevenLabs library |
| Visuals | footage + motion design | Commons, Openverse, Pexels, Pixabay, USGS (licensed) | real footage per sentence | AI video (22 models), AI images, stock, 29 graphic types |
| Storyboard review | – | every scene, swap shots, flags | – | storyboard, mix presets |
| Motion graphics | keyframed, eased | chapter, photo card, number plate, big number, bar/line chart | zoom punch, kinetic text, counters, map reveal | bar race, this-vs-that, icon count, map, timeline, big number, list, name card, funnel, article scene |
| Captions | – | burned, per-theme | karaoke, word-synced | pop, karaoke, boxed, keyword, sentence |
| Themes | – | 8 declarative themes | – | 22 styles, 5 graphic looks |
| Music / SFX | – | scored per chapter mood | soundtrack + SFX, ducked | mood track + sound on every cut |
| Thumbnails | A/B ×3 | 3 options | AI thumbnails | AI thumbnail |
| YouTube pack | title, description, tags | MP4, SRT, title, description, chapters, credits, tags | MP4 | title, description, tags, scheduled publish |
| Formats | 16:9 | 16:9 | 16:9 | 9:16 + 16:9 |

## Design decisions for ytgen (where we aim higher)

1. **The word clock drives everything.** Every word's spoken time (from TTS word
   boundaries) drives the scene cuts, caption highlights, graphic beats (a counter
   starts on the number's word) and SFX hits. All four products describe this
   ("cut to the word", "on the word").
2. **Licensed footage only, with provenance.** Like ViCut: each shot records its
   source, author, license and URL. Non-commercial and no-derivatives licenses are
   rejected, and credits go into the description automatically.
3. **Graphics come from the script.** Numbers, comparisons, timelines, lists and
   quotes in the script become motion graphics. Writers can declare them
   explicitly, or the planner detects them. The graphic always shows what the
   voice says.
4. **Declarative themes.** Palette, typography, grade, texture, frame style,
   caption style, transitions and motion timings live in YAML. New looks need no
   code.
5. **Storyboard before render.** The pipeline can stop after planning and write
   an HTML storyboard with every shot, its license and alternatives. Edit
   `plan.json`, then render.
6. **Original sound.** A procedural, mood-per-chapter score and synthesized SFX
   are royalty-free by construction. The voice is ducked under the music and the
   mix is loudness-normalized to YouTube's −14 LUFS.
7. **A complete YouTube pack.** MP4, SRT/VTT, three thumbnail variants, title
   options, a description with chapters, sources and credits, and tags. Optional
   API upload.
8. **Pluggable everything.** Writers, voices, media sources, graphics and themes
   are registries, so new providers (AI video, AI images, other TTS) slot in
   without touching the pipeline.
9. **No "inauthentic content" look.** Shot lengths vary, Ken Burns moves are
   deterministic but varied, and graphic, footage and kinetic scenes are mixed
   by pacing rules, not a fixed template. Wyrlo stresses this point about
   YouTube's inauthentic-content policy.
