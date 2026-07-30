# Tasks: WebUI Canvas Revamp

## Reconciliation note (2026-07-30)

This checklist was left unchecked despite most of the plan being implemented across
commits `f97eba3` and `fd50594`. Boxes below reflect actual, verified code state as of
this reconciliation pass (frontend `npm ci && npm run lint && npm run build && npx vitest run`
all pass: 0 lint errors, clean build, 42/42 tests). Sub-bullets note where the
implementation is functionally equivalent but structured differently than originally
specified (e.g. component-local `useState` instead of Zustand store fields) — these are
flagged, not silently marked done.

## Phase 1: Foundation & Layout Shell

### Task 1: Color tokens & design system setup
- [x] Add Canvas dark color palette as CSS custom properties in `src/styles/canvas.css`
- [x] Define light mode overrides under `[data-theme="light"]`
- [x] Create shared utility components: `StatusDot.tsx`, `Pill.tsx`, `IconButton.tsx` in `src/components/shared/`
- [x] Import new styles in `main.tsx`

### Task 2: App Shell layout refactor
- [x] Create new `AppShell.tsx` component with 3-panel flexbox layout (sidebar + main content)
- [x] Add `viewMode`/`sidebarCollapsed`-equivalent state — *implemented as local `useState` in `AppShell.tsx` (`isHome`, `sidebarOpen`), not in the Zustand store as originally specified. Functionally equivalent for current usage; revisit if another component needs to read view mode.*
- [x] Modify `App.tsx` to conditionally render `AppShell` (new UI) vs old layout based on `prefs.newUi` flag
- [x] Add `newUi: boolean` to `UserPrefs` type and prefs slice

### Task 3: Sidebar component
- [x] Create `src/components/Sidebar/Sidebar.tsx` — full sidebar with all sections
- [x] Create `SidebarHeader.tsx` — Kageha logo + "New thread" button
- [x] Create `SidebarSearch.tsx` — search input filtering threads
- [x] Create `AgentsList.tsx` — list agents/skills from store with colored dots
- [x] Create `RecentThreadsList.tsx` — recent sessions with status indicators + thumbnails
- [x] Create `ResourcesNav.tsx` — static links (Skills, Memories, Projects, etc.)
- [x] Create `SidebarFooter.tsx` — user avatar + settings toggle (now wired to `prefs.newUi`)
- [x] Wire sidebar to store: collapse toggle, session opening, new chat creation

## Phase 2: Command Center (Home View)

### Task 4: CommandCenter component
- [x] Create `src/components/CommandCenter/CommandCenter.tsx` — hero layout wrapper
- [x] Create `HeroInput.tsx` — large centered input with model/agent/mode selectors below
- [x] Wire HeroInput to existing `sendMessage` + `setDraft` + `setModelOverride` store actions
- [ ] Ensure existing slash command picker and @ mention picker work within HeroInput — *not confirmed wired; HeroInput is a plain textarea without visible picker integration. Follow-up needed.*

### Task 5: Quick actions & recent threads grid
- [x] Create `QuickActions.tsx` — row of suggestion pills mapped to predefined prompts
- [x] Create `ThreadCard.tsx` — card component showing title, snippet, timestamp *(no thumbnail image support yet)*
- [x] Recent-threads grid — *implemented inline in `CommandCenter.tsx` rather than as a separate `RecentThreadsGrid.tsx` file. Same visual/behavioral result.*
- [x] Add click handler: clicking a ThreadCard opens that session (calls `openSession`)
- [ ] Add click handler: clicking a QuickAction sets draft and optionally auto-sends — *only sets draft; auto-send is not implemented.*

## Phase 3: Thread View (Conversation + Canvas)

### Task 6: ThreadView layout
- [x] Create `src/components/ThreadView/ThreadView.tsx` — split layout (conversation left, artifacts right)
- [x] Create `ThreadHeader.tsx` — thread title, status pills, artifact tabs, view toggle
- [x] `artifactFilter` — *implemented as local `useState` in `ThreadView.tsx`, not in the store as specified.* `artifactViewMode` (grid/list toggle) — **not implemented**; only the type-filter tabs exist.
- [x] Implement tab filtering logic (All/Images/Webpages/Documents) based on artifact MIME types

### Task 7: ConversationPanel
- [x] Create `ConversationPanel.tsx` — wraps existing MessageList with new styling
- [x] Restyle message bubbles: remove bubble chrome, use clean left-aligned prose with spacing
- [x] Create `SuggestedFollowUps.tsx` — renders follow-up suggestions as clickable row cards
- [x] Create `MiniComposer.tsx` — compact input at bottom of conversation panel ("Add a follow-up...")
- [x] Wire MiniComposer to `sendMessage` store action

