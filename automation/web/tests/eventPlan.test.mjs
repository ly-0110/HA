import assert from "node:assert/strict";
import { test } from "node:test";
import { configuredValue, copyEvents, eventIdentity, replaceTargets, validateEvents } from "../src/eventPlan.ts";

const parameters = {
  brightness: { range: [1, 100], unit: "%", tolerance: 3 },
  color_temperature: { range: [2600, 5100], unit: "K", tolerance: 100 },
  scene: { scenes: ["电脑模式", "阅读模式", "温馨模式"] },
};
const template = [
  { event_type: "turn_on", required_state: "off", expected_state: "on" },
  { event_type: "set_brightness", target: 30, required_state: "on" },
  { event_type: "set_brightness", target: 80, required_state: "on" },
  { event_type: "select_scene", target: "电脑模式", required_state: "on" },
  { event_type: "select_scene", target: "阅读模式", required_state: "on" },
  { event_type: "set_focus_mode", target: false, required_state: null },
];

test("editing numeric targets preserves other event identities and power preconditions", () => {
  const edited = replaceTargets(copyEvents(template), template, "set_brightness", [20, 90]);
  assert.deepEqual(edited.filter(event => event.event_type === "set_brightness"), [
    { event_type: "set_brightness", target: 20, required_state: "on" },
    { event_type: "set_brightness", target: 90, required_state: "on" },
  ]);
  assert.deepEqual(edited.filter(event => event.event_type !== "set_brightness"), template.filter(event => event.event_type !== "set_brightness"));
  assert.equal(template[1].target, 30);
});

test("scene selections retain distinct target identities rather than type-only deduplication", () => {
  const edited = replaceTargets(template, template, "select_scene", parameters.scene.scenes);
  assert.equal(new Set(edited.filter(event => event.event_type === "select_scene").map(eventIdentity)).size, 3);
  assert.equal(validateEvents(edited, parameters), null);
  assert.match(validateEvents([...edited, { event_type: "select_scene", target: "电脑模式" }], parameters), /目标重复/);
});

test("focus target remains boolean and has no power precondition", () => {
  const edited = replaceTargets(template, template, "set_focus_mode", [false, true]);
  assert.deepEqual(edited.filter(event => event.event_type === "set_focus_mode"), [
    { event_type: "set_focus_mode", target: false, required_state: null },
    { event_type: "set_focus_mode", target: true, required_state: null },
  ]);
  assert.match(validateEvents([{ event_type: "set_focus_mode", target: "false" }], parameters), /开启或关闭/);
});

test("invalid numeric and undeclared scene targets cannot be submitted", () => {
  for (const target of [0, 101, 2.5, Number.NaN, "30", false]) {
    assert.match(validateEvents([{ event_type: "set_brightness", target }], parameters), /范围内的整数/);
  }
  assert.equal(validateEvents([{ event_type: "set_color_temperature", target: 5100 }], parameters), null);
  assert.match(validateEvents([{ event_type: "select_scene", target: "未知模式" }], parameters), /模板中的情景/);
  assert.match(validateEvents([], parameters), /至少选择/);
});

test("disabling one dimension does not remove a two-state transition", () => {
  const edited = replaceTargets(template, template, "select_scene", []);
  assert.ok(edited.some(event => event.event_type === "turn_on" && event.required_state === "off" && event.expected_state === "on"));
  assert.equal(edited.filter(event => event.event_type === "select_scene").length, 0);
});

test("template placeholders are not presented as valid formal configuration", () => {
  assert.equal(configuredValue("host REPLACE_WITH_TARGET_DEVICE_IP"), null);
  assert.equal(configuredValue("REPLACE_WITH_ADB_UDID"), null);
  assert.equal(configuredValue("  host 192.168.1.20  "), "host 192.168.1.20");
});
