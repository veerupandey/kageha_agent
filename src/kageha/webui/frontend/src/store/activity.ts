import type { ActivityStep } from "../api/types";

/** Normalize SSE `detail` (array or string) into short display lines. */
export function asDetailLines(
  data: Record<string, unknown> | undefined,
  limit = 12,
): string[] {
  if (!data) return [];
  const raw = data.detail;
  if (Array.isArray(raw)) {
    return raw
      .map((item) => String(item ?? "").trim())
      .filter(Boolean)
      .slice(0, limit);
  }
  if (typeof raw === "string" && raw.trim()) {
    return [raw.trim()].slice(0, limit);
  }
  return [];
}

/** Append or refresh an Activity step; keep last N. */
export function appendActivityStep(
  steps: ActivityStep[],
  step: ActivityStep,
  max = 32,
): ActivityStep[] {
  const label = String(step.label || "").trim();
  if (!label) return steps;
  const nextStep: ActivityStep = {
    label,
    detail: step.detail?.length ? step.detail : undefined,
    kind: step.kind || undefined,
    interesting: step.interesting,
  };
  const last = steps[steps.length - 1];
  if (last && last.label === label) {
    const merged = [...steps];
    merged[merged.length - 1] = {
      ...last,
      ...nextStep,
      detail: nextStep.detail?.length ? nextStep.detail : last.detail,
    };
    return merged.slice(-max);
  }
  return [...steps, nextStep].slice(-max);
}