### Task 8: ArtifactPanel
- [x] Create `ArtifactPanel.tsx` — right panel with grid of artifact thumbnails
- [x] Responsive CSS grid, grouped by type with count badges — *implemented directly in `ArtifactPanel.tsx` via `.ka-artifact-grid` CSS, not as a separate `ArtifactGrid.tsx` file.*
- [x] Create `ArtifactThumb.tsx` — individual thumbnail with name overlay, click to open lightbox
- [x] Implement lazy loading with IntersectionObserver for thumbnail images
- [x] Connect to existing `canvasItems` from store, filtered by `artifactFilter`

## Phase 4: Artifact Lightbox

### Task 9: Lightbox overlay
- [x] Create `src/components/Lightbox/ArtifactLightbox.tsx` — portal-based overlay with backdrop blur
- [x] Create `LightboxPreview.tsx` — renders image/PDF/video based on artifact type *(dedicated code-preview branch not distinct from generic fallback)*
- [x] Create `LightboxSidebar.tsx` — actions (Copy, Download; Remix is a stub/"coming soon") + metadata + "Used in threads" *(currently hardcoded to "Current thread", not real cross-referencing)*
- [x] Lightbox open/close/navigate state — *implemented as local `useState`/callbacks in `AppShell.tsx`, passed down as props, rather than in the Zustand store as specified. Works for current usage; not globally accessible outside AppShell's subtree.*
- [x] Implement keyboard navigation: Escape to close, ← → to navigate between artifacts
- [x] Implement focus trap within lightbox when open — *added in this reconciliation pass (Tab/Shift+Tab now cycles within the dialog, focus restored to the trigger on close).*
- [ ] Add horizontal artifact strip at bottom of lightbox for quick navigation — *not implemented; only prev/next arrow buttons exist.*

## Phase 5: Integration & Polish

### Task 10: Wire existing features into new layout
- [x] Ensure WebSocket/SSE streaming works in ThreadView (reuse existing `runTurn` logic)
- [x] Ensure approval banners render correctly in ConversationPanel
- [x] Ensure tool cards and activity steps display in new message styling
- [x] Ensure computer frames (browser/computer mode) display in ArtifactPanel (via `AgentCanvas`)
- [x] Ensure voice input (Mic button) works in both HeroInput and MiniComposer — *wired in this pass; both now reuse `startMicRecording`/`transcribeBlob`/`stopSpokenReply` from `lib/voiceClient`, with recording/transcribing states and error toasts.*
- [x] Ensure drag-and-drop file attachment works across both views — *already worked: drag-and-drop is handled at window level in `App.tsx` (`dragenter`/`dragover`/`drop` → `addPendingFiles`), so it is layout-agnostic. Attach-file buttons and pending-file chips were additionally wired into both composers in this pass.*

### Task 11: Transitions & animations
- [x] Add slide transition for sidebar collapse/expand
- [ ] Add fade-in for lightbox open/close — *backdrop blur exists; no confirmed fade transition on open/close.*
- [x] Add subtle scale-up on artifact thumbnail hover
- [x] Respect `prefers-reduced-motion` — disable all transitions when set
- [ ] Add smooth scroll behavior to conversation panel on new messages — *not confirmed implemented.*

### Task 12: Responsive behavior
- [x] Mobile (< 768px): sidebar is hamburger-triggered overlay, artifact panel stacks below conversation
- [x] Tablet (768-1024px): sidebar collapsed by default, artifact panel narrower (40%) — *implemented in this pass: `ThreadView` now uses `md:flex` for the artifact panel with the conversation at `md:max-w-[60%] lg:max-w-[50%]`, so tablet gets a ~40% artifact panel and desktop an even split.*
- [x] Desktop (> 1024px): full 3-panel layout
- [x] Test and fix touch interactions for lightbox (swipe to navigate) — *implemented in this pass: horizontal swipe with a 50px threshold, ignoring vertical scroll gestures (`|dx| > |dy|`).*

### Task 13: Final cleanup & feature flag
- [x] Add UI toggle in settings/prefs to switch between old and new UI — *added in this reconciliation pass (gear icon in `SidebarFooter` now opens a small menu with a `newUi` checkbox bound to `setPrefs`).*
- [x] Ensure old UI still works when `newUi: false` (verified: `App.tsx` renders the legacy `SessionsRail`/`Stage` layout unconditionally in that branch)
- [ ] Remove any console.logs, TODO comments — *not audited in this pass.*
- [x] Run `npm run lint` and fix all issues — verified: 0 errors, 4 pre-existing warnings in `AgentCanvas.tsx` (unrelated to this spec)
- [x] Run `npm run build` to verify production build succeeds — verified clean build
- [x] Write brief README section documenting the new UI flag — see `docs/USAGE.md` → WebUI section (added in this reconciliation pass)
