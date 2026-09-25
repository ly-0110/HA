import assert from "node:assert/strict";
import { test } from "node:test";
import {
  eventLabel,
  eventListLabel,
  formatTarget,
  isParameterized,
  resultName,
} from "../src/eventLabels.ts";

test("two-state events keep their classic names", () => {
  assert.equal(eventLabel({ event_type: "turn_on" }), "开灯");
  assert.equal(eventLabel({ event_type: "turn_off" }), "关灯");
  assert.equal(eventLabel({ event_type: "play_music" }), "播放音乐");
  assert.equal(isParameterized({ event_type: "turn_on" }), false);
});

test("parameterized events display their numeric target and unit", () => {
  assert.equal(eventLabel({ event_type: "set_brightness", target: 30 }), "亮度→30%");
  assert.equal(eventLabel({ event_type: "set_brightness", target: 80 }), "亮度→80%");
  assert.equal(eventLabel({ event_type: "set_color_temperature", target: 3500 }), "色温→3500K");
  assert.equal(isParameterized({ event_type: "set_brightness", target: 30 }), true);
});

test("six scene targets are never collapsed into one label", () => {
  const scenes = ["电脑模式", "温馨模式", "休闲模式", "办公模式", "阅读模式", "娱乐模式"];
  const labels = new Set(scenes.map((scene) => eventLabel({ event_type: "select_scene", target: scene })));
  assert.equal(labels.size, 6);
  assert.equal(eventLabel({ event_type: "select_scene", target: "阅读模式" }), "情景→阅读模式");
});

test("focus switch targets read as on/off", () => {
  assert.equal(formatTarget({ event_type: "set_focus_mode", target: true }), "专注模式→开启");
  assert.equal(formatTarget({ event_type: "set_focus_mode", target: false }), "专注模式→关闭");
});

test("event list joins mixed events with distinct targets", () => {
  const label = eventListLabel([
    { event_type: "set_brightness", target: 30 },
    { event_type: "set_brightness", target: 80 },
    { event_type: "select_scene", target: "阅读模式" },
    { event_type: "turn_on" },
  ]);
  assert.equal(label, "亮度→30%、亮度→80%、情景→阅读模式、开灯");
});

test("app page receipts are explicitly not independent confirmations", () => {
  assert.equal(resultName["app_ack_only"], "App 页面回执（未独立验证）");
  assert.equal(resultName["confirmed"], "独立确认");
});
