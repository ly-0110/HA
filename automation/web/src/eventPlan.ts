import { eventName, isParameterized, type TemplateEvent } from "./eventLabels.ts";
import type { Parameters } from "./types";

export function copyEvents(events: TemplateEvent[]): TemplateEvent[] {
  return events.map(event => ({ ...event }));
}

export function eventIdentity(event: TemplateEvent): string {
  return JSON.stringify([event.event_type, event.target ?? null]);
}

// Replace one dimension without collapsing its distinct targets or changing any
// other event's state transition. State preconditions come from the template.
export function replaceTargets(
  events: TemplateEvent[],
  templateEvents: TemplateEvent[],
  eventType: string,
  targets: (string | number | boolean)[],
): TemplateEvent[] {
  const base = templateEvents.find(event => event.event_type === eventType);
  if (!base) return events;
  const replacement = targets.map(target => ({ ...base, target }));
  const first = events.findIndex(event => event.event_type === eventType);
  const remaining = events.filter(event => event.event_type !== eventType);
  remaining.splice(first < 0 ? remaining.length : first, 0, ...replacement);
  return remaining;
}

export function validateEvents(events: TemplateEvent[], parameters?: Parameters | null): string | null {
  if (!events.length) return "请至少选择一个事件";
  const seen = new Set<string>();
  for (const event of events) {
    const identity = eventIdentity(event);
    if (seen.has(identity)) return `${eventName[event.event_type] || event.event_type}的目标重复`;
    seen.add(identity);
    if (!isParameterized(event)) continue;
    if (event.event_type === "set_brightness" || event.event_type === "set_color_temperature") {
      const config = event.event_type === "set_brightness" ? parameters?.brightness : parameters?.color_temperature;
      if (!config || typeof event.target !== "number" || !Number.isInteger(event.target)
        || event.target < config.range[0] || event.target > config.range[1]) {
        return `${eventName[event.event_type]}目标必须是范围内的整数`;
      }
    } else if (event.event_type === "select_scene") {
      if (typeof event.target !== "string" || !parameters?.scene?.scenes.includes(event.target)) return "请选择模板中的情景模式";
    } else if (event.event_type === "set_focus_mode" && typeof event.target !== "boolean") {
      return "专注模式目标必须为开启或关闭";
    }
  }
  return null;
}

export function templateTitle(template: { display_name: string; events: TemplateEvent[] }): string {
  return `${template.display_name} · ${template.events.some(isParameterized) ? "参数与模式" : "开关与状态"}`;
}

export function configuredValue(value?: string | null): string | null {
  const cleaned = value?.trim();
  return !cleaned || /REPLACE|PLACEHOLDER/i.test(cleaned) ? null : cleaned;
}
