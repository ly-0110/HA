import { eventLabel, eventName, type TemplateEvent } from "./eventLabels";
import { copyEvents, replaceTargets } from "./eventPlan";
import type { NumericParameter, Template } from "./types";

const stateName: Record<string, string> = { on: "开启", off: "关闭", playing: "播放中", paused: "已暂停" };

export function EventEditor({ template, events, onChange }: {
  template: Template;
  events: TemplateEvent[];
  onChange: (events: TemplateEvent[]) => void;
}) {
  const types = [...new Set(template.events.map(event => event.event_type))];
  const update = (type: string, targets: (string | number | boolean)[]) => onChange(replaceTargets(events, template.events, type, targets));
  const toggleGroup = (type: string) => {
    const current = events.filter(event => event.event_type === type);
    onChange(current.length ? events.filter(event => event.event_type !== type)
      : [...events, ...copyEvents(template.events.filter(event => event.event_type === type))]);
  };
  const toggleTarget = (type: string, value: string | boolean) => {
    const targets = events.filter(event => event.event_type === type).map(event => event.target as string | boolean);
    update(type, targets.includes(value) ? targets.filter(target => target !== value) : [...targets, value]);
  };

  return <div className="event-editor">{types.map(type => {
    const current = events.filter(event => event.event_type === type);
    const numeric = type === "set_brightness" ? template.parameters?.brightness
      : type === "set_color_temperature" ? template.parameters?.color_temperature : null;
    const candidates = type === "select_scene" ? template.parameters?.scene?.scenes ?? []
      : type === "set_focus_mode" ? [true, false] : null;
    const addNumber = (config: NumericParameter) => {
      const occupied = new Set(current.map(event => event.target));
      const defaults = template.events.filter(event => event.event_type === type).map(event => event.target as number);
      const choices = [...defaults, config.range[0], config.range[1]];
      let value = choices.find(target => !occupied.has(target));
      if (value === undefined) {
        for (let target = config.range[0]; target <= config.range[1]; target++) {
          if (!occupied.has(target)) { value = target; break; }
        }
      }
      if (value !== undefined) update(type, [...current.map(event => event.target as number), value]);
    };
    return <div className={`event-group ${current.length ? "enabled" : ""}`} key={type}>
      <div className="event-group-head">
        <label className="check-label"><input type="checkbox" checked={current.length > 0} onChange={() => toggleGroup(type)} /><b>{eventName[type] || type}</b></label>
        {numeric && <span className="field-hint">{numeric.range[0]}–{numeric.range[1]} {numeric.unit}</span>}
        {!numeric && !candidates && <span className="field-hint">{template.events.filter(event => event.event_type === type).map(event => `${stateName[event.required_state || ""] || event.required_state} → ${stateName[event.expected_state || ""] || event.expected_state}`).join("、")}</span>}
      </div>
      {numeric && current.length > 0 && <div className="numeric-targets">
        {current.map((event, index) => <div className="target-input" key={index}>
          <input aria-label={`${eventName[type]}目标 ${index + 1}`} type="number" min={numeric.range[0]} max={numeric.range[1]} step="1" value={typeof event.target === "number" && Number.isFinite(event.target) ? event.target : ""}
            onChange={e => update(type, current.map((item, i) => i === index ? (e.target.value ? Number(e.target.value) : Number.NaN) : item.target as number))} />
          <span>{numeric.unit}</span><button type="button" className="icon-button" aria-label={`移除${eventName[type]}目标 ${index + 1}`} onClick={() => update(type, current.filter((_, i) => i !== index).map(item => item.target as number))}>×</button>
        </div>)}
        <button type="button" className="link small" onClick={() => addNumber(numeric)} disabled={current.length >= numeric.range[1] - numeric.range[0] + 1}>＋ 添加目标</button>
      </div>}
      {candidates && <div className="target-choices">{candidates.map(value => {
        const selected = current.some(event => event.target === value);
        return <label key={String(value)} className={`target-choice ${selected ? "selected" : ""}`}>
          <input type="checkbox" checked={selected} onChange={() => toggleTarget(type, value)} />
          <span>{typeof value === "boolean" ? value ? "开启" : "关闭" : value}</span>
        </label>;
      })}</div>}
      {!numeric && !candidates && current.length > 0 && <span className="sr-only">{current.map(eventLabel).join("、")}</span>}
    </div>;
  })}</div>;
}
