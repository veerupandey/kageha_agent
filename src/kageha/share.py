"""Generate a shareable standalone HTML replay of a session.

Usage:
    kageha share <session_id> [--output path]

Produces a self-contained HTML file with:
- Conversation (user request + agent response)
- Progress timeline (steps, tools, milestones)
- Embedded artifact previews (images base64-inlined)
- Cost/steps metadata
- Goal card with evidence
"""

from __future__ import annotations

import base64
import json
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any


def _load_session_data(session_dir: Path) -> dict[str, Any]:
    """Load all session data needed for the replay."""
    data: dict[str, Any] = {}

    # Turn data
    turns_dir = session_dir / "_turns"
    if turns_dir.is_dir():
        for f in sorted(turns_dir.iterdir()):
            if f.suffix == ".json":
                try:
                    data["turn"] = json.loads(f.read_text(encoding="utf-8"))
                    break
                except (json.JSONDecodeError, OSError):
                    pass

    # Goal card
    gc = session_dir / "goal_card.json"
    if gc.is_file():
        try:
            data["goals"] = json.loads(gc.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # Events (for timeline)
    events_file = session_dir / "events.jsonl"
    events: list[dict] = []
    if events_file.is_file():
        for line in events_file.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    data["events"] = events

    # Result
    result_md = session_dir / "result.md"
    if result_md.is_file():
        data["result_md"] = result_md.read_text(encoding="utf-8")

    # Plan
    plan_file = session_dir / "plan.json"
    if plan_file.is_file():
        try:
            data["plan"] = json.loads(plan_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    return data


def _embed_image(path: Path, max_size_mb: float = 2.0) -> str | None:
    """Base64-encode an image for embedding in HTML."""
    if not path.is_file():
        return None
    if path.stat().st_size > max_size_mb * 1024 * 1024:
        return None
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _collect_artifacts(session_dir: Path) -> list[dict[str, Any]]:
    """Collect artifact metadata and embed images."""
    artifacts_dir = session_dir / "artifacts"
    if not artifacts_dir.is_dir():
        return []
    items: list[dict[str, Any]] = []
    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    for f in sorted(artifacts_dir.rglob("*")):
        if not f.is_file():
            continue
        rel = str(f.relative_to(session_dir))
        item: dict[str, Any] = {
            "path": rel,
            "name": f.name,
            "size": f.stat().st_size,
            "ext": f.suffix.lower(),
        }
        if f.suffix.lower() in image_exts:
            embedded = _embed_image(f)
            if embedded:
                item["data_url"] = embedded
                item["type"] = "image"
        elif f.suffix.lower() in {".html", ".htm"}:
            try:
                item["html_content"] = f.read_text(encoding="utf-8")[:50000]
                item["type"] = "html"
            except (OSError, UnicodeDecodeError):
                item["type"] = "file"
        else:
            item["type"] = "file"
        items.append(item)
    return items


def _format_event_timeline(events: list[dict]) -> list[dict[str, str]]:
    """Extract key timeline events for display."""
    timeline: list[dict[str, str]] = []
    for ev in events:
        kind = ev.get("kind", "")
        data = ev.get("data", {})
        ts = ev.get("ts", 0)

        if kind == "run_start":
            timeline.append({
                "time": _format_ts(ts),
                "icon": "▶",
                "label": f"Started — {data.get('effort', '')} effort, {data.get('loop_mode', '')} mode",
            })
        elif kind == "plan":
            timeline.append({
                "time": _format_ts(ts),
                "icon": "📋",
                "label": f"Plan: {data.get('steps', '?')} steps ({data.get('source', '')})",
            })
        elif kind == "model":
            model = data.get("model", "?")
            tokens = data.get("prompt_tokens", 0) + data.get("completion_tokens", 0)
            timeline.append({
                "time": _format_ts(ts),
                "icon": "🧠",
                "label": f"{model} — {tokens} tokens",
            })
        elif kind == "model_failover":
            timeline.append({
                "time": _format_ts(ts),
                "icon": "🔄",
                "label": f"Failover: {data.get('from', '?')} → {data.get('to', '?')}",
            })
        elif kind == "checkpoint":
            timeline.append({
                "time": _format_ts(ts),
                "icon": "💾",
                "label": "Checkpoint saved",
            })
    return timeline


def _format_ts(ts: float) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%H:%M:%S")
    except (OSError, ValueError):
        return ""


def generate_share_html(session_id: str, session_dir: Path) -> str:
    """Generate a complete standalone HTML replay page."""
    data = _load_session_data(session_dir)
    turn = data.get("turn", {})
    goals = data.get("goals", {})
    events = data.get("events", [])
    plan = data.get("plan", {})
    artifacts = _collect_artifacts(session_dir)
    timeline = _format_event_timeline(events)

    request = turn.get("request", "")
    answer = turn.get("answer", "")
    status = turn.get("status", "unknown")
    steps = turn.get("steps", 0)
    cost = turn.get("spent_usd", 0)
    validated = turn.get("validated", False)
    failures = turn.get("recovered_failures", [])

    goal_items = goals.get("items", [])
    plan_steps = plan.get("steps", [])

    # Build image gallery HTML
    gallery_html = ""
    image_artifacts = [a for a in artifacts if a.get("type") == "image" and a.get("data_url")]
    if image_artifacts:
        cards = []
        for img in image_artifacts:
            cards.append(
                f'<div class="gallery-card">'
                f'<img src="{img["data_url"]}" alt="{img["name"]}" />'
                f'<span class="gallery-label">{img["name"]}</span>'
                f'</div>'
            )
        gallery_html = '<div class="gallery">' + "".join(cards) + "</div>"

    # Build goals HTML
    goals_html = ""
    if goal_items:
        items_html = []
        for g in goal_items:
            check = "✓" if g.get("passes") else "○"
            cls = "goal-pass" if g.get("passes") else "goal-pending"
            items_html.append(
                f'<li class="{cls}"><span class="goal-check">{check}</span> '
                f'{_esc(g.get("description", ""))}'
                f'{"<br/><small>" + _esc(g.get("evidence", "")[:200]) + "</small>" if g.get("evidence") else ""}'
                f'</li>'
            )
        goals_html = '<ul class="goals-list">' + "".join(items_html) + "</ul>"

    # Build timeline HTML
    timeline_html = ""
    if timeline:
        rows = []
        for t in timeline[:20]:
            rows.append(
                f'<div class="timeline-row">'
                f'<span class="timeline-time">{t["time"]}</span>'
                f'<span class="timeline-icon">{t["icon"]}</span>'
                f'<span class="timeline-label">{_esc(t["label"])}</span>'
                f'</div>'
            )
        timeline_html = '<div class="timeline">' + "".join(rows) + "</div>"

    # Plan HTML
    plan_html = ""
    if plan_steps:
        steps_li = "".join(
            f'<li>{_esc(s.get("description", ""))}</li>' for s in plan_steps
        )
        plan_html = f'<details class="plan-details"><summary>Plan ({len(plan_steps)} steps)</summary><ol>{steps_li}</ol></details>'

    # Failures HTML
    failures_html = ""
    if failures:
        items = "".join(f"<li>{_esc(f)}</li>" for f in failures[:5])
        failures_html = f'<details class="failures"><summary>Recovered failures ({len(failures)})</summary><ul>{items}</ul></details>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kageha Session — {_esc(session_id[:8])}</title>
<style>
:root {{
  --bg: #f7f6f3; --surface: #fff; --ink: #1c1b19; --muted: #6b6860;
  --faint: #9a968c; --line: #e6e3da; --accent: #1f4b3a; --accent-soft: #e8f0ec;
  --font: "IBM Plex Sans", -apple-system, sans-serif;
  --mono: "IBM Plex Mono", monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #121110; --surface: #1c1b19; --ink: #f3f1ea; --muted: #a8a49a;
    --faint: #7a766c; --line: #2e2c28; --accent: #6dbe9c; --accent-soft: #1e2e28;
  }}
}}
* {{ box-sizing: border-box; margin: 0; }}
body {{ font-family: var(--font); background: var(--bg); color: var(--ink); line-height: 1.6; padding: 2rem 1rem; }}
.container {{ max-width: 48rem; margin: 0 auto; }}
.header {{ text-align: center; margin-bottom: 2rem; padding-bottom: 1.5rem; border-bottom: 1px solid var(--line); }}
.header h1 {{ font-size: 1.1rem; font-weight: 600; color: var(--accent); letter-spacing: 0.02em; }}
.header .meta {{ margin-top: 0.5rem; font-size: 0.8rem; color: var(--muted); display: flex; justify-content: center; gap: 1rem; flex-wrap: wrap; }}
.meta-badge {{ background: var(--accent-soft); color: var(--accent); padding: 0.2rem 0.6rem; border-radius: 1rem; font-weight: 600; font-size: 0.72rem; }}
.section {{ margin: 1.5rem 0; }}
.section-title {{ font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: var(--faint); margin-bottom: 0.5rem; }}
.card {{ background: var(--surface); border: 1px solid var(--line); border-radius: 0.75rem; padding: 1.25rem; margin-bottom: 1rem; }}
.request {{ font-size: 0.92rem; color: var(--ink); white-space: pre-wrap; }}
.answer {{ font-size: 0.92rem; color: var(--ink); white-space: pre-wrap; }}
.answer code {{ font-family: var(--mono); font-size: 0.84em; background: var(--line); padding: 0.1em 0.3em; border-radius: 0.2rem; }}
.goals-list {{ list-style: none; padding: 0; }}
.goals-list li {{ padding: 0.4rem 0; display: flex; align-items: flex-start; gap: 0.5rem; font-size: 0.85rem; }}
.goals-list li small {{ display: block; color: var(--faint); font-size: 0.75rem; margin-top: 0.2rem; }}
.goal-check {{ font-size: 0.9rem; }}
.goal-pass .goal-check {{ color: var(--accent); }}
.goal-pending .goal-check {{ color: var(--faint); }}
.gallery {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 0.75rem; margin-top: 0.5rem; }}
.gallery-card {{ border: 1px solid var(--line); border-radius: 0.5rem; overflow: hidden; background: var(--surface); }}
.gallery-card img {{ width: 100%; height: auto; display: block; }}
.gallery-label {{ display: block; padding: 0.3rem 0.5rem; font-size: 0.65rem; color: var(--muted); text-align: center; border-top: 1px solid var(--line); }}
.timeline {{ font-family: var(--mono); font-size: 0.75rem; }}
.timeline-row {{ display: flex; align-items: center; gap: 0.5rem; padding: 0.25rem 0; color: var(--muted); }}
.timeline-time {{ width: 5rem; color: var(--faint); }}
.timeline-icon {{ width: 1.5rem; text-align: center; }}
.timeline-label {{ flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
details {{ margin: 0.5rem 0; }}
summary {{ cursor: pointer; font-size: 0.8rem; color: var(--muted); font-weight: 500; padding: 0.3rem 0; }}
details ol, details ul {{ padding-left: 1.5rem; margin-top: 0.5rem; font-size: 0.82rem; color: var(--ink); }}
details li {{ margin: 0.3rem 0; }}
.footer {{ margin-top: 2rem; padding-top: 1rem; border-top: 1px solid var(--line); text-align: center; font-size: 0.72rem; color: var(--faint); }}
.status-success {{ color: var(--accent); }}
.status-error {{ color: #9b2c2c; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>Kageha Session Replay</h1>
    <div class="meta">
      <span>Session: {_esc(session_id[:12])}</span>
      <span class="meta-badge">{steps} steps</span>
      <span class="meta-badge">${cost:.4f}</span>
      <span class="meta-badge status-{'success' if status == 'success' else 'error'}">{status}</span>
      {'<span class="meta-badge">✓ verified</span>' if validated else ''}
    </div>
  </div>

  <div class="section">
    <div class="section-title">Request</div>
    <div class="card">
      <div class="request">{_esc(request)}</div>
    </div>
  </div>

  {f'<div class="section"><div class="section-title">Plan</div><div class="card">{plan_html}</div></div>' if plan_html else ''}

  {f'<div class="section"><div class="section-title">Goals</div><div class="card">{goals_html}</div></div>' if goals_html else ''}

  {f'<div class="section"><div class="section-title">Artifacts</div><div class="card">{gallery_html}</div></div>' if gallery_html else ''}

  <div class="section">
    <div class="section-title">Response</div>
    <div class="card">
      <div class="answer">{_esc(answer)}</div>
    </div>
  </div>

  {f'<div class="section"><div class="section-title">Timeline</div><div class="card">{timeline_html}</div></div>' if timeline_html else ''}

  {failures_html}

  <div class="footer">
    Generated by Kageha · {datetime.now().strftime("%Y-%m-%d %H:%M")}
  </div>
</div>
</body>
</html>"""


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def export_session(session_id: str, output: str | None = None) -> Path:
    """Export a session as a shareable HTML file."""
    from kageha.config import sessions_dir

    session_dir = sessions_dir() / session_id
    if not session_dir.is_dir():
        raise FileNotFoundError(f"Session not found: {session_id}")

    html = generate_share_html(session_id, session_dir)

    if output:
        out_path = Path(output)
    else:
        out_path = session_dir / "share.html"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
