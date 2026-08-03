import { useEffect, useState } from "react";
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

  if (item.kind === "code" && codeText) {
    return (
      <div className="ka-lightbox-preview overflow-auto p-4">
        <CodeBlock code={codeText} filename={name} maxHeight="calc(100vh - 8rem)" />
      </div>
    );
  }

  if ((item.kind === "text" || item.kind === "markdown") && codeText) {
    return (
      <div className="ka-lightbox-preview overflow-auto p-4">
        <pre className="rounded-lg border border-line bg-canvas p-4 font-mono text-[0.8rem] text-ink whitespace-pre-wrap">
          {codeText}
        </pre>
      </div>
    );
  }

  // Fallback: file icon
  return (
    <div className="ka-lightbox-preview">
      <div className="flex flex-col items-center gap-3 text-faint">
        <span className="text-5xl">📄</span>
        <span className="text-sm">{name}</span>
      </div>
    </div>
  );
}
