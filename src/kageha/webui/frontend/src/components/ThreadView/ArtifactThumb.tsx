import { useEffect, useRef, useState } from "react";
import type { CanvasItem } from "../../lib/artifactMedia";
import { CodeThumbnail } from "../shared/CodeBlock";

interface ArtifactThumbProps {
  item: CanvasItem;
  onClick: () => void;
}

export function ArtifactThumb({ item, onClick }: ArtifactThumbProps) {
  const ref = useRef<HTMLButtonElement>(null);
  const [visible, setVisible] = useState(false);
  const [codePreview, setCodePreview] = useState("");

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { threshold: 0.1 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Fetch a code snippet for thumbnail
  useEffect(() => {
    if (!visible || item.kind !== "code" || !item.url) return;
    let cancelled = false;
    void fetch(item.url)
      .then((r) => r.text())
      .then((body) => { if (!cancelled) setCodePreview(body.slice(0, 600)); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [visible, item.kind, item.url]);

  const isImage = item.kind === "image";
  const isCode = item.kind === "code";
  const name = item.caption || item.path.split("/").pop() || "artifact";

  return (
    <button
      ref={ref}
      type="button"
      className="ka-artifact-thumb"
      onClick={onClick}
      aria-label={`Open ${name}`}
    >
      {visible && isImage && item.url ? (
        <img src={item.url} alt={name} loading="lazy" />
      ) : visible && isCode && codePreview ? (
        <div className="h-full w-full overflow-hidden rounded">
          <CodeThumbnail code={codePreview} filename={name} lines={10} />
        </div>
      ) : (
        <div className="flex h-full w-full items-center justify-center bg-[var(--color-surface-hover)] text-2xl text-faint">
          {item.kind === "pdf" ? "📄" : item.kind === "video" ? "🎬" : item.kind === "code" ? "⟨⟩" : "📎"}
        </div>
      )}
      <span className="ka-artifact-label">{name}</span>
    </button>
  );
}
