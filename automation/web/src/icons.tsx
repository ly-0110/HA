export type IconName = "devices" | "create" | "runs" | "history" | "settings" | "refresh" | "arrow" | "file" | "folder" | "check" | "chevron";

const paths: Record<IconName, React.ReactNode> = {
  devices: <><rect x="7" y="2" width="10" height="20" rx="3"/><path d="M10 5h4M11 19h2"/></>,
  create: <><rect x="3" y="3" width="18" height="18" rx="4"/><path d="M12 8v8M8 12h8"/></>,
  runs: <><path d="m9 6 9 6-9 6z"/><path d="M4 5v14"/></>,
  history: <><path d="M3 12a9 9 0 1 0 2.6-6.4L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></>,
  settings: <><path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/></>,
  refresh: <><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 7a7 7 0 0 1 11.5-2L20 8M4 16l2.4 3A7 7 0 0 0 18 17"/></>,
  arrow: <><path d="M5 12h14m-5-5 5 5-5 5"/></>,
  file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M8 13h8M8 17h6"/></>,
  folder: <path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>,
  check: <path d="m5 12 4 4L19 6"/>,
  chevron: <path d="m9 5 7 7-7 7"/>,
};

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
