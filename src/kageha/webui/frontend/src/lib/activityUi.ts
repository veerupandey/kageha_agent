import type { ActivityStep } from "../api/types";

/** Match CLI TransientProgress glyphs. */
export function activityGlyph(label: string): string {
  const low = (label || "").toLowerCase();
  if (low.includes("waiting") || low.includes("approval") || low.includes("awaiting")) {
    return "⏸";
  }
  if (
    low.includes("think") ||
    low.includes("reason") ||
    low.includes("planning") ||
    low.includes("model:")
  ) {
    return "✦";
  }
  if (
    low.includes("step") ||
    low.includes("tool") ||
    low.includes("running") ||
    low.includes("working") ||
    low.includes("action")
  ) {
    return "▸";
  }
  if (low.includes("error") || low.includes("fail") || low.includes("denied")) {
    return "!";
  }
  if (low.includes("done") || low.includes("complete") || low.includes("success")) {
    return "✓";
  }
  return "·";
}

export function activityGlyphClass(label: string): string {
  const g = activityGlyph(label);
  if (g === "⏸") return "text-warn";
  if (g === "✦") return "text-sky-600";
  if (g === "▸") return "text-teal-700";
  if (g === "!") return "text-danger";
  if (g === "✓") return "text-accent";
  return "text-cyan-700";
}

/** Shorten noisy labels toward CLI-friendly status. */
export function friendlyActivityLabel(label: string): string {
  const compact = (label || "").replace(/\s+/g, " ").trim();
  if (!compact) return "";
  const low = compact.toLowerCase();
  if (low.includes("ask_human")) return "Waiting for your answer…";
  if (low.includes("reasoning:")) return "Thinking…";
  if (low.includes("planning") || low.includes("plan ready")) return "Planning…";
  if (low.includes("preparing task")) return "Preparing…";
  if (low.startsWith("accepted")) return "Starting…";
  if (low.includes("checking") || low.includes("verify") || low.includes("progress=")) {
    return "Checking the result…";
  }
  const action = /action:\s*([a-z0-9_]+)/i.exec(compact);
  if (action) return `Running ${action[1]}…`;
  const tools = /tools:\s*([^(\n]+)/i.exec(compact);
  if (tools) {
    const first = tools[1].split(",")[0]?.trim();
    if (first && !first.includes("ask_human")) return `Running ${first}…`;
    return "Working…";
  }
  if (low.includes("model:") && compact.includes("→")) {
    return compact.length > 72 ? `${compact.slice(0, 69)}…` : compact;
  }
  if (low.includes("thinking") || low.includes("model=")) return "Thinking…";
  return compact.length > 96 ? `${compact.slice(0, 93)}…` : compact;
}

export function activityLineText(step: ActivityStep): string {
  const label = friendlyActivityLabel(step.label);
  const detail = step.detail?.[0]?.replace(/\s+/g, " ").trim();
  if (!detail) return label;
  // Keep one short trailing hint, CLI-style.
  const hint = detail.length > 64 ? `${detail.slice(0, 61)}…` : detail;
  if (label.toLowerCase().includes(hint.toLowerCase().slice(0, 24))) return label;
  return `${label}  ${hint}`;
}
