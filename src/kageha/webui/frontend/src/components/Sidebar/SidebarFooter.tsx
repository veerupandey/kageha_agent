import { useState } from "react";
import { Icon } from "../../lib/icons";
import { useAppStore } from "../../store";
import type { ThemeMode } from "../../api/types";

export function SidebarFooter() {
  const theme = useAppStore((s) => s.prefs.theme);
  const density = useAppStore((s) => s.prefs.density);
  const newUi = useAppStore((s) => s.prefs.newUi);
  const reduceMotion = useAppStore((s) => s.prefs.reduceMotion);
  const setPrefs = useAppStore((s) => s.setPrefs);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const toggleTheme = () => {
    const next: ThemeMode = theme === "dark" ? "light" : "dark";
    setPrefs({ theme: next });
  };

  return (
    <div className="relative mt-auto border-t border-[var(--color-line)] px-3 py-2.5">
      {/* Settings panel */}
      {settingsOpen && (
        <>
          <div
            className="fixed inset-0 z-10"
            onClick={() => setSettingsOpen(false)}
          />
          <div
            className="absolute bottom-full left-2 right-2 z-20 mb-2 rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] p-3 shadow-xl"
            role="menu"
          >
            <p className="mb-2 text-[0.6rem] font-semibold uppercase tracking-wider text-faint">
              Settings
            </p>
            <div className="space-y-2">
              <SettingRow label="Theme" sublabel={theme === "dark" ? "Dark" : "Light"}>
                <button
                  type="button"
                  className="inline-flex items-center gap-1 rounded-md bg-line/50 px-2 py-1 text-xs font-medium text-ink hover:bg-line transition-colors"
                  onClick={toggleTheme}
                >
                  {theme === "dark" ? <Icon.Sun size={13} /> : <Icon.Moon size={13} />}
                  {theme === "dark" ? "Light" : "Dark"}
                </button>
              </SettingRow>
              <SettingRow label="Density" sublabel={density}>
                <button
                  type="button"
                  className="rounded-md bg-line/50 px-2 py-1 text-xs font-medium text-ink hover:bg-line transition-colors"
                  onClick={() => setPrefs({ density: density === "comfortable" ? "compact" : "comfortable" })}
                >
                  {density === "comfortable" ? "Compact" : "Comfortable"}
                </button>
              </SettingRow>
              <SettingRow label="Reduce motion">
                <ToggleSwitch
                  checked={reduceMotion}
                  onChange={(v) => setPrefs({ reduceMotion: v })}
                />
              </SettingRow>
              <SettingRow label="Canvas UI">
                <ToggleSwitch
                  checked={newUi}
                  onChange={(v) => setPrefs({ newUi: v })}
                />
              </SettingRow>
            </div>
            <div className="mt-3 border-t border-line/50 pt-2">
              <p className="text-[0.55rem] text-faint">
                ⌘K Command palette · ⌘N New thread · ⇧↵ New line
              </p>
            </div>
          </div>
        </>
      )}

      <div className="flex items-center gap-2">
        {/* Theme quick toggle */}
        <button
          type="button"
          className="ka-icon-btn h-7 w-7"
          title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
          onClick={toggleTheme}
        >
          {theme === "dark" ? <Icon.Sun size={16} /> : <Icon.Moon size={16} />}
        </button>
        <span className="min-w-0 flex-1 text-[0.7rem] text-faint">
          ⌘K palette
        </span>
        <button
          type="button"
          className="ka-icon-btn h-7 w-7"
          aria-label="Settings"
          title="Settings"
          onClick={() => setSettingsOpen((v) => !v)}
        >
          <Icon.Settings size={16} />
        </button>
      </div>
    </div>
  );
}

function SettingRow({
  label,
  sublabel,
  children,
}: {
  label: string;
  sublabel?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <div>
        <p className="text-xs font-medium text-ink">{label}</p>
        {sublabel && <p className="text-[0.6rem] text-faint">{sublabel}</p>}
      </div>
      {children}
    </div>
  );
}

function ToggleSwitch({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${checked ? "bg-accent" : "bg-line-strong"}`}
      onClick={() => onChange(!checked)}
    >
      <span
        className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform ${checked ? "translate-x-4" : "translate-x-0.5"}`}
      />
    </button>
  );
}
