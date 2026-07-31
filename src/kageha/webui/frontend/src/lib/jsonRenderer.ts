/**
 * Post-process rendered markdown HTML to enhance JSON code blocks
 * into polished, readable data cards with export options.
 */

/**
 * Detect if a code block contains valid JSON and is large enough
 * to benefit from enhanced rendering (>100 chars).
 */
function isEnhanceableJson(text: string): boolean {
  const trimmed = text.trim();
  if (trimmed.length < 100) return false;
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return false;
  try {
    JSON.parse(trimmed);
    return true;
  } catch {
    return false;
  }
}

/**
 * Render a JSON value as a formatted HTML string with indentation,
 * type coloring, and collapsible nested objects.
 */
function renderJsonValue(value: unknown, indent = 0): string {
  const pad = "  ".repeat(indent);
  const padInner = "  ".repeat(indent + 1);

  if (value === null) return `<span class="json-null">null</span>`;
  if (typeof value === "boolean")
    return `<span class="json-bool">${value}</span>`;
  if (typeof value === "number")
    return `<span class="json-num">${value}</span>`;
  if (typeof value === "string") {
    // Truncate very long strings
    const display = value.length > 200 ? value.slice(0, 197) + "…" : value;
    return `<span class="json-str">"${escapeHtml(display)}"</span>`;
  }

  if (Array.isArray(value)) {
    if (value.length === 0) return `<span class="json-bracket">[]</span>`;
    if (value.length <= 3 && value.every((v) => typeof v !== "object" || v === null)) {
      // Short arrays inline
      const items = value.map((v) => renderJsonValue(v, 0)).join(", ");
      return `<span class="json-bracket">[</span>${items}<span class="json-bracket">]</span>`;
    }
    const items = value
      .map((v) => `${padInner}${renderJsonValue(v, indent + 1)}`)
      .join(",\n");
    return `<span class="json-bracket">[</span>\n${items}\n${pad}<span class="json-bracket">]</span>`;
  }

  if (typeof value === "object" && value !== null) {
    const entries = Object.entries(value);
    if (entries.length === 0)
      return `<span class="json-bracket">{}</span>`;
    const items = entries
      .map(
        ([k, v]) =>
          `${padInner}<span class="json-key">"${escapeHtml(k)}"</span>: ${renderJsonValue(v, indent + 1)}`,
      )
      .join(",\n");
    return `<span class="json-bracket">{</span>\n${items}\n${pad}<span class="json-bracket">}</span>`;
  }

  return escapeHtml(String(value));
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Build a polished data card HTML wrapper around JSON content.
 * Includes: formatted view, copy button, download button, raw toggle.
 */
function buildDataCard(jsonText: string, index: number): string {
  const id = `json-card-${index}-${Date.now()}`;
  let parsed: unknown;
  try {
    parsed = JSON.parse(jsonText);
  } catch {
    return ""; // Can't parse — skip enhancement
  }

  // Determine a title from the top-level keys
  const title = getDataTitle(parsed);
  const formatted = renderJsonValue(parsed, 1);
  const escapedRaw = escapeHtml(JSON.stringify(parsed, null, 2));

  return `<div class="json-data-card" data-json-id="${id}">
  <div class="json-card-header">
    <span class="json-card-title">${escapeHtml(title)}</span>
    <div class="json-card-actions">
      <button type="button" class="json-card-btn" data-action="copy" data-json-id="${id}" title="Copy JSON">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="5" y="5" width="9" height="9" rx="1.5"/><path d="M5 11H3.5A1.5 1.5 0 012 9.5v-7A1.5 1.5 0 013.5 1h7A1.5 1.5 0 0112 2.5V5"/></svg>
        Copy
      </button>
      <button type="button" class="json-card-btn" data-action="download" data-json-id="${id}" title="Download as JSON">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 2v9m0 0l-3-3m3 3l3-3M3 13h10"/></svg>
        Export
      </button>
      <button type="button" class="json-card-btn json-toggle-raw" data-action="toggle" data-json-id="${id}" title="Toggle raw/formatted">
        Raw
      </button>
    </div>
  </div>
  <div class="json-card-body">
    <pre class="json-formatted">${formatted}</pre>
    <pre class="json-raw" style="display:none">${escapedRaw}</pre>
  </div>
  <textarea class="json-card-clipboard" style="position:absolute;left:-9999px" aria-hidden="true">${escapeHtml(JSON.stringify(parsed, null, 2))}</textarea>
</div>`;
}

function getDataTitle(parsed: unknown): string {
  if (typeof parsed !== "object" || parsed === null) return "Data";
  const obj = parsed as Record<string, unknown>;
  const keys = Object.keys(obj);
  if (keys.length === 1) {
    // Single top-level key — use it as title
    const key = keys[0];
    return key
      .replace(/[_-]/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }
  // Look for common title-like keys
  for (const k of ["title", "name", "type", "report_type", "analysis"]) {
    if (typeof obj[k] === "string") return String(obj[k]).slice(0, 60);
  }
  return `Data (${keys.length} fields)`;
}

/**
 * Enhance markdown HTML by replacing large JSON code blocks with
 * polished data cards. Returns the enhanced HTML string.
 */
export function enhanceJsonBlocks(html: string): string {
  // Match <pre><code class="language-json">...</code></pre> blocks
  let cardIndex = 0;
  return html.replace(
    /<pre><code(?:\s+class="[^"]*language-json[^"]*")?>([\s\S]*?)<\/code><\/pre>/gi,
    (_match, content: string) => {
      // Decode HTML entities back to raw text for parsing
      const raw = content
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/&amp;/g, "&")
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'");

      if (!isEnhanceableJson(raw)) {
        return _match; // Keep original rendering for small/invalid JSON
      }

      const card = buildDataCard(raw, cardIndex++);
      return card || _match;
    },
  );
}

/**
 * Initialize click handlers for JSON data card buttons.
 * Call once after the DOM is updated with enhanced HTML.
 */
export function initJsonCardHandlers(container: HTMLElement): void {
  container.querySelectorAll<HTMLButtonElement>(".json-card-btn").forEach((btn) => {
    // Remove any existing listener to avoid duplicates
    const clone = btn.cloneNode(true) as HTMLButtonElement;
    btn.parentNode?.replaceChild(clone, btn);

    clone.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const action = clone.dataset.action;
      const cardId = clone.dataset.jsonId;
      const card = container.querySelector(`[data-json-id="${cardId}"]`);
      if (!card) return;

      if (action === "copy") {
        const textarea = card.querySelector<HTMLTextAreaElement>(".json-card-clipboard");
        if (textarea) {
          void navigator.clipboard.writeText(textarea.value).then(() => {
            clone.textContent = "Copied!";
            setTimeout(() => { clone.innerHTML = `<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="5" y="5" width="9" height="9" rx="1.5"/><path d="M5 11H3.5A1.5 1.5 0 012 9.5v-7A1.5 1.5 0 013.5 1h7A1.5 1.5 0 0112 2.5V5"/></svg> Copy`; }, 1500);
          });
        }
      } else if (action === "download") {
        const textarea = card.querySelector<HTMLTextAreaElement>(".json-card-clipboard");
        if (textarea) {
          const blob = new Blob([textarea.value], { type: "application/json" });
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = "export.json";
          a.click();
          URL.revokeObjectURL(url);
        }
      } else if (action === "toggle") {
        const formatted = card.querySelector<HTMLElement>(".json-formatted");
        const raw = card.querySelector<HTMLElement>(".json-raw");
        if (formatted && raw) {
          const showingFormatted = formatted.style.display !== "none";
          formatted.style.display = showingFormatted ? "none" : "";
          raw.style.display = showingFormatted ? "" : "none";
          clone.textContent = showingFormatted ? "Formatted" : "Raw";
        }
      }
    });
  });
}
