import { useRef } from "react";
import type { AgentMode, Density } from "../api/types";
import { useFocusTrap } from "../lib/focusTrap";
import { useAppStore } from "../store";

const AGENT_MODE_OPTIONS: { id: AgentMode; label: string }[] = [
  { id: "normal", label: "Normal" },
  { id: "plan", label: "Plan" },
  { id: "spec", label: "Spec" },
  { id: "goal", label: "Goal" },
];

export function SettingsDrawer() {
  const open = useAppStore((s) => s.drawers.settings);
  const prefs = useAppStore((s) => s.prefs);
  const closeDrawer = useAppStore((s) => s.closeDrawer);
  const setPrefs = useAppStore((s) => s.setPrefs);
  const drawerRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useFocusTrap(open, drawerRef, { initialFocusRef: closeRef });

  return (
    <aside
      ref={drawerRef}
      className={`settings drawer${open ? " open" : ""}`}
      id="settings-drawer"
      aria-hidden={open ? "false" : "true"}
    >
      <header className="settings-head">
        <div>
          <p className="eyebrow">Preferences</p>
          <h2 className="drawer-title">Settings</h2>
        </div>
        <button
          ref={closeRef}
          type="button"
          className="btn ghost"
          id="btn-close-settings"
          aria-label="Close"
          onClick={() => closeDrawer("settings")}
        >
          Close
        </button>
      </header>

      <div className="settings-body">
        <fieldset className="settings-field">
          <legend>Density</legend>
          <div className="settings-seg" role="group" aria-label="Density">
            {(["comfortable", "compact"] as Density[]).map((d) => (
              <button
                key={d}
                type="button"
                className={`btn ghost compact${prefs.density === d ? " is-active" : ""}`}
                aria-pressed={prefs.density === d}
                onClick={() => setPrefs({ density: d })}
              >
                {d === "comfortable" ? "Comfortable" : "Compact"}
              </button>
            ))}
          </div>
        </fieldset>

        <fieldset className="settings-field">
          <legend>Default tool approval</legend>
          <div className="settings-seg" role="group" aria-label="Ask or Auto">
            <button
              type="button"
              className={`btn ghost compact${prefs.defaultAskMode ? " is-active" : ""}`}
              aria-pressed={prefs.defaultAskMode}
              onClick={() => setPrefs({ defaultAskMode: true })}
            >
              Ask
            </button>
            <button
              type="button"
              className={`btn ghost compact${!prefs.defaultAskMode ? " is-active" : ""}`}
              aria-pressed={!prefs.defaultAskMode}
              onClick={() => setPrefs({ defaultAskMode: false })}
            >
              Auto
            </button>
          </div>
          <p className="settings-hint">
            Applied on boot and when starting a new session.
          </p>
        </fieldset>

        <fieldset className="settings-field">
          <legend>Default agent mode</legend>
          <div className="settings-seg settings-seg-wrap" role="group">
            {AGENT_MODE_OPTIONS.map((opt) => (
              <button
                key={opt.id}
                type="button"
                className={`btn ghost compact${prefs.defaultAgentMode === opt.id ? " is-active" : ""}`}
                aria-pressed={prefs.defaultAgentMode === opt.id}
                onClick={() => setPrefs({ defaultAgentMode: opt.id })}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </fieldset>

        <label className="settings-toggle">
          <input
            type="checkbox"
            checked={prefs.reduceMotion}
            onChange={(e) => setPrefs({ reduceMotion: e.target.checked })}
          />
          <span>Reduce motion</span>
        </label>

        <label className="settings-toggle">
          <input
            type="checkbox"
            checked={prefs.showToolCards}
            onChange={(e) => setPrefs({ showToolCards: e.target.checked })}
          />
          <span>Show tool cards</span>
        </label>
      </div>
    </aside>
  );
}
