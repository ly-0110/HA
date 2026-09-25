// Event display labels for the experiment console.
// Parameterized events (brightness / color temperature / scene / focus) carry an
// explicit target; two-state events keep their classic names.

export type TemplateEvent = {
  event_type: string;
  required_state?: string | null;
  expected_state?: string | null;
  target?: string | number | boolean | null;
};

export const eventName: Record<string, string> = {
  turn_on: "开灯",
  turn_off: "关灯",
  play_music: "播放音乐",
  pause_music: "暂停音乐",
  set_brightness: "亮度",
  set_color_temperature: "色温",
  select_scene: "情景",
  set_focus_mode: "专注模式",
};

const parameterizedTypes = new Set(["set_brightness", "set_color_temperature", "select_scene", "set_focus_mode"]);

export function isParameterized(event: TemplateEvent): boolean {
  return parameterizedTypes.has(event.event_type);
}

export function formatTarget(event: TemplateEvent): string {
  const target = event.target;
  switch (event.event_type) {
    case "set_brightness":
      return `亮度→${String(target)}%`;
    case "set_color_temperature":
      return `色温→${String(target)}K`;
    case "select_scene":
      return `情景→${String(target)}`;
    case "set_focus_mode":
      return `专注模式→${target === true || target === "true" ? "开启" : "关闭"}`;
    default:
      return eventName[event.event_type] || event.event_type;
  }
}

// One line per planned event; six scene targets must stay distinguishable.
export function eventLabel(event: TemplateEvent): string {
  if (isParameterized(event)) return formatTarget(event);
  return eventName[event.event_type] || event.event_type;
}

export function eventListLabel(events: TemplateEvent[]): string {
  return events.map(eventLabel).join("、") || "—";
}

// Receipt wording: without independent observation, an App page receipt is
// explicitly NOT an independent confirmation.
export const resultName: Record<string, string> = {
  confirmed: "独立确认",
  app_ack_only: "App 页面回执（未独立验证）",
  ha_only: "仅独立观测确认",
  timeout: "超时",
  unexpected_state: "状态不符合",
  automation_error: "自动化错误",
  failed: "失败",
};
