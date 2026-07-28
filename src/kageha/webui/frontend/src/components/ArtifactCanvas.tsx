import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useMemo, useState } from "react";
import type { CanvasItem, CanvasKind } from "../lib/artifactMedia";
import {
  formatBytes,
  isPreviewableKind,
  kindLabel,
} from "../lib/artifactMedia";
import { cn } from "../lib/cn";
import { useAppStore } from "../store";

function KindBadge({ kind }: { kind: CanvasKind }) {
  return (
    <span className="rounded bg-line/80 px-1.5 py-0.5 text-[0.65rem] font-medium uppercase tracking-wide text-muted">
      {kindLabel(kind)}
    </span>
  );
}

function ImagePreview({
  item,
  large,
}: {
  item: CanvasItem;
  large?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line bg-canvas px-4 py-10 text-center">
        <span className="text-sm font-medium text-ink">{item.caption}</span>
        <span className="text-xs text-muted">Preview unavailable</span>
        <a
          href={item.url}
          target="_blank"
          rel="noreferrer"
          className="text-xs font-medium text-accent hover:underline"
        >
          Open file
        </a>
      </div>
    );
  }
  return (
    <img
      src={item.url}
      alt={item.caption}
      className={cn(
        "mx-auto max-w-full rounded-lg object-contain",
        large ? "max-h-[min(80vh,900px)]" : "max-h-64",
      )}
      onError={() => setFailed(true)}
    />
  );
}

function PreviewBody({
  item,
  text,
  textLoading,
  large,
}: {
  item: CanvasItem;
  text?: string;
  textLoading?: boolean;
  large?: boolean;
}) {
  if (item.kind === "image") {
    return <ImagePreview item={item} large={large} />;
  }
  if (item.kind === "video") {
    return (
      <video
        src={item.url}
        controls
        className={cn(
          "mx-auto w-full bg-ink",
          large ? "max-h-[min(80vh,900px)]" : "max-h-64",
        )}
      />
    );
  }
  if (item.kind === "audio") {
    return (
      <div className="rounded-lg border border-line bg-canvas px-4 py-6">
        <p className="mb-3 text-sm font-medium text-ink">{item.caption}</p>
        <audio src={item.url} controls className="w-full" />
      </div>
    );
  }
  if (item.kind === "pdf") {
    return (
      <iframe
        title={item.caption}
        src={item.url}
        className={cn(
          "w-full rounded border border-line bg-surface",
          large ? "h-[min(80vh,900px)]" : "h-72",
        )}
      />
    );
  }
  if (item.kind === "markdown" || item.kind === "text") {
    if (textLoading) {
      return <p className="p-4 text-sm text-muted">Loading…</p>;
    }
    return (
      <pre
        className={cn(
          "overflow-auto whitespace-pre-wrap break-words rounded border border-line bg-canvas p-3 font-mono text-xs text-ink",
          large ? "max-h-[min(80vh,900px)]" : "max-h-64",
        )}
      >
        {text || "(empty)"}
      </pre>
    );
  }

  // Office / archive / unknown — card with open + download
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-line bg-canvas px-4 py-8 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-xl bg-accent-soft text-lg font-semibold text-accent">
        {item.kind === "presentation"
          ? "PPT"
          : item.kind === "document"
            ? "DOC"
            : item.kind === "spreadsheet"
              ? "XLS"
              : "FILE"}
      </div>
      <div>
        <p className="font-medium text-ink">{item.caption}</p>
        <p className="mt-1 text-xs text-muted">
          {kindLabel(item.kind)}
          {item.size != null ? ` · ${formatBytes(item.size)}` : ""}
        </p>
      </div>
      <p className="max-w-xs text-xs text-faint">
        Preview isn’t available in-browser for this format. Open or download
        the file instead.
      </p>
      <div className="flex gap-2">
        <a
          href={item.url}
          target="_blank"
          rel="noreferrer"
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white"
        >
          Open
        </a>
        <a
          href={item.url}
          download={item.caption}
          className="rounded-md border border-line bg-surface px-3 py-1.5 text-sm font-medium text-ink"
        >
          Download
        </a>
      </div>
    </div>
  );
}

