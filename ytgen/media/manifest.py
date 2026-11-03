"""Source manifest: every asset used in the video with provenance, license
status, attribution text and where it appears. Written as JSON (machine) and
CREDITS.md (human), next to the video."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from ytgen.media import license as lic
from ytgen.models import Plan


def _fmt(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def write(plan: Plan, out_dir: Path) -> Path:
    uses: dict[str, list] = defaultdict(list)
    for sc in plan.scenes:
        if sc.asset_id:
            uses[sc.asset_id].append([round(sc.start, 2), round(sc.end, 2), sc.kind])
    rows = []
    for aid, spans in uses.items():
        a = plan.assets.get(aid)
        if a is None:
            continue
        if not a.license_status:
            lic.apply(a)
        who = a.author if a.author and a.author != "Unknown" else a.provider
        rows.append({
            "id": a.id, "kind": a.kind, "provider": a.provider, "title": a.title, "author": a.author,
            "license": a.license, "license_status": a.license_status, "license_url": a.license_url,
            "attribution_required": a.attribution_required, "commercial_ok": a.commercial_ok,
            "source_url": a.page_url, "download_url": a.url, "local_path": a.path, "sha1": a.sha1,
            "ai_generated": a.generated, "cost_usd": a.cost_usd, "semantic_match": a.semantic,
            "attribution": f"{a.title} by {who}, {a.license}" + (f" ({a.page_url})" if a.page_url else ""),
            "used_at": spans,
        })
    rows.sort(key=lambda r: r["used_at"][0][0])
    flagged = [r for r in rows if r["license_status"] in ("uncertain", "rejected") or r["ai_generated"]]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "assets_manifest.json").write_text(json.dumps({"assets": rows, "flagged": [r["id"] for r in flagged],
                                                              "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 4)},
                                                             indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Credits and licenses", "",
             "Every visual in this video, its source and license. Items marked ⚑ need a human check.", ""]
    for r in rows:
        flag = " ⚑" if r in flagged else ""
        at = ", ".join(_fmt(s[0]) for s in r["used_at"][:4])
        lines.append(f"- **{r['title'][:90]}**{flag} — {r['author'] or r['provider']} · {r['license']} "
                     f"({r['license_status']}) · {r['provider']} · at {at}" + (f"  \n  {r['source_url']}" if r["source_url"] else ""))
    (out_dir / "CREDITS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_dir / "assets_manifest.json"
