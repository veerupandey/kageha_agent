/**
 * Enhance rendered markdown HTML with:
 * - Copy buttons on all code blocks
 * - Syntax highlighting for common languages (lightweight, no external lib)
 * - Collapsible long code blocks (>20 lines)
 * - CSV blocks rendered as tables
 * - Mermaid blocks rendered with a "render" placeholder
 * - YAML syntax coloring
 */

const LANG_LABELS: Record<string, string> = {
  js: "JavaScript",
  javascript: "JavaScript",
  ts: "TypeScript",
  typescript: "TypeScript",
  tsx: "TypeScript",
  jsx: "JavaScript",
  py: "Python",
  python: "Python",
  bash: "Bash",
  sh: "Shell",
  shell: "Shell",
  zsh: "Shell",
  json: "JSON",
  yaml: "YAML",
  yml: "YAML",
  html: "HTML",
  css: "CSS",
  sql: "SQL",
  rust: "Rust",
  rs: "Rust",
  go: "Go",
  java: "Java",
  rb: "Ruby",
  ruby: "Ruby",
  cpp: "C++",
  c: "C",
  md: "Markdown",
  markdown: "Markdown",
  toml: "TOML",
  dockerfile: "Dockerfile",
  csv: "CSV",
  mermaid: "Mermaid",
  text: "Text",
  plaintext: "Text",
};

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function unescapeHtml(s: string): string {
  return s
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
}