function usePreviewText(item: CanvasItem | null) {
  const [text, setText] = useState<string>("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!item || (item.kind !== "text" && item.kind !== "markdown")) {
      setText("");
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    void fetch(item.url)
      .then((r) => r.text())
      .then((body) => {
        if (!cancelled) {
          setText(body.slice(0, 200_000));
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setText("Could not load file.");
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [item?.url, item?.kind]);

  return { text, loading };
}

/** Docked canvas + expand dialog for session artifacts. */
export function ArtifactCanvas() {
  const sessionId = useAppStore((s) => s.sessionId);
  const canvasOpen = useAppStore((s) => s.canvasOpen);
  const canvasExpanded = useAppStore((s) => s.canvasExpanded);
  const canvasItems = useAppStore((s) => s.canvasItems);
  const canvasSelectedPath = useAppStore((s) => s.canvasSelectedPath);
  const setCanvasOpen = useAppStore((s) => s.setCanvasOpen);
  const setCanvasExpanded = useAppStore((s) => s.setCanvasExpanded);
  const selectCanvasItem = useAppStore((s) => s.selectCanvasItem);
  const refreshArtifacts = useAppStore((s) => s.refreshArtifacts);

  const selected = useMemo(
    () =>
      canvasItems.find((i) => i.path === canvasSelectedPath) ||
      canvasItems[0] ||
      null,
    [canvasItems, canvasSelectedPath],
  );

  const { text, loading } = usePreviewText(selected);

  useEffect(() => {
    if (canvasOpen && sessionId) {
      void refreshArtifacts();
    }
  }, [canvasOpen, sessionId, refreshArtifacts]);

  if (!canvasOpen) return null;

  return (
    <>
      <aside
        className="flex w-full min-w-0 flex-col border-l border-line bg-surface md:w-[22rem] lg:w-[26rem]"
        id="artifact-canvas"
        aria-label="Artifact canvas"
      >
        <header className="flex h-12 shrink-0 items-center gap-2 border-b border-line px-3">
          <p className="min-w-0 flex-1 truncate text-sm font-semibold text-ink">
            Canvas
          </p>
          <button
            type="button"
            className="rounded-md px-2 py-1 text-xs text-muted hover:bg-line/70 hover:text-ink"
            onClick={() => void refreshArtifacts()}
            title="Refresh artifacts"
          >
            Refresh
          </button>
          {selected && isPreviewableKind(selected.kind) ? (
            <button
              type="button"
              className="rounded-md px-2 py-1 text-xs font-medium text-accent hover:bg-accent-soft"
              onClick={() => setCanvasExpanded(true)}
            >
              Expand
            </button>
          ) : null}
          <button
            type="button"
            className="rounded-md px-2 py-1 text-xs text-muted hover:bg-line/70"
            aria-label="Close canvas"
            onClick={() => setCanvasOpen(false)}
          >
            ✕
          </button>
        </header>

        {!canvasItems.length ? (
          <div className="flex flex-1 items-center justify-center px-4 text-center text-sm text-muted">
            No artifacts yet. Files created in this session will show up here.
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="shrink-0 overflow-x-auto border-b border-line px-2 py-2">
              <div className="flex gap-2">
                {canvasItems.map((item) => {
                  const active = item.path === selected?.path;
                  return (
                    <button
                      key={item.path}
                      type="button"
                      title={item.path}
                      onClick={() => selectCanvasItem(item.path)}
                      className={cn(
                        "w-[4.5rem] shrink-0 overflow-hidden rounded-lg border text-left transition",
                        active
                          ? "border-accent ring-2 ring-accent/25"
                          : "border-line hover:border-accent/40",
                      )}
                    >
                      {item.kind === "image" ? (
                        <img
                          src={item.url}
                          alt=""
                          className="h-12 w-full object-cover"
                          onError={(e) => {
                            (e.currentTarget as HTMLImageElement).style.display =
                              "none";
                          }}
                        />
                      ) : item.kind === "audio" ? (
                        <div className="flex h-12 items-center justify-center bg-canvas text-sm text-accent">
                          ♪
                        </div>
                      ) : (
                        <div className="flex h-12 items-center justify-center bg-canvas text-[0.6rem] font-semibold uppercase tracking-wide text-accent">
                          {kindLabel(item.kind).slice(0, 4)}
                        </div>
                      )}
                      <span className="block truncate px-1.5 py-1 text-[0.65rem] text-muted">
                        {item.caption}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>

            {selected ? (
              <div className="min-h-0 flex-1 overflow-y-auto p-3">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <KindBadge kind={selected.kind} />
                  <span className="truncate text-xs text-muted">
                    {selected.path}
                  </span>
                </div>
                <PreviewBody
                  item={selected}
                  text={text}
                  textLoading={loading}
                />
                {isPreviewableKind(selected.kind) ? (
                  <div className="mt-3 flex gap-2">
                    <button
                      type="button"
                      className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white"
                      onClick={() => setCanvasExpanded(true)}
                    >
                      Expand
                    </button>
                    <a
                      href={selected.url}
                      target="_blank"
                      rel="noreferrer"
                      className="rounded-md border border-line px-3 py-1.5 text-sm text-ink hover:bg-line/50"
                    >
                      Open tab
                    </a>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        )}
      </aside>

      <Dialog.Root
        open={canvasExpanded && Boolean(selected)}
        onOpenChange={(open) => setCanvasExpanded(open)}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[70] bg-ink/50" />
          <Dialog.Content className="fixed inset-3 z-[71] flex flex-col overflow-hidden rounded-xl border border-line bg-surface shadow-2xl md:inset-8">
            <header className="flex shrink-0 items-center gap-3 border-b border-line px-4 py-3">
              <Dialog.Title className="min-w-0 flex-1 truncate text-sm font-semibold">
                {selected?.caption || "Preview"}
              </Dialog.Title>
              {selected ? <KindBadge kind={selected.kind} /> : null}
              {selected ? (
                <a
                  href={selected.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs font-medium text-accent hover:underline"
                >
                  Open tab
                </a>
              ) : null}
              <Dialog.Close className="rounded-md px-2 py-1 text-sm text-muted hover:bg-line/70">
                Close
              </Dialog.Close>
            </header>
            <div className="min-h-0 flex-1 overflow-auto p-4">
              {selected ? (
                <PreviewBody
                  item={selected}
                  text={text}
                  textLoading={loading}
                  large
                />
              ) : null}
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}
