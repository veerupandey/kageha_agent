import { memo } from "react";
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

/** Live todo/milestone board — shows agent progress as a checklist. */
export const TodoBoard = memo(function TodoBoard({
  board,
  collapsed,
}: {
  board: TodoBoardData | null;
  collapsed?: boolean;
}) {
  if (!board || !board.total) return null;

  const progress = board.total > 0 ? (board.done / board.total) * 100 : 0;
  const allDone = board.done === board.total;

  if (collapsed) {
    return (
      <div className="flex items-center gap-2 px-3 py-1.5 text-xs text-muted">
        <span
          className={cn(
            "font-semibold tabular-nums",
            allDone ? "text-emerald-600" : "text-accent",
          )}
        >
          {board.done}/{board.total}
        </span>
        <div className="h-1.5 flex-1 rounded-full bg-line">
          <div
            className={cn(
              "h-full rounded-full transition-all duration-500",
              allDone ? "bg-emerald-500" : "bg-accent",
            )}
            style={{ width: `${progress}%` }}
          />
        </div>
        {allDone && (
          <span className="text-emerald-600 font-medium">Done</span>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-line bg-surface overflow-hidden">
      {/* Header with progress bar */}
      <div className="flex items-center gap-3 border-b border-line px-3 py-2">
        <span className="text-xs font-semibold text-muted uppercase tracking-wide">
          Progress
        </span>
        <div className="h-1.5 flex-1 rounded-full bg-line">
          <div
            className={cn(
              "h-full rounded-full transition-all duration-500",
              allDone ? "bg-emerald-500" : "bg-accent",
            )}
            style={{ width: `${progress}%` }}
          />
        </div>
        <span
          className={cn(
            "text-xs font-semibold tabular-nums",
            allDone ? "text-emerald-600" : "text-accent",
          )}
        >
          {board.done}/{board.total}
        </span>
      </div>

      {/* Item list */}
      <ul className="divide-y divide-line/50 px-3 py-1">
        {board.items.map((item) => (
          <li
            key={item.id}
            className={cn(
              "flex items-start gap-2 py-1.5 text-sm transition-opacity",
              item.done ? "opacity-70" : "opacity-100",
            )}
          >
            <span
              className={cn(
                "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[0.6rem]",
                item.done
                  ? "bg-emerald-100 text-emerald-600 dark:bg-emerald-900/40 dark:text-emerald-400"
                  : "bg-line text-muted",
              )}
            >
              {item.done ? "✓" : "○"}
            </span>
            <span
              className={cn(
                "min-w-0 flex-1 leading-tight",
                item.done
                  ? "text-muted line-through decoration-muted/40"
                  : "text-ink",
              )}
            >
              {item.text}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
});
