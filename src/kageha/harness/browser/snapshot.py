"""Accessibility + interactive snapshots with stable eN refs.

Primary path: Chromium Accessibility.getFullAXTree via CDP (Cursor-style).
Fallback: DOM query of interactive elements (legacy, still useful for plain HTML).
"""

from __future__ import annotations

import re
from typing import Any

# Roles an agent can typically act on.
_INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "link",
        "textbox",
        "searchbox",
        "combobox",
        "checkbox",
        "radio",
        "switch",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "option",
        "tab",
        "treeitem",
        "slider",
        "spinbutton",
        "listbox",
    }
)

_AX_JS_FALLBACK = """(limit) => {
  const out = [];
  const sel = [
    'a[href]', 'button', 'input', 'textarea', 'select',
    '[role="button"]', '[role="link"]', '[role="textbox"]',
    '[role="checkbox"]', '[role="radio"]', '[role="combobox"]',
    '[role="menuitem"]', '[role="tab"]', '[role="switch"]',
    '[contenteditable="true"]'
  ].join(',');
  const nodes = Array.from(document.querySelectorAll(sel));
  let i = 0;
  for (const el of nodes) {
    if (i >= limit) break;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) continue;
    const role = el.getAttribute('role') || el.tagName.toLowerCase();
    const name = (
      el.getAttribute('aria-label') || el.innerText || el.value ||
      el.getAttribute('placeholder') || el.getAttribute('name') || ''
    ).trim().slice(0, 80);
    const href = el.getAttribute('href') || '';
    const type = el.getAttribute('type') || '';
    let path = el.tagName.toLowerCase();
    if (el.id) path += '#' + CSS.escape(el.id);
    else if (el.classList && el.classList.length)
      path += '.' + CSS.escape(el.classList[0]);
    out.push({
      role, name, href, type, path,
      tag: el.tagName.toLowerCase(),
      backendDOMNodeId: null,
    });
    i++;
  }
  return out;
}"""


def _ax_value(field: Any) -> str:
    if isinstance(field, dict):
        return str(field.get("value") or "")
    if field is None:
        return ""
    return str(field)


def _ax_name(node: dict[str, Any]) -> str:
    name = _ax_value(node.get("name")).strip()
    if name:
        return name[:80]
    for p in node.get("properties") or []:
        if p.get("name") == "name":
            return _ax_value(p.get("value")).strip()[:80]
    return ""


def _ax_role(node: dict[str, Any]) -> str:
    role = _ax_value(node.get("role")).strip().lower()
    return role or "generic"


