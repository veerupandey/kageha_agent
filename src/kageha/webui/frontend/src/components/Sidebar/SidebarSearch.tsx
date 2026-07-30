interface SidebarSearchProps {
  query: string;
  onChange: (value: string) => void;
}

export function SidebarSearch({ query, onChange }: SidebarSearchProps) {
  return (
    <div className="px-3 pb-2">
      <label className="sr-only" htmlFor="ka-sidebar-search">
        Search
      </label>
      <div className="relative">
        <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-xs text-faint">
          ⌕
        </span>
        <input
          id="ka-sidebar-search"
          type="search"
          className="w-full rounded-md border border-[var(--color-line)] bg-[var(--color-surface)] py-1.5 pl-7 pr-2.5 text-sm text-ink outline-none placeholder:text-faint focus:border-[var(--color-accent)]"
          placeholder="Search"
          autoComplete="off"
          value={query}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
    </div>
  );
}
