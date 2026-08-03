import { useCallback } from "react";
import type { CanvasItem } from "../../lib/artifactMedia";
import { artifactDownloadUrl, fileExt, formatBytes, kindLabel } from "../../lib/artifactMedia";
import { useAppStore } from "../../store";

interface LightboxSidebarProps {
  item: CanvasItem;
}

export function LightboxSidebar({ item }: LightboxSidebarProps) {
  const showToast = useAppStore((s) => s.showToast);
  const setDraft = useAppStore((s) => s.setDraft);
  const sessionId = useAppStore((s) => s.sessionId);
  const name = item.caption || item.path.split("/").pop() || "artifact";
  const ext = fileExt(item.path).replace(".", "").toUpperCase();

  const handleCopy = useCallback(async () => {
    try {
      if (item.kind === "code" || item.kind === "text" || item.kind === "markdown") {
        // Copy file content for text-based files
        const res = await fetch(item.url);
        const text = await res.text();
        await navigator.clipboard.writeText(text);
        showToast("Code copied to clipboard");
      } else if (item.kind === "image") {
        // Copy image to clipboard
        const res = await fetch(item.url);
        const blob = await res.blob();
        await navigator.clipboard.write([
          new ClipboardItem({ [blob.type]: blob }),
        ]);
        showToast("Image copied to clipboard");
      } else {
        // Fallback: copy URL
        await navigator.clipboard.writeText(window.location.origin + item.url);
        showToast("Link copied to clipboard");
      }
    } catch {
      showToast("Copy failed — try downloading instead");
    }
  }, [item, showToast]);

  const handleDownload = useCallback(() => {
    if (!item.url) return;
    const url = (sessionId && artifactDownloadUrl(sessionId, item.path)) || item.url;
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }, [item, name, sessionId]);

  const handleRemix = useCallback(() => {
    const prompt =
      item.kind === "code"
        ? `Modify the code in artifacts/${name}: `
        : item.kind === "image"
          ? `Create a variation of the image artifacts/${name}: `
          : `Update artifacts/${name}: `;
    setDraft(prompt);
    showToast("Describe what to change, then send");
  }, [item, name, setDraft, showToast]);

  return (
    <div className="ka-lightbox-sidebar">
      {/* Header */}
      <div>
        <p className="text-sm font-semibold text-ink">{name}</p>
        <div className="mt-1 flex items-center gap-2">
          {ext && (
            <span className="rounded bg-accent/15 px-1.5 py-0.5 text-[0.6rem] font-bold text-accent">
              {ext}
            </span>
          )}
          <span className="text-xs text-faint">
            {kindLabel(item.kind)}
            {item.size ? ` · ${formatBytes(item.size)}` : ""}
          </span>
        </div>
      </div>

      {/* Actions */}
      <div>
        <p className="mb-2 text-[0.6rem] font-semibold uppercase tracking-wider text-faint">
          Actions
        </p>
        <div className="space-y-1">
          <SidebarAction
            icon="✦"
            label="Remix"
            sublabel="Pre-fill a follow-up prompt"
            onClick={handleRemix}
          />
          <SidebarAction
            icon="⊡"
            label="Copy to clipboard"
            sublabel={item.kind === "code" || item.kind === "text" ? "Copies file content" : item.kind === "image" ? "Copies image" : "Copies link"}
            onClick={() => void handleCopy()}
          />
          <SidebarAction
            icon="↓"
            label="Download"
            sublabel="Save to your machine"
            onClick={handleDownload}
          />
        </div>
      </div>

      {/* File info */}
      <div>
        <p className="mb-2 text-[0.6rem] font-semibold uppercase tracking-wider text-faint">
          Details
        </p>
        <div className="space-y-1.5 text-xs">
          <DetailRow label="Path" value={item.path} />
          <DetailRow label="Type" value={kindLabel(item.kind)} />
          {item.size != null && item.size > 0 && (
            <DetailRow label="Size" value={formatBytes(item.size)} />
          )}
        </div>
      </div>
    </div>
  );
}

function SidebarAction({
  icon,
  label,
  sublabel,
  onClick,
}: {
  icon: string;
  label: string;
  sublabel?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-line/40"
      onClick={onClick}
    >
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-line/40 text-xs text-ink">
        {icon}
      </span>
      <div className="min-w-0">
        <p className="text-[0.8rem] font-medium text-ink">{label}</p>
        {sublabel && (
          <p className="text-[0.6rem] text-faint">{sublabel}</p>
        )}
      </div>
    </button>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start gap-2">
      <span className="shrink-0 w-10 text-[0.65rem] text-faint">{label}</span>
      <span className="min-w-0 break-all text-[0.7rem] text-muted">{value}</span>
    </div>
  );
}
