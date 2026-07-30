import type { CanvasItem } from "../../lib/artifactMedia";

interface LightboxPreviewProps {
  item: CanvasItem;
}

export function LightboxPreview({ item }: LightboxPreviewProps) {
  const name = item.caption || item.path.split("/").pop() || "artifact";

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
