import { useEffect, useState } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import type { CanvasItem } from "../../lib/artifactMedia";
import { CodeBlock } from "../shared/CodeBlock";

interface LightboxPreviewProps {
  item: CanvasItem;
}

export function LightboxPreview({ item }: LightboxPreviewProps) {
  const name = item.caption || item.path.split("/").pop() || "artifact";
  const [codeText, setCodeText] = useState("");

  useEffect(() => {
    if (item.kind !== "code" && item.kind !== "text" && item.kind !== "markdown") {
      setCodeText("");
      return;
    }
    let cancelled = false;
    void fetch(item.url)
      .then((r) => r.text())
      .then((body) => { if (!cancelled) setCodeText(body); })
      .catch(() => { if (!cancelled) setCodeText("Could not load file."); });
    return () => { cancelled = true; };
  }, [item.url, item.kind]);

  if (item.kind === "image" && item.url) {
    return (
      <div className="ka-lightbox-preview">
        <img src={item.url} alt={name} />
      </div>
    );
  }

  if (item.kind === "video" && item.url) {
    return (
      <div className="ka-lightbox-preview">
        <video src={item.url} controls className="max-h-full max-w-full">
          <track kind="captions" />
        </video>
      </div>
    );
  }

  if (item.kind === "pdf" && item.url) {
    return (
      <div className="ka-lightbox-preview">
        <iframe
          src={item.url}
          title={name}
          className="h-full w-full border-0"
        />
      </div>
    );
  }

  if (item.kind === "webpage" && item.url) {
    return (
      <div className="ka-lightbox-preview">
        <iframe
          src={item.url}
          title={name}
          sandbox="allow-scripts allow-same-origin"
          className="h-full w-full border-0"
        />
      </div>
    );
  }

  if (item.kind === "code" && codeText) {
    return (
      <div className="ka-lightbox-preview overflow-auto p-4">
        <CodeBlock code={codeText} filename={name} maxHeight="calc(100vh - 8rem)" />
      </div>
    );
  }

  if (item.kind === "markdown" && codeText) {
    return (
      <div className="ka-lightbox-preview overflow-y-auto p-6" style={{ alignItems: "flex-start" }}>
        <div
          className="markdown mx-auto w-full max-w-3xl"
          dangerouslySetInnerHTML={{
            __html: DOMPurify.sanitize(
              marked.parse(codeText, { async: false }) as string
            ),
          }}
        />
      </div>
    );
  }

  if (item.kind === "text" && codeText) {
    return (
      <div className="ka-lightbox-preview overflow-y-auto p-4" style={{ alignItems: "flex-start" }}>
        <pre className="w-full rounded-lg border border-line bg-canvas p-4 font-mono text-[0.8rem] text-ink whitespace-pre-wrap">
          {codeText}
        </pre>
      </div>
    );
  }

  // Fallback: file icon
  return (
    <div className="ka-lightbox-preview">
      <div className="mx-6 flex max-w-md flex-col items-center gap-3 rounded-2xl border border-line bg-surface px-8 py-10 text-center shadow-xl">
        <span className="flex h-16 w-16 items-center justify-center rounded-2xl bg-accent/15 text-sm font-bold uppercase text-accent">
          {item.path.split(".").pop()?.slice(0, 5) || "FILE"}
        </span>
        <span className="break-all text-sm font-medium text-ink">{name}</span>
        <span className="text-xs leading-relaxed text-faint">
          This format cannot be rendered safely by the browser. You can open it in its native application or download it from the actions panel.
        </span>
        <a
          href={item.url}
          target="_blank"
          rel="noreferrer"
          className="mt-1 rounded-lg bg-accent/15 px-3 py-2 text-xs font-medium text-accent hover:bg-accent/25"
        >
          Open file
        </a>
      </div>
    </div>
  );
}
