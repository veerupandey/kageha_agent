import { useAppStore } from "../store";

export function TodoBoard() {
  const sessionId = useAppStore((s) => s.sessionId);
  const board = useAppStore((s) =>
    sessionId ? s.todoBoards[sessionId] : undefined,
  );
  const dismissed = useAppStore((s) => s.todoBoardDismissed);
  const dismissTodoBoard = useAppStore((s) => s.dismissTodoBoard);

  if (!sessionId || !board) return null;
  if (dismissed.includes(sessionId)) return null;

  return (
    <aside className="todo-board" id="todo-board" aria-label="Build todo board">
      <div className="todo-board-head">
        <details className="todo-board-details" id="todo-board-details" open>
          <summary className="todo-board-summary">
            <span className="todo-board-title">
              <span className="todo-board-chevron" aria-hidden="true">
                ▾
              </span>
              {board.label || "Todos"}
            </span>
            <span className="todo-board-progress" id="todo-board-progress">
              {board.done}/{board.total}
            </span>
          </summary>
          <ul className="todo-board-list" id="todo-board-list">
            {board.items.map((item, i) => (
              <li
                key={item.id || `todo-${i}`}
                className={item.done ? "is-done" : ""}
              >
                <span className="todo-check" aria-hidden="true">
                  {item.done ? "✓" : "○"}
                </span>
                <span className="todo-text">{item.text}</span>
              </li>
            ))}
          </ul>
        </details>
        <button
          type="button"
          className="btn ghost compact todo-board-close"
          id="btn-close-todo-board"
          aria-label="Hide todos"
          title="Hide todos"
          onClick={() => dismissTodoBoard(sessionId)}
        >
          Close
        </button>
      </div>
    </aside>
  );
}
