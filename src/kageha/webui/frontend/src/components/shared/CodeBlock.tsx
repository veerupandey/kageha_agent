/**
 * CodeBlock — Syntax-highlighted code preview using highlight.js.
 * Supports auto-detection or explicit language via file extension.
 */
import { useEffect, useRef } from "react";
import hljs from "highlight.js/lib/core";

// Register common languages (keeps bundle size manageable)
import python from "highlight.js/lib/languages/python";
import javascript from "highlight.js/lib/languages/javascript";
import typescript from "highlight.js/lib/languages/typescript";
import bash from "highlight.js/lib/languages/bash";
import json from "highlight.js/lib/languages/json";
import yaml from "highlight.js/lib/languages/yaml";
import sql from "highlight.js/lib/languages/sql";
import css from "highlight.js/lib/languages/css";
import xml from "highlight.js/lib/languages/xml";
import go from "highlight.js/lib/languages/go";
import rust from "highlight.js/lib/languages/rust";
import java from "highlight.js/lib/languages/java";
import cpp from "highlight.js/lib/languages/cpp";
import ruby from "highlight.js/lib/languages/ruby";
import swift from "highlight.js/lib/languages/swift";
import kotlin from "highlight.js/lib/languages/kotlin";
import php from "highlight.js/lib/languages/php";
import shell from "highlight.js/lib/languages/shell";
import markdown from "highlight.js/lib/languages/markdown";
import graphql from "highlight.js/lib/languages/graphql";
import lua from "highlight.js/lib/languages/lua";

hljs.registerLanguage("python", python);
hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("typescript", typescript);
hljs.registerLanguage("bash", bash);
hljs.registerLanguage("json", json);
hljs.registerLanguage("yaml", yaml);
hljs.registerLanguage("sql", sql);
hljs.registerLanguage("css", css);
hljs.registerLanguage("xml", xml);
hljs.registerLanguage("go", go);
hljs.registerLanguage("rust", rust);
hljs.registerLanguage("java", java);
hljs.registerLanguage("cpp", cpp);
hljs.registerLanguage("ruby", ruby);
hljs.registerLanguage("swift", swift);
hljs.registerLanguage("kotlin", kotlin);
hljs.registerLanguage("php", php);
hljs.registerLanguage("shell", shell);
hljs.registerLanguage("markdown", markdown);
hljs.registerLanguage("graphql", graphql);
hljs.registerLanguage("lua", lua);

const EXT_TO_LANG: Record<string, string> = {
  ".py": "python",
  ".js": "javascript",
  ".mjs": "javascript",
  ".cjs": "javascript",
  ".ts": "typescript",
  ".tsx": "typescript",
  ".jsx": "javascript",
  ".sh": "bash",
  ".bash": "bash",
  ".zsh": "bash",
  ".fish": "bash",
  ".json": "json",
  ".yml": "yaml",
  ".yaml": "yaml",
  ".sql": "sql",
  ".css": "css",
  ".html": "xml",
  ".htm": "xml",
  ".xml": "xml",
  ".svg": "xml",
  ".go": "go",
  ".rs": "rust",
  ".java": "java",
  ".c": "cpp",
  ".cpp": "cpp",
  ".h": "cpp",
  ".hpp": "cpp",
  ".cs": "cpp",
  ".rb": "ruby",
  ".swift": "swift",
  ".kt": "kotlin",
  ".kts": "kotlin",
  ".php": "php",
  ".md": "markdown",
  ".graphql": "graphql",
  ".gql": "graphql",
  ".lua": "lua",
};

function detectLanguage(filename: string): string | undefined {
  const ext = filename.includes(".")
    ? "." + filename.split(".").pop()!.toLowerCase()
    : "";
  return EXT_TO_LANG[ext];
}

interface CodeBlockProps {
  code: string;
  filename?: string;
  language?: string;
  maxHeight?: string;
  className?: string;
}

export function CodeBlock({
  code,
  filename,
  language,
  maxHeight = "20rem",
  className = "",
}: CodeBlockProps) {
  const codeRef = useRef<HTMLElement>(null);

  const lang = language || (filename ? detectLanguage(filename) : undefined);

  useEffect(() => {
    if (codeRef.current && code) {
      try {
        if (lang && hljs.getLanguage(lang)) {
          const result = hljs.highlight(code, { language: lang });
          codeRef.current.innerHTML = result.value;
        } else {
          const result = hljs.highlightAuto(code);
          codeRef.current.innerHTML = result.value;
        }
      } catch {
        // Fallback: just text
        codeRef.current.textContent = code;
      }
    }
  }, [code, lang]);

  return (
    <pre
      className={`overflow-auto rounded-lg border border-line bg-[#0d1117] p-3 font-mono text-[0.72rem] leading-relaxed ${className}`}
      style={{ maxHeight }}
    >
      <code ref={codeRef} className="hljs text-[#c9d1d9]">
        {code}
      </code>
    </pre>
  );
}

/**
 * Generate a tiny code thumbnail (first N lines, no highlighting for perf).
 */
export function CodeThumbnail({
  code,
  filename,
  lines = 6,
}: {
  code: string;
  filename?: string;
  lines?: number;
}) {
  const ext = filename?.includes(".")
    ? filename.split(".").pop()!.toUpperCase()
    : "CODE";
  const preview = code.split("\n").slice(0, lines).join("\n");

  return (
    <div className="flex h-full w-full flex-col overflow-hidden rounded bg-[#0d1117]">
      {/* Mini header */}
      <div className="flex items-center gap-1 px-1.5 py-0.5 bg-[#161b22] border-b border-[#30363d]">
        <span className="h-1.5 w-1.5 rounded-full bg-[#f85149]" />
        <span className="h-1.5 w-1.5 rounded-full bg-[#d29922]" />
        <span className="h-1.5 w-1.5 rounded-full bg-[#3fb950]" />
        <span className="ml-auto text-[0.4rem] text-[#8b949e]">{ext}</span>
      </div>
      {/* Code preview */}
      <pre className="flex-1 overflow-hidden px-1.5 py-1 font-mono text-[0.35rem] leading-tight text-[#8b949e] whitespace-pre">
        {preview}
      </pre>
    </div>
  );
}
