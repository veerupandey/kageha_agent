import { useRef } from "react";
import { useAppStore } from "../store";
import { useFocusTrap } from "../lib/focusTrap";

export function MemoryDrawer() {
  const open = useAppStore((s) => s.drawers.memory);
  const artifactsOpen = useAppStore((s) => s.drawers.artifacts);
  const closeDrawer = useAppStore((s) => s.closeDrawer);
  const memoryQuery = useAppStore((s) => s.memoryQuery);
  const setMemoryQuery = useAppStore((s) => s.setMemoryQuery);
  const memoryKinds = useAppStore((s) => s.memoryKinds);
  const memoryStates = useAppStore((s) => s.memoryStates);
  const memorySelectedKinds = useAppStore((s) => s.memorySelectedKinds);
  const toggleMemoryKind = useAppStore((s) => s.toggleMemoryKind);
  const memoryStateFilter = useAppStore((s) => s.memoryStateFilter);
  const setMemoryStateFilter = useAppStore((s) => s.setMemoryStateFilter);
  const searchMemory = useAppStore((s) => s.searchMemory);
  const memoryResults = useAppStore((s) => s.memoryResults);
  const memoryTraceId = useAppStore((s) => s.memoryTraceId);
  const memorySearching = useAppStore((s) => s.memorySearching);
  const drawerRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useFocusTrap(open, drawerRef, { initialFocusRef: closeRef });

  const beside = open && artifactsOpen;

  return (
    <aside
      ref={drawerRef}
      className={`memory drawer${open ? " open" : ""}${beside ? " beside-artifacts" : ""}`}
      id="memory-drawer"
      aria-hidden={open ? "false" : "true"}
    >
      <header className="memory-head">
        <div>
          <p className="eyebrow">Memory</p>
          <h2 className="drawer-title">Search across sessions</h2>
        </div>
        <button
          ref={closeRef}
          type="button"
          className="btn ghost"
          id="btn-close-memory"
          aria-label="Close"
          onClick={() => closeDrawer("memory")}
        >
          Close
        </button>
      </header>

      <form
        className="memory-form"
        id="memory-form"
        onSubmit={(e) => {
          e.preventDefault();
          void searchMemory();
        }}
      >
        <input
          type="search"
          id="memory-query"
          placeholder="Recall preferences, facts, episodes…"
          autoComplete="off"
          value={memoryQuery}
          onChange={(e) => setMemoryQuery(e.target.value)}
        />
        <div
          className="kind-filters"
          id="kind-filters"
          role="group"
          aria-label="Memory kinds"
        >
          {memoryKinds.map((kind) => {
            const active =
              !memorySelectedKinds.length ||
              memorySelectedKinds.includes(kind);
            return (
              <button
                key={kind}
                type="button"
                className={`kind-chip${active ? " is-active" : ""}`}
                onClick={() => toggleMemoryKind(kind)}
              >
                {kind}
              </button>
            );
          })}
        </div>
        <div className="memory-toolbar">
          <select
            id="memory-state"
            aria-label="State filter"
            value={memoryStateFilter}
            onChange={(e) => setMemoryStateFilter(e.target.value)}
          >
            <option value="">Any state</option>
            {memoryStates.map((st) => (
              <option key={st} value={st}>
                {st}
              </option>
            ))}
          </select>
          <button type="submit" className="btn primary compact">
            {memorySearching ? "Searching…" : "Search"}
          </button>
        </div>
      </form>

      {memoryTraceId ? (
        <p className="trace-line" id="memory-trace">
          trace · {memoryTraceId}
        </p>
      ) : null}

      <div className="memory-results" id="memory-results">
        {!memoryResults.length && !memorySearching ? (
          <p className="muted">No results yet.</p>
        ) : (
          memoryResults.map((item, i) => (
            <article
              key={String(item.id || i)}
              className="memory-result"
              data-kind={item.kind || ""}
            >
              <header className="memory-result-head">
                <span className="memory-kind">{item.kind || "memory"}</span>
                {item.state ? (
                  <span className="memory-state muted">{item.state}</span>
                ) : null}
              </header>
              <p className="memory-result-body">
                {item.summary || item.content || item.task || "(empty)"}
              </p>
            </article>
          ))
        )}
      </div>
    </aside>
  );
}
