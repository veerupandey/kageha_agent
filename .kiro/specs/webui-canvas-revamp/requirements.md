# Requirements: WebUI Canvas Revamp

## Overview

Revamp the Kageha webui to adopt a modern design language: a clean, professional workspace with a sidebar of Agents/Threads, a centered command input, a rich artifact canvas with tabbed filtering (All, Images, Webpages, Documents), and an elegant thread view with suggested follow-ups and contextual actions on artifacts.

## Functional Requirements

### FR-1: Sidebar Navigation (Canvas-style)
- **FR-1.1**: Replace current SessionsRail with a full sidebar containing:
  - Logo/brand at top ("Kageha" wordmark)
  - "New thread" button (accent-styled)
  - Search bar for threads/agents
  - "Agents" section with named agents (e.g., skill-based agents) grouped with colored dot indicators
  - "Recent threads" section showing recent conversations with status indicators (● Waiting for input, completed)
  - "Resources" section with links to: Teams, Skills, Memories, Learning, Library, Projects, Marketplace
  - User avatar + name at bottom
- **FR-1.2**: Sidebar should be collapsible on mobile, fixed on desktop (~250px wide)
- **FR-1.3**: Each thread row shows: title (truncated), subtitle (description snippet), timestamp ("3mo ago"), and thumbnail preview if artifacts present

### FR-2: Command Center Input (Canvas-style hero prompt)
- **FR-2.1**: When no thread is active (home state), show a large centered hero area:
  - Headline: "Let's get to work." (or configurable brand tagline)
  - Large rounded input box with placeholder "Ask anything or start a task..."
  - Below input: row of controls — attach (+), model selector dropdown, agent selector dropdown, mode selector (Plan/Execute/Normal)
  - "Connect your integrations →" link row with integration icons
- **FR-2.2**: Below the command center, show quick-action suggestion pills:
  - "Design a website", "Source candidates", "Research a topic", "Generate images", "More..."
  - These map to slash commands or predefined prompts
- **FR-2.3**: Below suggestions, show "Recent threads" cards in a grid:
  - Each card: title, description snippet, timestamp, optional thumbnail
  - Starred/pinned threads shown with a ☆ action
  - "Show all" link to full thread list

### FR-3: Thread View (Conversation Canvas)
- **FR-3.1**: Thread header bar with:
  - Thread title (editable, with dropdown to rename)
  - Status pills: "Live" indicator, model name pill, agent name
  - Tab row for artifact filtering: All (count), Images (count), Webpages (count), Documents (count)
  - View mode toggles (grid/list/detail)
- **FR-3.2**: Split layout within a thread:
  - Left panel (~40%): conversation messages + suggested follow-ups
  - Right panel (~60%): artifact canvas with thumbnails/previews
- **FR-3.3**: Conversation panel features:
  - Bullet-pointed structured responses (agent uses markdown)
  - Suggested follow-ups as clickable cards with arrow (→) at bottom of each response
  - "Add a follow-up..." input at bottom (mini composer)
- **FR-3.4**: Artifact canvas features:
  - Grid of artifact thumbnails (images, documents, webpages captured)
  - Grouped by type with count badges
  - Click to expand into a lightbox/detail view
  - Detail view shows: full-size preview, Actions panel (Remix, Copy to clipboard, Download), "Used in threads" backlinks
  - Horizontal scrollable strip of all artifacts at bottom of detail view

### FR-4: Artifact Detail / Lightbox
- **FR-4.1**: Click any artifact thumbnail → opens a lightbox overlay
- **FR-4.2**: Lightbox contains:
  - Left: full-size artifact preview (image, PDF render, code block, etc.)
  - Right sidebar: artifact metadata panel
    - Artifact name + timestamp
    - ACTIONS section: Remix, Copy to clipboard, Download
    - "USED IN THREADS" section listing which threads reference this artifact
- **FR-4.3**: Close lightbox via X button or Escape key
- **FR-4.4**: Keyboard navigation between artifacts (← →) while lightbox is open

### FR-5: Visual Design System
- **FR-5.1**: Dark mode by default, with light mode toggle (already exists)
- **FR-5.2**: Color palette:
  - Background: near-black canvas (#0f0f0f or similar)
  - Surface: dark gray cards (#1a1a1a)
  - Accent: warm gold/orange (#f5a623 or similar) for CTAs and active states
  - Text: off-white (#fafafa) primary, muted gray (#888) secondary
  - Status dots: green (active), orange (waiting), gray (idle)
- **FR-5.3**: Typography: Clean sans-serif, generous whitespace, compact information density
- **FR-5.4**: Rounded corners (lg/xl) on cards and inputs, subtle borders
- **FR-5.5**: Smooth transitions (150-200ms) for hover states, panel toggles, lightbox open/close

### FR-6: Agent/Skill Selection
- **FR-6.1**: Sidebar "Agents" section lists available skills/agents with colored dot indicators
- **FR-6.2**: Clicking an agent opens a thread pre-configured with that agent's context
- **FR-6.3**: "View all" expands to show all available agents/skills
- **FR-6.4**: "Command Center" entry in agents list acts as a general-purpose assistant

### FR-7: Responsive & Accessibility
- **FR-7.1**: Mobile: sidebar collapses to hamburger menu, canvas stacks below conversation
- **FR-7.2**: All interactive elements have proper aria labels
- **FR-7.3**: Keyboard navigation: Tab through sidebar items, Enter to open, Escape to close overlays
- **FR-7.4**: Reduced motion preference respected (prefers-reduced-motion media query)

## Non-Functional Requirements

### NFR-1: Performance
- Initial paint < 200ms (SPA with Vite, already satisfied)
- Artifact thumbnails lazy-loaded with IntersectionObserver
- Canvas items virtualized if > 50 items

### NFR-2: Compatibility
- Maintain existing WebSocket/SSE stream API contract — no backend changes for phase 1
- Reuse existing store (zustand) and data models
- Existing slash commands, @ mentions, voice input must continue working

### NFR-3: Incremental Migration
- New layout should be opt-in via a "New UI" toggle or replace existing (configurable)
- Components should be composable for future features (multi-agent tabs, learning center)

## Out of Scope (Phase 1)
- Marketplace / plugin system
- Teams / collaboration features
- Learning center content
- Backend API changes (use existing endpoints)
- Real "Remix" functionality (placeholder button only)
