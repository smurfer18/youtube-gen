"""Data model shared by every pipeline stage.

The pipeline is a chain of pure-ish transforms over these models, each persisted
as JSON in the project directory so any stage can be inspected, hand-edited and
resumed:

    Script  ->  Timing  ->  Plan  ->  (frames, audio)  ->  Package
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

GraphicType = Literal[
    "big_number",   # a figure that counts up, with a label
    "bar_chart",    # labelled bars that grow one by one
    "line_chart",   # a series that draws itself
    "timeline",     # dated events along a line
    "list",         # numbered items that land one by one
    "quote",        # a pull quote with attribution
    "compare",      # this vs that, two columns
    "kinetic",      # animated keywords (typography scene)
]

SceneKind = Literal["footage", "graphic", "chapter", "kinetic", "title", "outro"]


# ----------------------------------------------------------------- script ----


class GraphicItem(BaseModel):
    label: str
    value: Optional[float] = None
    display: Optional[str] = None  # how the value is printed, e.g. "$1.2B"


class GraphicSpec(BaseModel):
    type: GraphicType
    title: str = ""
    value: Optional[float] = None    # big_number: the numeric target
    display: Optional[str] = None    # big_number: printed form, e.g. "205,000"
    prefix: str = ""                 # e.g. "$"
    suffix: str = ""                 # e.g. "%", " km"
    label: str = ""                  # caption under a number / quote author
    items: list[GraphicItem] = Field(default_factory=list)
    unit: str = ""
    source: str = ""                 # "Source: NASA" footer
    anchor: str = ""                 # word in the narration the beat lands on


class Beat(BaseModel):
    """One paragraph of narration and what the viewer should see during it."""

    text: str
    visual: Optional[str] = None          # footage search query
    graphic: Optional[GraphicSpec] = None  # a motion graphic for this beat
    lower_third: Optional[str] = None      # e.g. "Houston, Texas · April 1970"
    emphasis: list[str] = Field(default_factory=list)  # caption keywords


class Chapter(BaseModel):
    title: str
    mood: str = "neutral"   # drives the score: tension, wonder, somber, driving, uplifting, neutral
    beats: list[Beat]


class Source(BaseModel):
    title: str
    url: str = ""


class Script(BaseModel):
    title: str
    topic: str = ""
    language: str = "en"
    title_options: list[str] = Field(default_factory=list)
    thumbnail_text: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    chapters: list[Chapter]
    sources: list[Source] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)  # fact-check notes, warnings

    def all_text(self) -> str:
        return "\n\n".join(b.text for c in self.chapters for b in c.beats)

    def word_count(self) -> int:
        return len(self.all_text().split())


# ----------------------------------------------------------------- timing ----


class Word(BaseModel):
    text: str
    start: float
    end: float
    chapter: int
    beat: int        # global beat index
    sentence: int    # global sentence index


class Sentence(BaseModel):
    text: str
    start: float
    end: float
    chapter: int
    beat: int


class Timing(BaseModel):
    duration: float
    words: list[Word]
    sentences: list[Sentence]
    chapter_starts: list[float]
    voice_path: str
    lead_in: float = 0.0


# ------------------------------------------------------------------- plan ----


class Asset(BaseModel):
    id: str
    kind: Literal["image", "video"] = "image"
    path: str = ""                 # local file once downloaded
    url: str = ""                  # download URL
    provider: str = ""
    title: str = ""
    author: str = ""
    license: str = ""
    license_url: str = ""
    page_url: str = ""
    width: int = 0
    height: int = 0
    query: str = ""
    score: float = 0.0
    duration: float = 0.0          # videos


class Motion(BaseModel):
    """Ken Burns move for an image (normalized to the cover-cropped frame)."""

    zoom_from: float = 1.0
    zoom_to: float = 1.08
    pan_from: tuple[float, float] = (0.5, 0.5)
    pan_to: tuple[float, float] = (0.5, 0.5)
    frame: Literal["full", "card"] = "full"
    tilt: float = 0.0


class Scene(BaseModel):
    index: int
    kind: SceneKind
    start: float
    end: float
    chapter: int = 0
    text: str = ""
    query: str = ""
    asset_id: Optional[str] = None
    alternatives: list[str] = Field(default_factory=list)
    graphic: Optional[GraphicSpec] = None
    title: str = ""                  # chapter / title scenes
    subtitle: str = ""
    lower_third: Optional[str] = None
    lower_third_at: float = 0.0      # seconds into the scene
    beat_times: list[float] = Field(default_factory=list)  # absolute times graphic beats land
    motion: Motion = Field(default_factory=Motion)
    transition: str = "crossfade"
    needs_review: bool = False
    review_reason: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


class CaptionWord(BaseModel):
    text: str
    start: float
    end: float
    emphasis: bool = False


class CaptionPhrase(BaseModel):
    start: float
    end: float
    words: list[CaptionWord]


class SfxCue(BaseModel):
    t: float
    kind: str      # whoosh, pop, tick, impact, riser, shutter
    gain_db: float = 0.0


class MusicSection(BaseModel):
    start: float
    end: float
    mood: str


class ChapterMark(BaseModel):
    title: str
    start: float


class Plan(BaseModel):
    width: int
    height: int
    fps: int
    duration: float
    theme: str
    caption_style: str
    scenes: list[Scene]
    assets: dict[str, Asset] = Field(default_factory=dict)
    captions: list[CaptionPhrase] = Field(default_factory=list)
    sfx: list[SfxCue] = Field(default_factory=list)
    music: list[MusicSection] = Field(default_factory=list)
    chapters: list[ChapterMark] = Field(default_factory=list)
    banner_at: Optional[float] = None   # subscribe banner time
    channel_name: str = ""
