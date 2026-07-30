# Tasks: WebUI Canvas Revamp

## Phase 1: Foundation & Layout Shell

### Task 1: Color tokens & design system setup
- [ ] Add Canvas dark color palette as CSS custom properties in `src/styles/canvas.css`
- [ ] Define light mode overrides under `[data-theme="light"]`
- [ ] Create shared utility components: `StatusDot.tsx`, `Pill.tsx`, `IconButton.tsx` in `src/components/shared/`
- [ ] Import new styles in `main.tsx`

### Task 2: App Shell layout refactor
- [ ] Create new `AppShell.tsx` component with 3-panel flexbox layout (sidebar + main content)
- [ ] Add `viewMode: "home" | "thread"` and `sidebarCollapsed` to Zustand store
- [ ] Modify `App.tsx` to conditionally render `AppShell` (new UI) vs old layout based on `prefs.newUi` flag
- [ ] Add `newUi: boolean` to `UserPrefs` type and prefs slice

### Task 3: Sidebar component
- [ ] Create `src/components/Sidebar/Sidebar.tsx` — full sidebar with all sections
- [ ] Create `SidebarHeader.tsx` — Kageha logo + "New thread" button
- [ ] Create `SidebarSearch.tsx` — search input filtering threads
- [ ] Create `AgentsList.tsx` — list agents/skills from store with colored dots
- [ ] Create `RecentThreadsList.tsx` — recent sessions with status indicators + thumbnails
- [ ] Create `ResourcesNav.tsx` — static links (Skills, Memories, Projects, etc.)
- [ ] Create `SidebarFooter.tsx` — user avatar placeholder
- [ ] Wire sidebar to store: collapse toggle, session opening, new chat creation

## Phase 2: Command Center (Home View)

### Task 4: CommandCenter component
- [ ] Create `src/components/CommandCenter/CommandCenter.tsx` — hero layout wrapper
- [ ] Create `HeroInput.tsx` — large centered input with model/agent/mode selectors below
- [ ] Wire HeroInput to existing `sendMessage` + `setDraft` + `setModelOverride` store actions
- [ ] Ensure existing slash command picker and @ mention picker work within HeroInput

### Task 5: Quick actions & recent threads grid
- [ ] Create `QuickActions.tsx` — row of suggestion pills mapped to predefined prompts
- [ ] Create `ThreadCard.tsx` — card component showing title, snippet, timestamp, thumbnail
- [ ] Create `RecentThreadsGrid.tsx` — 2-column grid of ThreadCards from recent sessions
- [ ] Add click handler: clicking a ThreadCard opens that session (calls `openSession`)
- [ ] Add click handler: clicking a QuickAction sets draft and optionally auto-sends

## Phase 3: Thread View (Conversation + Canvas)

### Task 6: ThreadView layout
- [ ] Create `src/components/ThreadView/ThreadView.tsx` — split layout (conversation left, artifacts right)
- [ ] Create `ThreadHeader.tsx` — thread title, status pills, artifact tabs, view toggle
- [ ] Add `artifactFilter` and `artifactViewMode` to store
- [ ] Implement tab filtering logic (All/Images/Webpages/Documents) based on artifact MIME types

### Task 7: ConversationPanel
- [ ] Create `ConversationPanel.tsx` — wraps existing MessageList with new styling
- [ ] Restyle message bubbles: remove bubble chrome, use clean left-aligned prose with spacing
- [ ] Create `SuggestedFollowUps.tsx` — renders follow-up suggestions as clickable row cards
- [ ] Create `MiniComposer.tsx` — compact input at bottom of conversation panel ("Add a follow-up...")
- [ ] Wire MiniComposer to `sendMessage` store action

### Task 8: ArtifactPanel
- [ ] Create `ArtifactPanel.tsx` — right panel with grid of artifact thumbnails
- [ ] Create `ArtifactGrid.tsx` — responsive CSS grid, grouped by type with count badges
- [ ] Create `ArtifactThumb.tsx` — individual thumbnail with name overlay, click to open lightbox
- [ ] Implement lazy loading with IntersectionObserver for thumbnail images
- [ ] Connect to existing `canvasItems` from store, filtered by `artifactFilter`

## Phase 4: Artifact Lightbox

### Task 9: Lightbox overlay
- [ ] Create `src/components/Lightbox/ArtifactLightbox.tsx` — portal-based overlay with backdrop blur
- [ ] Create `LightboxPreview.tsx` — renders image/PDF/code/video based on artifact type
- [ ] Create `LightboxSidebar.tsx` — actions (Remix, Copy, Download) + metadata + "Used in threads"
- [ ] Add `lightboxOpen`, `lightboxItemPath`, `openLightbox`, `closeLightbox`, `navigateLightbox` to store
- [ ] Implement keyboard navigation: Escape to close, ← → to navigate between artifacts
- [ ] Implement focus trap within lightbox when open
- [ ] Add horizontal artifact strip at bottom of lightbox for quick navigation

## Phase 5: Integration & Polish

### Task 10: Wire existing features into new layout
- [ ] Ensure WebSocket/SSE streaming works in ThreadView (reuse existing `runTurn` logic)
- [ ] Ensure approval banners render correctly in ConversationPanel
- [ ] Ensure tool cards and activity steps display in new message styling
- [ ] Ensure computer frames (browser/computer mode) display in ArtifactPanel
- [ ] Ensure voice input (Mic button) works in both HeroInput and MiniComposer
- [ ] Ensure drag-and-drop file attachment works across both views

### Task 11: Transitions & animations
- [ ] Add slide transition for sidebar collapse/expand
- [ ] Add fade-in for lightbox open/close
- [ ] Add subtle scale-up on artifact thumbnail hover
- [ ] Respect `prefers-reduced-motion` — disable all transitions when set
- [ ] Add smooth scroll behavior to conversation panel on new messages

### Task 12: Responsive behavior
- [ ] Mobile (< 768px): sidebar is hamburger-triggered overlay, artifact panel stacks below conversation
- [ ] Tablet (768-1024px): sidebar collapsed by default, artifact panel narrower (40%)
- [ ] Desktop (> 1024px): full 3-panel layout
- [ ] Test and fix touch interactions for lightbox (swipe to navigate)

### Task 13: Final cleanup & feature flag
- [ ] Add UI toggle in settings/prefs to switch between old and new UI
- [ ] Ensure old UI still works when `newUi: false`
- [ ] Remove any console.logs, TODO comments
- [ ] Run `npm run lint` and fix all issues
- [ ] Run `npm run build` to verify production build succeeds
- [ ] Write brief README section documenting the new UI flag
