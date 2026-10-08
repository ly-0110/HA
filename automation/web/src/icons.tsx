import {
  ArrowRight, Check, ChevronRight, FileText, FolderOpen, History,
  Play, RefreshCw, SlidersHorizontal, Smartphone, SquarePlus, Workflow,
  type LucideIcon,
} from "lucide-react";

export type IconName = "devices" | "create" | "runs" | "history" | "settings" | "refresh" | "arrow" | "file" | "folder" | "check" | "chevron" | "brand";

const icons: Record<IconName, LucideIcon> = {
  devices: Smartphone, create: SquarePlus, runs: Play, history: History,
  settings: SlidersHorizontal, refresh: RefreshCw, arrow: ArrowRight,
  file: FileText, folder: FolderOpen, check: Check, chevron: ChevronRight,
  brand: Workflow,
};

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  const Glyph = icons[name];
  return <Glyph size={size} strokeWidth={1.8} aria-hidden="true" focusable="false" />;
}
