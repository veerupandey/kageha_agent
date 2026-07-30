# Design: WebUI Canvas Revamp

## Architecture Overview

The revamp restructures the frontend into a **3-panel layout** (Sidebar → Main Content → Canvas), with the Main Content area switching between a **Home state** (command center) and a **Thread state** (conversation + artifacts).

```
┌────────────────────────────────────────────────────────────────────┐
│ App Shell                                                          │
├──────────┬─────────────────────────────────────────────────────────┤
│ Sidebar  │  Main Content Area                                      │
│ (250px)  │  ┌─────────────────────────────────────────────────────┐│
│          │  │ Home: CommandCenter / Thread: ThreadView             ││
│ • Logo   │  │                                                     ││
│ • New    │  │  [Thread mode splits into:]                         ││
│ • Search │  │  ┌──────────────┬───────────────────────────────┐  ││
│ • Agents │  │  │ Conversation │  Artifact Canvas              │  ││
│ • Recent │  │  │    (40%)     │     (60%)                     │  ││
│ • Resrc  │  │  │              │                               │  ││
│ • User   │  │  └──────────────┴───────────────────────────────┘  ││
│          │  └─────────────────────────────────────────────────────┘│
└──────────┴─────────────────────────────────────────────────────────┘
```

## Component Hierarchy

```
App
├── Sidebar (new)
│   ├── SidebarHeader (logo + new thread button)
│   ├── SidebarSearch
│   ├── AgentsList
│   │   └── AgentRow
│   ├── RecentThreadsList
│   │   └── ThreadRow
│   ├── ResourcesNav
│   └── SidebarFooter (user avatar)
├── MainContent
│   ├── [Home state] CommandCenter
│   │   ├── HeroInput
│   │   ├── QuickActions (suggestion pills)
│   │   └── RecentThreadsGrid
│   │       └── ThreadCard
│   ├── [Thread state] ThreadView
│   │   ├── ThreadHeader
│   │   │   ├── ThreadTitle (editable)
│   │   │   ├── StatusPills
│   │   │   ├── ArtifactTabs (All/Images/Webpages/Documents)
│   │   │   └── ViewModeToggle
│   │   ├── ThreadBody (split)
│   │   │   ├── ConversationPanel
│   │   │   │   ├── MessageList (existing, restyled)
│   │   │   │   ├── SuggestedFollowUps
│   │   │   │   └── MiniComposer
│   │   │   └── ArtifactPanel
│   │   │       ├── ArtifactGrid
│   │   │       │   └── ArtifactThumb
│   │   │       └── ArtifactStrip (bottom scroller)
│   │   └── ThreadComposer (bottom bar)
├── ArtifactLightbox (portal/overlay)
│   ├── LightboxPreview
│   └── LightboxSidebar (actions + metadata)
├── CommandPalette (existing)
├── Toasts (existing)
└── DropOverlay (existing)
```

## State Management Changes

Extend the existing Zustand store with:

```typescript
// New state additions to AppState
interface AppState {
  // ... existing fields ...

  // Home vs Thread view
  viewMode: "home" | "thread";

  // Sidebar
  sidebarCollapsed: boolean;
  agents: AgentEntry[];

  // Artifact canvas in thread
  artifactFilter: "all" | "images" | "webpages" | "documents";
  artifactViewMode: "grid" | "list";
  
  // Lightbox
  lightboxOpen: boolean;
  lightboxItemPath: string | null;

  // Quick actions / suggestions
  suggestedFollowUps: string[];

  // Actions
  setSidebarCollapsed: (v: boolean) => void;
  setViewMode: (mode: "home" | "thread") => void;
  setArtifactFilter: (filter: "all" | "images" | "webpages" | "documents") => void;
  setArtifactViewMode: (mode: "grid" | "list") => void;
  openLightbox: (path: string) => void;
  closeLightbox: () => void;
  navigateLightbox: (direction: "prev" | "next") => void;
}

interface AgentEntry {
  id: string;
  name: string;
  color: string; // dot color
  description?: string;
  skillId?: string;
}
```

## Routing / View Logic

No router needed. View switching is state-driven:

```
viewMode === "home" && !sessionId  → CommandCenter
viewMode === "thread" || sessionId → ThreadView
```

Opening a session sets `viewMode: "thread"`. Clicking the logo/home returns to `viewMode: "home"`.

## Key Component Designs

### Sidebar

- Fixed 250px width on desktop, slides in/out on mobile
- Dark background (slightly lighter than canvas)
- Sections separated by subtle dividers
- Agents list populated from `/api/meta` skills or a new `/api/agents` endpoint (fallback: hardcoded from skills)
- Thread list reuses existing `sessions` from store, sorted by `updated_at`

