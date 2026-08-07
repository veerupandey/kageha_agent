/**
 * Centralized icon set for the Kageha Web UI.
 *
 * All UI icons flow through here so sizing, stroke width, and the visual
 * vocabulary stay consistent. Swap the underlying set in one place.
 *
 * Usage:
 *   import { Icon } from "../lib/icons";
 *   <Icon.NewThread />              // default size
 *   <Icon.NewThread size={16} />    // explicit size
 */
import {
  Activity,
  Archive,
  ArchiveRestore,
  ArrowUp,
  BookOpen,
  Brain,
  ChevronDown,
  ChevronRight,
  Folder,
  GitBranch,
  LayoutGrid,
  ListChecks,
  Menu,
  MessageSquare,
  Mic,
  Moon,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRight,
  Paperclip,
  Pencil,
  Pin,
  Plus,
  Search,
  Send,
  Settings,
  Share2,
  Sparkles,
  SquarePen,
  Star,
  Sun,
  Trash2,
  Webhook,
  Zap,
  type LucideProps,
} from "lucide-react";

export type IconProps = LucideProps;

const defaults: IconProps = {
  size: 18,
  strokeWidth: 1.75,
  "aria-hidden": true,
};

const wrap =
  (Cmp: typeof Sparkles) =>
  (props: IconProps) =>
    <Cmp {...defaults} {...props} />;

/** Concept → icon. Names describe purpose, not the lucide glyph. */
export const Icon = {
  // Brand
  Logo: wrap(Sparkles),
  // Composition / actions
  NewThread: wrap(SquarePen),
  Compose: wrap(Pencil),
  Send: wrap(Send),
  ArrowUp: wrap(ArrowUp),
  Attach: wrap(Paperclip),
  Plus: wrap(Plus),
  Mic: wrap(Mic),
  Search: wrap(Search),
  Share: wrap(Share2),
  More: wrap(MoreHorizontal),
  // Navigation / panels
  Menu: wrap(Menu),
  Collapse: wrap(PanelLeftClose),
  Expand: wrap(PanelLeftOpen),
  Canvas: wrap(PanelRight),
  CommandCenter: wrap(LayoutGrid),
  Chevron: wrap(ChevronDown),
  ChevronRight: wrap(ChevronRight),
  // Settings / theme
  Settings: wrap(Settings),
  Sun: wrap(Sun),
  Moon: wrap(Moon),
  // Agents
  Jobs: wrap(ListChecks),
  Worktrees: wrap(GitBranch),
  Brain: wrap(Brain),
  Hooks: wrap(Webhook),
  // Resources
  Skills: wrap(Zap),
  Memories: wrap(BookOpen),
  Projects: wrap(Folder),
  Library: wrap(BookOpen),
  // Thread context menu
  Pin: wrap(Pin),
  Unpin: wrap(Star),
  Archive: wrap(Archive),
  ArchiveRestore: wrap(ArchiveRestore),
  Rename: wrap(Pencil),
  Delete: wrap(Trash2),
  // Misc
  Activity: wrap(Activity),
  Message: wrap(MessageSquare),
} as const;