def _flatten_ax_tree(nodes: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Pick interactive (and a few structural) nodes from a full AX tree."""
    by_id = {n.get("nodeId"): n for n in nodes if n.get("nodeId") is not None}
    out: list[dict[str, Any]] = []
    seen: set[Any] = set()

    def walk(node_id: Any, depth: int) -> None:
        if len(out) >= limit or node_id in seen:
            return
        seen.add(node_id)
        node = by_id.get(node_id)
        if not node or node.get("ignored"):
            # Still walk children of ignored wrappers.
            for cid in node.get("childIds") or [] if node else []:
                walk(cid, depth)
            return
        role = _ax_role(node).lower()
        name = _ax_name(node).strip()
        interactive = role in _INTERACTIVE_ROLES
        structural = role in {"heading", "banner", "main", "navigation", "article"} and bool(name)
        if interactive or (structural and depth <= 3):
            out.append(
                {
                    "role": role,
                    "name": name,
                    "href": "",
                    "type": "",
                    "path": "",
                    "tag": role,
                    "backendDOMNodeId": node.get("backendDOMNodeId"),
                    "ax_node_id": node.get("nodeId"),
                    "depth": depth,
                }
            )
        for cid in node.get("childIds") or []:
            walk(cid, depth + 1)

    roots = [n for n in nodes if n.get("parentId") in (None, "", 0)]
    if not roots and nodes:
        roots = [nodes[0]]
    for root in roots:
        walk(root.get("nodeId"), 0)
    return out[:limit]


async def build_ax_items(page: Any, limit: int = 60) -> list[dict[str, Any]]:
    """Best-effort AX items with backendDOMNodeId when CDP allows."""
    limit = max(1, min(120, int(limit)))
    try:
        client = await page.context.new_cdp_session(page)
        try:
            await client.send("Accessibility.enable")
            result = await client.send("Accessibility.getFullAXTree")
            nodes = result.get("nodes") or []
            items = _flatten_ax_tree(nodes, limit)
            if items:
                return items
        finally:
            try:
                await client.detach()
            except Exception:
                pass
    except Exception:
        pass
    try:
        items = await page.evaluate(_AX_JS_FALLBACK, limit)
        return list(items or [])
    except Exception:
        return []


def format_snapshot(
    items: list[dict[str, Any]],
    *,
    url: str = "",
    title: str = "",
    compact: bool = True,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Return (text, refs_map, ref_meta). refs_map: eN -> meta dict."""
    refs: dict[str, Any] = {}
    lines: list[str] = []
    if url or title:
        header = []
        if title:
            header.append(f"title: {title}")
        if url:
            header.append(f"url: {url}")
        lines.append(" | ".join(header))
        lines.append("")
    for idx, it in enumerate(items):
        ref = f"e{idx}"
        refs[ref] = it
        role = it.get("role") or "generic"
        name = (it.get("name") or "").replace("\n", " ").strip()
        href = it.get("href") or ""
        name_s = f" {name!r}" if name else ""
        if compact:
            extra = f" {href}" if href and len(href) < 60 else ""
            lines.append(f"- [{ref}] {role}{name_s}{extra}")
        else:
            indent = "  " * int(it.get("depth") or 0)
            lines.append(
                f"{indent}- [{ref}] role={role} name={name!r} "
                f"dom={it.get('backendDOMNodeId')} path={it.get('path')!r}"
            )
    body = "\n".join(lines) if lines else "(empty snapshot)"
    return body, refs, items


async def resolve_locator(page: Any, ref_or_selector: str, refs: dict[str, Any]) -> Any:
    """Resolve eN ref / CSS / text= / role= to a Playwright locator."""
    key = (ref_or_selector or "").strip()
    meta = refs.get(key) if key in refs else None
    if isinstance(meta, dict):
        # Prefer role+name (stable across minor DOM churn).
        role = (meta.get("role") or meta.get("tag") or "").lower()
        name = meta.get("name") or ""
        role_map = {
            "a": "link",
            "button": "button",
            "input": "textbox",
            "textarea": "textbox",
            "select": "combobox",
            "searchbox": "searchbox",
            "textbox": "textbox",
            "link": "link",
            "checkbox": "checkbox",
            "radio": "radio",
            "combobox": "combobox",
            "menuitem": "menuitem",
            "tab": "tab",
            "switch": "switch",
        }
        aria = role_map.get(role, role)
        if name and aria in _INTERACTIVE_ROLES | {"link", "button", "textbox", "combobox"}:
            try:
                loc = page.get_by_role(aria, name=re.compile(re.escape(name[:40]), re.I))
                if await loc.count() > 0:
                    return loc.first
            except Exception:
                pass
        if name:
            loc = page.get_by_text(name[:40], exact=False)
            if await loc.count() > 0:
                return loc.first
        css = meta.get("path") or ""
        if css:
            return page.locator(css).first
        # Last resort: backend DOM node via CDP
        backend_id = meta.get("backendDOMNodeId")
        if backend_id:
            try:
                client = await page.context.new_cdp_session(page)
                try:
                    resolved = await client.send(
                        "DOM.resolveNode", {"backendNodeId": int(backend_id)}
                    )
                    object_id = (resolved.get("object") or {}).get("objectId")
                    if object_id:
                        # Mark element and query via JS handle — fall through to role.
                        pass
                finally:
                    try:
                        await client.detach()
                    except Exception:
                        pass
            except Exception:
                pass
        raise RuntimeError(f"Could not resolve ref {key} (role={role} name={name!r})")
    if key.startswith("text="):
        return page.get_by_text(key[5:], exact=False).first
    if key.startswith("role="):
        body = key[5:]
        if ":" in body:
            role, name = body.split(":", 1)
            return page.get_by_role(role, name=name).first
        return page.get_by_role(body).first
    return page.locator(key).first