### CommandCenter (Home)

- Vertically centered within MainContent
- Large heading: "Let's get to work." (36px, bold)
- Input: 600px max-width, rounded-2xl, 48px height, subtle shadow
- Below input: model + agent selectors inline
- Quick action pills: horizontal wrap, pill-shaped buttons with icons
- Recent threads: 2-column card grid, max 6 shown

### ThreadView

- Header bar (h-14) with thread metadata
- Split below header:
  - ConversationPanel: scrollable, max-width prose, styled markdown
  - ArtifactPanel: grid layout, responsive columns (2-4 depending on width)
- ConversationPanel ends with SuggestedFollowUps (clickable cards) and MiniComposer

### ArtifactLightbox

- Fixed overlay (z-60), backdrop blur
- Centered modal: 90vw × 85vh max
- Left: preview area (object-fit contain for images, iframe for webpages, code block for text)
- Right: 280px sidebar with actions and metadata
- Close: X button (top-right), Escape key, backdrop click
- Navigate: ← → arrow keys, clickable arrows

## File Structure (New/Modified)

```
src/components/
├── Sidebar/
│   ├── Sidebar.tsx
│   ├── SidebarHeader.tsx
│   ├── AgentsList.tsx
│   ├── RecentThreadsList.tsx
│   ├── ResourcesNav.tsx
│   └── SidebarFooter.tsx
├── CommandCenter/
│   ├── CommandCenter.tsx
│   ├── HeroInput.tsx
│   ├── QuickActions.tsx
│   └── ThreadCard.tsx
├── ThreadView/
│   ├── ThreadView.tsx
│   ├── ThreadHeader.tsx
│   ├── ConversationPanel.tsx
│   ├── SuggestedFollowUps.tsx
│   ├── MiniComposer.tsx
│   ├── ArtifactPanel.tsx
│   ├── ArtifactGrid.tsx
│   └── ArtifactThumb.tsx
├── Lightbox/
│   ├── ArtifactLightbox.tsx
│   ├── LightboxPreview.tsx
│   └── LightboxSidebar.tsx
├── shared/
│   ├── StatusDot.tsx
│   ├── Pill.tsx
│   └── IconButton.tsx
├── App.tsx (modified — new layout shell)
├── Stage.tsx (deprecated, functionality moved to ThreadView)
└── ... (existing components kept for backward compat)
```

## Styling Approach

- Continue using Tailwind CSS v4 (already configured)
- Add CSS custom properties for the Canvas color palette in `styles/`
- Dark mode is default; light mode adjusts custom properties
- Use `@layer components` for repeated patterns (pills, cards, sidebar items)

### Color Tokens (Dark Mode Default)

```css
:root {
  --canvas: #0c0c0c;
  --surface: #161616;
  --surface-hover: #1f1f1f;
  --border: #2a2a2a;
  --border-strong: #3a3a3a;
  --text-primary: #fafafa;
  --text-secondary: #999;
  --text-muted: #666;
  --accent: #f5a623;
  --accent-soft: rgba(245, 166, 35, 0.12);
  --success: #34d399;
  --warning: #fbbf24;
  --danger: #ef4444;
}
```

## Data Flow

1. **Boot**: Fetch sessions, meta, capabilities (unchanged)
2. **Home → Thread**: User clicks a thread in sidebar/grid OR starts new chat
3. **Thread streaming**: Existing SSE/WS stream API (unchanged)
4. **Artifacts**: Existing `/api/sessions/{id}/artifacts` endpoint provides canvas items
5. **Agents list**: Derived from `/api/meta` → `features` or skills catalog
6. **Suggested follow-ups**: Parsed from assistant message metadata (if present in stream events) or generated client-side from context

## Migration Strategy

1. Build new components alongside existing ones
2. App.tsx switches layout based on a feature flag (`useNewUI` pref or URL param `?ui=new`)
3. Once stable, make new layout the default and deprecate old Stage/SessionsRail
4. Remove deprecated components in a follow-up PR

## Accessibility Considerations

- Sidebar navigation uses `<nav>` landmark with `aria-label="Main navigation"`
- Lightbox traps focus, returns focus on close
- All images in artifact grid have `alt` text (filename-derived)
- Artifact tabs use proper `role="tablist"` / `role="tab"` / `role="tabpanel"`
- Color contrast ratios meet WCAG 2.1 AA (4.5:1 for text)
- `prefers-reduced-motion` disables transitions