/** Lightweight syntax highlighting for Python */
function highlightPython(code: string): string {
  return code
    .replace(/\b(def|class|import|from|return|if|elif|else|for|while|with|as|try|except|finally|raise|yield|lambda|and|or|not|in|is|True|False|None|async|await|pass|break|continue)\b/g, '<span class="syn-kw">$1</span>')
    .replace(/(#[^\n]*)/g, '<span class="syn-comment">$1</span>')
    .replace(/(&quot;{3}[\s\S]*?&quot;{3}|&quot;[^&]*?&quot;|&#39;[^&]*?&#39;)/g, '<span class="syn-str">$1</span>')
    .replace(/\b(\d+\.?\d*)\b/g, '<span class="syn-num">$1</span>');
}

/** Lightweight syntax highlighting for JS/TS */
function highlightJs(code: string): string {
  return code
    .replace(/\b(const|let|var|function|return|if|else|for|while|class|import|export|from|default|async|await|new|this|typeof|instanceof|throw|try|catch|finally|switch|case|break|continue|yield|true|false|null|undefined|void)\b/g, '<span class="syn-kw">$1</span>')
    .replace(/(\/\/[^\n]*)/g, '<span class="syn-comment">$1</span>')
    .replace(/(&quot;[^&]*?&quot;|&#39;[^&]*?&#39;|`[^`]*?`)/g, '<span class="syn-str">$1</span>')
    .replace(/\b(\d+\.?\d*)\b/g, '<span class="syn-num">$1</span>');
}

/** Lightweight syntax highlighting for Bash/Shell */
function highlightBash(code: string): string {
  return code
    .replace(/(#[^\n]*)/g, '<span class="syn-comment">$1</span>')
    .replace(/\b(if|then|else|elif|fi|for|do|done|while|case|esac|function|return|exit|export|source|alias|cd|ls|cat|grep|echo|rm|mkdir|cp|mv|chmod|sudo|apt|pip|npm|git|docker|curl|wget)\b/g, '<span class="syn-kw">$1</span>')
    .replace(/(&quot;[^&]*?&quot;|&#39;[^&]*?&#39;)/g, '<span class="syn-str">$1</span>')
    .replace(/(\$\w+|\$\{[^}]+\})/g, '<span class="syn-var">$1</span>');
}

/** Lightweight syntax highlighting for YAML */
function highlightYaml(code: string): string {
  return code
    .replace(/(#[^\n]*)/g, '<span class="syn-comment">$1</span>')
    .replace(/^(\s*[\w.-]+)(:)/gm, '<span class="syn-kw">$1</span><span class="syn-punct">$2</span>')
    .replace(/:\s*(&quot;[^&]*?&quot;|&#39;[^&]*?&#39;)/g, ': <span class="syn-str">$1</span>')
    .replace(/\b(true|false|null|yes|no)\b/gi, '<span class="syn-bool">$1</span>')
    .replace(/\b(\d+\.?\d*)\b/g, '<span class="syn-num">$1</span>');
}

/** Lightweight syntax highlighting for SQL */
function highlightSql(code: string): string {
  return code
    .replace(/\b(SELECT|FROM|WHERE|AND|OR|INSERT|INTO|VALUES|UPDATE|SET|DELETE|CREATE|TABLE|ALTER|DROP|INDEX|JOIN|LEFT|RIGHT|INNER|OUTER|ON|AS|ORDER|BY|GROUP|HAVING|LIMIT|OFFSET|UNION|DISTINCT|COUNT|SUM|AVG|MAX|MIN|NOT|NULL|IN|LIKE|BETWEEN|EXISTS|PRIMARY|KEY|FOREIGN|REFERENCES|CASCADE|DEFAULT|CONSTRAINT|UNIQUE|CHECK|VIEW|TRIGGER|GRANT|REVOKE)\b/gi, '<span class="syn-kw">$&</span>')
    .replace(/(--[^\n]*)/g, '<span class="syn-comment">$1</span>')
    .replace(/(&#39;[^&]*?&#39;)/g, '<span class="syn-str">$1</span>')
    .replace(/\b(\d+\.?\d*)\b/g, '<span class="syn-num">$1</span>');
}

/** Lightweight syntax highlighting for CSS */
function highlightCss(code: string): string {
  return code
    .replace(/(\/\*[\s\S]*?\*\/)/g, '<span class="syn-comment">$1</span>')
    .replace(/([\w-]+)\s*:/g, '<span class="syn-kw">$1</span>:')
    .replace(/(#[0-9a-fA-F]{3,8})\b/g, '<span class="syn-num">$1</span>')
    .replace(/(\d+\.?\d*(?:px|em|rem|%|vh|vw|s|ms))/g, '<span class="syn-num">$1</span>');
}

function highlightCode(code: string, lang: string): string {
  const l = lang.toLowerCase();
  if (l === "python" || l === "py") return highlightPython(code);
  if (["js", "javascript", "ts", "typescript", "tsx", "jsx"].includes(l)) return highlightJs(code);
  if (["bash", "sh", "shell", "zsh"].includes(l)) return highlightBash(code);
  if (["yaml", "yml", "toml"].includes(l)) return highlightYaml(code);
  if (l === "sql") return highlightSql(code);
  if (l === "css") return highlightCss(code);
  return code; // No highlighting for unknown languages
}

/** Parse CSV text into an HTML table */
function csvToTable(csv: string): string {
  const lines = csv.trim().split("\n").filter(Boolean);
  if (lines.length < 2) return ""; // Need at least header + 1 row

  const parseRow = (line: string): string[] => {
    const cells: string[] = [];
    let current = "";
    let inQuotes = false;
    for (const ch of line) {
      if (ch === '"') {
        inQuotes = !inQuotes;
      } else if (ch === "," && !inQuotes) {
        cells.push(current.trim());
        current = "";
      } else {
        current += ch;
      }
    }
    cells.push(current.trim());
    return cells;
  };

  const headers = parseRow(lines[0]);
  const rows = lines.slice(1).map(parseRow);

  let html = '<div class="csv-table-wrapper"><table class="csv-table"><thead><tr>';
  for (const h of headers) {
    html += `<th>${escapeHtml(h)}</th>`;
  }
  html += "</tr></thead><tbody>";
  for (const row of rows.slice(0, 100)) { // Cap at 100 rows
    html += "<tr>";
    for (let i = 0; i < headers.length; i++) {
      html += `<td>${escapeHtml(row[i] || "")}</td>`;
    }
    html += "</tr>";
  }
  html += "</tbody></table>";
  if (rows.length > 100) {
    html += `<p class="csv-truncated">Showing 100 of ${rows.length} rows</p>`;
  }
  html += "</div>";
  return html;
}

/** Build a mermaid placeholder (actual rendering needs client-side mermaid.js) */
function mermaidPlaceholder(code: string): string {
  return `<div class="mermaid-block">
    <div class="mermaid-header">
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" class="text-accent"><path d="M8 1L14.5 5v6L8 15L1.5 11V5L8 1z" stroke="currentColor" stroke-width="1.5"/></svg>
      <span>Diagram</span>
    </div>
    <pre class="mermaid-source">${escapeHtml(code)}</pre>
    <p class="mermaid-note">Mermaid diagram — copy source and paste into <a href="https://mermaid.live" target="_blank" rel="noreferrer">mermaid.live</a> to render</p>
  </div>`;
}

/**
 * Enhance all code blocks in rendered HTML with:
 * - Language label badge
 * - Copy button
 * - Syntax highlighting
 * - Collapse for long blocks
 * - CSV → table conversion
 * - Mermaid → placeholder
 */
export function enhanceCodeBlocks(html: string): string {
  let blockIndex = 0;

  return html.replace(
    /<pre><code(?:\s+class="([^"]*)")?>([\s\S]*?)<\/code><\/pre>/gi,
    (_match, classAttr: string | undefined, content: string) => {
      // Skip if already enhanced by jsonRenderer
      if (_match.includes("json-data-card")) return _match;

      // Extract language from class="language-xxx"
      const langMatch = (classAttr || "").match(/language-(\w+)/);
      const lang = langMatch ? langMatch[1].toLowerCase() : "";
      const rawText = unescapeHtml(content);
      const id = `code-block-${blockIndex++}`;

      // CSV → render as table
      if (lang === "csv" && rawText.includes(",") && rawText.split("\n").length >= 2) {
        const table = csvToTable(rawText);
        if (table) return table;
      }

      // Mermaid → placeholder
      if (lang === "mermaid") {
        return mermaidPlaceholder(rawText);
      }

      // Apply syntax highlighting
      const highlighted = lang ? highlightCode(content, lang) : content;

      // Determine if collapsible (>20 lines)
      const lineCount = rawText.split("\n").length;
      const collapsible = lineCount > 20;

      // Language label
      const label = LANG_LABELS[lang] || (lang ? lang.toUpperCase() : "");

      // Build enhanced block
      let enhanced = `<div class="code-block-enhanced" data-code-id="${id}">`;
      enhanced += `<div class="code-block-header">`;
      if (label) {
        enhanced += `<span class="code-block-lang">${escapeHtml(label)}</span>`;
      }
      enhanced += `<button type="button" class="code-copy-btn" data-code-id="${id}" title="Copy code">`;
      enhanced += `<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="5" y="5" width="9" height="9" rx="1.5"/><path d="M5 11H3.5A1.5 1.5 0 012 9.5v-7A1.5 1.5 0 013.5 1h7A1.5 1.5 0 0112 2.5V5"/></svg>`;
      enhanced += ` Copy</button>`;
      enhanced += `</div>`;

      if (collapsible) {
        enhanced += `<details class="code-block-collapse"><summary class="code-block-expand">${lineCount} lines — click to expand</summary>`;
        enhanced += `<pre><code>${highlighted}</code></pre>`;
        enhanced += `</details>`;
      } else {
        enhanced += `<pre><code>${highlighted}</code></pre>`;
      }

      enhanced += `<textarea class="code-block-clipboard" style="position:absolute;left:-9999px" aria-hidden="true">${escapeHtml(rawText)}</textarea>`;
      enhanced += `</div>`;
      return enhanced;
    },
  );
}

/**
 * Initialize copy button handlers for enhanced code blocks.
 */
export function initCodeBlockHandlers(container: HTMLElement): void {
  container.querySelectorAll<HTMLButtonElement>(".code-copy-btn").forEach((btn) => {
    const clone = btn.cloneNode(true) as HTMLButtonElement;
    btn.parentNode?.replaceChild(clone, btn);

    clone.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const codeId = clone.dataset.codeId;
      const block = container.querySelector(`[data-code-id="${codeId}"]`);
      if (!block) return;
      const textarea = block.querySelector<HTMLTextAreaElement>(".code-block-clipboard");
      if (textarea) {
        void navigator.clipboard.writeText(textarea.value).then(() => {
          const orig = clone.innerHTML;
          clone.textContent = "Copied!";
          setTimeout(() => { clone.innerHTML = orig; }, 1500);
        });
      }
    });
  });
}
