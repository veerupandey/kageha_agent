import { memo, useEffect, useRef, useState } from "react";
import { cn } from "../lib/cn";

export interface TodoItem {
  id: string;
  text: string;
  done: boolean;
}

export interface TodoBoardData {
  done: number;
  total: number;
  items: TodoItem[];
}

/** Animated check icon — scales in with a spring. */
function CheckIcon({ done }: { done: boolean }) {
  return (
    <span
      className={cn(
        "relative mt-[3px] flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full",
        "transition-all duration-300 ease-out",
        done
          ? "bg-accent text-white scale-100"
          : "border border-line-strong bg-surface scale-100",
      )}
    >
      {done ? (
        <svg
          width="10"
          height="10"
          viewBox="0 0 12 12"
          fill="none"
          className="animate-[check-draw_0.25s_ease-out_forwards]"
        >
          <path
            d="M2 6.5L4.5 9L10 3"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      ) : (
        <span className="h-1.5 w-1.5 rounded-full bg-line-strong" />
      )}
    </span>
  );
}

/** Smooth progress bar with glow effect when active. */
function ProgressBar({
  progress,
  done,
  active,
}: {
  progress: number;
  done: boolean;
  active: boolean;
}) {
  return (
    <div className="relative h-1 flex-1 overflow-hidden rounded-full bg-line">
      <div
        className={cn(
          "absolute inset-y-0 left-0 rounded-full transition-all duration-700 ease-out",
          done ? "bg-accent" : "bg-accent/80",
        )}
        style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
      />
      {/* Pulse shimmer while actively working */}
      {active && !done && (
        <div
          className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-transparent via-white/20 to-transparent animate-[shimmer_2s_ease-in-out_infinite]"
          style={{ width: `${Math.min(100, progress + 15)}%` }}
        />
      )}
    </div>
  );
}

/** Live todo/milestone board — shows agent progress as an elegant checklist. */
export const TodoBoard = memo(function TodoBoard({
  board,
  collapsed: collapsedProp,
}: {
  board: TodoBoardData | null;
  collapsed?: boolean;
}) {
  const [collapsed, setCollapsed] = useState(collapsedProp ?? false);
  const [prevDone, setPrevDone] = useState(0);
  const justCompletedRef = useRef<string | null>(null);

  // Track newly completed items for animation
  useEffect(() => {
    if (!board) return;
    if (board.done > prevDone && board.items.length) {
      const newlyDone = board.items.find(
        (it) => it.done && !justCompletedRef.current,
      );
      if (newlyDone) justCompletedRef.current = newlyDone.id;
      const timer = setTimeout(() => {
        justCompletedRef.current = null;
      }, 600);
      return () => clearTimeout(timer);
    }
    setPrevDone(board.done);
  }, [board?.done, board?.items, prevDone]);

  if (!board || !board.total) return null;

  const progress = board.total > 0 ? (board.done / board.total) * 100 : 0;
  const allDone = board.done === board.total;
  const active = !allDone && board.done > 0;

  return (
    <div
      className={cn(
        "rounded-lg border overflow-hidden transition-all duration-300",
        allDone
          ? "border-accent/30 bg-accent-soft/50"
          : "border-line bg-surface",
      )}
    >
      {/* Header — always visible, clickable to collapse/expand */}
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        className={cn(
          "flex w-full items-center gap-3 px-3 py-2 text-left transition-colors",
          "hover:bg-line/30",
        )}
      >
        {/* Collapse chevron */}
        <svg
          width="12"
          height="12"
          viewBox="0 0 12 12"
          className={cn(
            "shrink-0 text-muted transition-transform duration-200",
            collapsed ? "-rotate-90" : "rotate-0",
          )}
        >
          <path
            d="M3 4.5L6 7.5L9 4.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </svg>

        <span className="text-[0.7rem] font-semibold uppercase tracking-wider text-muted">
          {allDone ? "Completed" : "Working"}
        </span>

        <ProgressBar progress={progress} done={allDone} active={active} />

        <span
          className={cn(
            "text-xs font-semibold tabular-nums",
            allDone ? "text-accent" : "text-muted",
          )}
        >
          {board.done}/{board.total}
        </span>

        {allDone && (
          <span className="flex h-5 items-center rounded-full bg-accent/10 px-2 text-[0.65rem] font-semibold text-accent">
            Done
          </span>
        )}
      </button>

      {/* Item list — collapsible */}
      <div
        className={cn(
          "grid transition-all duration-300 ease-out",
          collapsed ? "grid-rows-[0fr]" : "grid-rows-[1fr]",
        )}
      >
        <div className="overflow-hidden">
          <ul className="border-t border-line/60 px-3 py-1.5">
            {board.items.map((item, i) => (
              <li
                key={item.id}
                className={cn(
                  "flex items-start gap-2.5 py-[5px] transition-all duration-300",
                  item.done ? "opacity-60" : "opacity-100",
                  justCompletedRef.current === item.id &&
                    "animate-[flash_0.5s_ease-out]",
                )}
                style={{
                  transitionDelay: `${i * 30}ms`,
                }}
              >
                <CheckIcon done={item.done} />
                <span
                  className={cn(
                    "min-w-0 flex-1 text-[0.82rem] leading-snug transition-colors duration-300",
                    item.done ? "text-muted" : "text-ink",
                  )}
                >
                  {item.text}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
});
