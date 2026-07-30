import type { CanvasItem } from "../../lib/artifactMedia";
import { useAppStore } from "../../store";

interface LightboxSidebarProps {
  item: CanvasItem;
}

export function LightboxSidebar({ item }: LightboxSidebarProps) {
  const showToast = useAppStore((s) => s.showToast);
  const name = item.caption || item.path.split("/").pop() || "artifact";

  const handleCopy = async () => {
    if (item.url) {
      try {
        await navigator.clipboard.writeText(item.url);
        showToast("Copied to clipboard");
      } catch {
        showToast("Copy failed");
      }
    }
  };

  const handleDownload = () => {
    if (item.url) {
      const a = document.createElement("a");
      a.href = item.url;
      a.download = name;
      a.click();
    }
  };

  return (
    <div className="ka-lightbox-sidebar">
      {/* Header */}
      <div>
        <p className="text-sm font-medium text-ink">{name}</p>
        {item.size != null && (
          <p className="mt-0.5 text-xs text-faint">
            {item.size > 0 ? `${(item.size / 1024).toFixed(1)} KB` : ""}
          </p>
        )}
      </div>

      {/* Actions */}
      <div>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-faint">
          Actions
        </p>
        <div className="space-y-1.5">
          <button
            type="button"
            className="ka-sidebar-item w-full"
            onClick={() => showToast("Remix coming soon")}
          >
            <span aria-hidden="true">✨</span> Remix
          </button>
          <button
            type="button"
            className="ka-sidebar-item w-full"
            onClick={() => void handleCopy()}
          >
            <span aria-hidden="true">📋</span> Copy to clipboard
          </button>
          <button
            type="button"
            className="ka-sidebar-item w-full"
            onClick={handleDownload}
          >
            <span aria-hidden="true">⬇</span> Download
          </button>
        </div>
      </div>

      {/* Used in threads (placeholder) */}
      <div>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-faint">
          Used in threads
        </p>
        <p className="text-xs text-muted">Current thread</p>
      </div>
    </div>
  );
}
