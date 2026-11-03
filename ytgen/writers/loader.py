"""Load a user-written script: JSON/YAML (the Script model) or annotated Markdown.

Markdown format (see examples/apollo13.md)::

    ---
    title: How NASA Saved Apollo 13
    thumbnail_text: 200,000 MILES FROM HOME
    tags: [apollo 13, nasa]
    sources:
      - {title: Apollo 13 - Wikipedia, url: https://en.wikipedia.org/wiki/Apollo_13}
    ---
    ## The Explosion {mood=tension}

    [[visual: Apollo 13 service module damage]]
    [[place: 205,000 miles from Earth · April 13, 1970]]
    Fifty-six hours into the flight, oxygen tank number two exploded.

    [[number: 205,000 | miles from Earth | anchor=miles]]
    The crew was more than 200,000 miles from home...

Directive lines (``[[name: ...]]``) attach to the paragraph that follows them;
a directive written inside a paragraph attaches to that paragraph.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from ytgen.models import Beat, Chapter, GraphicItem, GraphicSpec, Script
from ytgen.util.text import parse_figure

DIRECTIVE = re.compile(r"\[\[\s*([a-z_]+)\s*:\s*(.*?)\s*\]\]", re.S)


def load_script(path: str | Path) -> Script:
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        return Script.model_validate(json.loads(raw))
    if p.suffix.lower() in (".yaml", ".yml"):
        return Script.model_validate(yaml.safe_load(raw))
    return parse_markdown(raw)


def _items(spec: str, kv_sep: str = "=") -> list[GraphicItem]:
    out = []
    for part in re.split(r"[;\n]|,(?=\s*[^,\d])", spec):
        part = part.strip()
        if not part:
            continue
        if kv_sep in part:
            label, val = (x.strip() for x in part.split(kv_sep, 1))
            fig = parse_figure(val)
            out.append(GraphicItem(label=label, value=fig[0] if fig else None, display=val))
        else:
            out.append(GraphicItem(label=part))
    return out


def _graphic(name: str, body: str) -> GraphicSpec | None:
    parts = [x.strip() for x in body.split("|")]
    opts = {}
    for x in list(parts):
        m = re.fullmatch(r"(anchor|source|unit)\s*=\s*(.+)", x)
        if m:
            opts[m.group(1)] = m.group(2).strip()
            parts.remove(x)
    g: GraphicSpec | None = None
    if name in ("number", "big_number"):
        fig = parse_figure(parts[0])
        g = GraphicSpec(type="big_number", display=parts[0], label=parts[1] if len(parts) > 1 else "",
                        value=fig[0] if fig else None)
    elif name in ("chart", "bar_chart", "line", "line_chart"):
        title, data = (parts[0], parts[1]) if len(parts) > 1 else ("", parts[0])
        g = GraphicSpec(type="line_chart" if name.startswith("line") else "bar_chart", title=title,
                        items=_items(data), unit=parts[2] if len(parts) > 2 else "")
    elif name == "timeline":
        title, data = (parts[0], parts[1]) if len(parts) > 1 else ("", parts[0])
        items = []
        for ev in re.split(r"[;\n]", data):
            if ":" in ev:
                d, txt = ev.split(":", 1)
                items.append(GraphicItem(label=d.strip(), display=txt.strip()))
        g = GraphicSpec(type="timeline", title=title, items=items)
    elif name == "list":
        title, data = (parts[0], parts[1]) if len(parts) > 1 else ("", parts[0])
        g = GraphicSpec(type="list", title=title, items=[GraphicItem(label=x.strip()) for x in re.split(r"[;\n]", data) if x.strip()])
    elif name == "quote":
        g = GraphicSpec(type="quote", title=parts[0].strip('"“” '), label=parts[1] if len(parts) > 1 else "")
    elif name in ("compare", "vs"):
        title, data = (parts[0], parts[1]) if len(parts) > 1 else ("", parts[0])
        g = GraphicSpec(type="compare", title=title, items=_items(data, ":"))
    elif name == "kinetic":
        g = GraphicSpec(type="kinetic", items=[GraphicItem(label=x.strip()) for x in re.split(r"[;,\n]", parts[0]) if x.strip()])
    if g is not None:
        for k, v in opts.items():
            setattr(g, k, v)
    return g


def _apply(beat: Beat, name: str, body: str) -> None:
    if name == "visual":
        beat.visual = body
    elif name in ("place", "lower_third"):
        beat.lower_third = body
    elif name == "emphasis":
        beat.emphasis = [x.strip() for x in body.split(",") if x.strip()]
    else:
        g = _graphic(name, body)
        if g is None:
            raise ValueError(f"unknown directive [[{name}: ...]]")
        beat.graphic = g


def parse_markdown(raw: str) -> Script:
    meta: dict = {}
    m = re.match(r"^---\n(.*?)\n---\n", raw, re.S)
    if m:
        meta = yaml.safe_load(m.group(1)) or {}
        raw = raw[m.end():]
    chapters: list[Chapter] = []
    title = meta.get("title", "")
    for block in re.split(r"\n(?=#{1,2} )", "\n" + raw):
        block = block.strip()
        if not block:
            continue
        head, _, body = block.partition("\n")
        if head.startswith("# ") and not head.startswith("## "):
            title = title or head[2:].strip()
            if not body.strip():
                continue
            head = "## " + (title or "Intro")
        if not head.startswith("## "):
            body, head = block, "## " + (title or "Intro")
        hm = re.match(r"##\s+(.*?)\s*(?:\{mood=(\w+)\})?\s*$", head)
        ch = Chapter(title=hm.group(1), mood=hm.group(2) or "neutral", beats=[])
        pending: list[tuple[str, str]] = []
        for para in re.split(r"\n\s*\n", body):
            para = para.strip()
            if not para:
                continue
            lines = para.splitlines()
            # standalone directive lines at the top of the paragraph
            while lines and DIRECTIVE.fullmatch(lines[0].strip()):
                dm = DIRECTIVE.fullmatch(lines[0].strip())
                pending.append((dm.group(1), dm.group(2)))
                lines.pop(0)
            text = " ".join(l.strip() for l in lines).strip()
            if not text:
                continue
            inline = DIRECTIVE.findall(text)
            text = re.sub(r"\s+", " ", DIRECTIVE.sub("", text)).strip()
            beat = Beat(text=text)
            for name, b in pending + inline:
                _apply(beat, name, b)
            pending = []
            ch.beats.append(beat)
        if pending and ch.beats:
            for name, b in pending:
                _apply(ch.beats[-1], name, b)
        if ch.beats:
            chapters.append(ch)
    if not chapters:
        raise ValueError("script has no narration paragraphs")
    data = {k: v for k, v in meta.items() if k in Script.model_fields}
    data["title"] = title or chapters[0].title
    data["chapters"] = chapters
    return Script.model_validate(data)
