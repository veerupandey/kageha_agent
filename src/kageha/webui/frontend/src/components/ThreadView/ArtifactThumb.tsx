import { useEffect, useRef, useState } from "react";
import type { CanvasItem } from "../../lib/artifactMedia";

interface ArtifactThumbProps {
  item: CanvasItem;
  onClick: () => void;
}

export function ArtifactThumb({ item, onClick }: ArtifactThumbProps) {
  const ref = useRef<HTMLButtonElement>(null);
  const [visible, setVisible] = useState(false);

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

  const isImage = item.kind === "image";
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
      ) : (
        <div className="flex h-full w-full items-center justify-center bg-[var(--color-surface-hover)] text-2xl text-faint">
          {item.kind === "pdf" ? "📄" : item.kind === "video" ? "🎬" : "📎"}
        </div>
      )}
      <span className="ka-artifact-label">{name}</span>
    </button>
  );
}
