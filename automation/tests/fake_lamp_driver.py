"""A fake Appium driver that emulates the Mi Home desk lamp pages.

The fake mirrors the real page structure captured on 2026-09-24 (local
runs/sessions evidence): a device page with power button, brightness and
color-temperature slider cards, six scene buttons, and a settings page holding
the focus-mode switch. Slider swipes update the fake value using the inverse of
the adapter's documented value-to-position mapping.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

_TRUE = {"true", "1", "on"}

DEVICE_PAGE = "device"
SETTINGS_PAGE = "settings"

SCENES = ["电脑模式", "温馨模式", "休闲模式", "办公模式", "阅读模式", "娱乐模式"]
SCENE_PRESETS = {
    "电脑模式": (50, 2700),
    "温馨模式": (60, 3500),
    "休闲模式": (50, 4000),
    "办公模式": (100, 4500),
    "阅读模式": (100, 5000),
    "娱乐模式": (80, 3000),
}

BRIGHTNESS_CARD = {"x": 42, "y": 1658, "width": 1356, "height": 473}
COLOR_TEMPERATURE_CARD = {"x": 42, "y": 2173, "width": 1356, "height": 472}

BRIGHTNESS_RANGE = (1, 100)
COLOR_TEMPERATURE_RANGE = (2600, 5100)
# Calibrated slider track x-range mirrored from experiment/mi_desk_lamp_1s_advanced.yaml.
TRACK_START_PX = 142
TRACK_END_PX = 1179


def _bounds(x: int, y: int, width: int, height: int) -> str:
    return f"[{x},{y}][{x + width},{y + height}]"


def _rect_from_bounds(value: str) -> dict[str, int]:
    numbers = [int(part) for part in re.findall(r"-?\d+", value)]
    x1, y1, x2, y2 = numbers[:4]
    return {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}


def _device_page_xml(
    *,
    power_on: bool,
    brightness: int | None,
    color_temperature: int | None,
    marked_scene: str | None,
    with_sliders: bool = True,
) -> str:
    parts: list[str] = ['<node class="android.widget.TextView" text="米家台灯1S 增强版"/>']
    parts.append(f'<node class="android.widget.TextView" text="{"关灯" if power_on else "开灯"}"/>')
    brightness_text = f"{brightness}%" if brightness is not None else "--"
    color_temperature_text = f"{color_temperature}K" if color_temperature is not None else "--"
    if with_sliders:
        parts.append(
            f'<node class="android.view.ViewGroup" bounds="{_bounds(**BRIGHTNESS_CARD)}">'
            '<node class="android.widget.TextView" text="亮度"/>'
            f'<node class="android.widget.TextView" text="{brightness_text}"/>'
            f'<node class="android.view.View" bounds="{_bounds(420, 1740, 900, 60)}"/>'
            "</node>"
        )
        parts.append(
            f'<node class="android.view.ViewGroup" bounds="{_bounds(**COLOR_TEMPERATURE_CARD)}">'
            '<node class="android.widget.TextView" text="色温"/>'
            f'<node class="android.widget.TextView" text="{color_temperature_text}"/>'
            f'<node class="android.view.View" bounds="{_bounds(420, 2255, 900, 60)}"/>'
            "</node>"
        )
    else:
        parts.append('<node class="android.widget.TextView" text="亮度"/>')
        parts.append(f'<node class="android.widget.TextView" text="{brightness_text}"/>')
        parts.append('<node class="android.widget.TextView" text="色温"/>')
        parts.append(f'<node class="android.widget.TextView" text="{color_temperature_text}"/>')
    parts.append('<node class="android.widget.TextView" text="我的模式">')
    for index, scene in enumerate(SCENES):
        # The real Mi Home widget paints its selection but exposes no selected
        # or checked accessibility attribute, including on the active scene.
        selected = "false"
        parts.append(
            f'<node class="android.view.ViewGroup" clickable="true" selected="{selected}" '
            f'bounds="{_bounds(100 + index * 200, 2500, 180, 260)}">'
            f'<node class="android.widget.ImageView" selected="{selected}"/>'
            f'<node class="android.widget.TextView" text="{scene}" selected="{selected}"/>'
            "</node>"
        )
    parts.append("</node>")
    parts.append(
        '<node class="android.widget.ImageView" content-desc="更多设置, 点按两次即可激活" clickable="true"/>'
    )
    return "<hierarchy>" + "".join(parts) + "</hierarchy>"


def _settings_page_xml(*, focus_on: bool) -> str:
    return (
        "<hierarchy>"
        '<node class="android.widget.TextView" text="设置"/>'
        '<node class="android.widget.TextView" text="设备设置"/>'
        '<node class="android.view.ViewGroup" content-desc="延时关灯 定时关闭 选中开关后点按两次即可切换">'
        '<node class="android.view.View" text="延时关灯"/>'
        '<node class="android.view.View" checkable="true" checked="false"/>'
        "</node>"
        '<node class="android.view.ViewGroup" content-desc="专注模式 每工作45分钟，休息10分钟  选中开关后点按两次即可切换">'
        '<node class="android.view.View" text="专注模式"/>'
        f'<node class="android.view.View" checkable="true" checked="{"true" if focus_on else "false"}"/>'
        "</node>"
        "</hierarchy>"
    )


def _parse_ui_selector(locator: str) -> list[dict[str, Any]]:
    text = locator.strip()
    prefix = "new UiSelector()"
    if not text.startswith(prefix):
        raise ValueError(f"unsupported UiSelector chain: {locator}")
    index = len(prefix)
    chain: list[dict[str, Any]] = []
    while index < len(text):
        while index < len(text) and text[index] in " .":
            index += 1
        if index >= len(text):
            break
        method_match = re.match(r"\w+", text[index:])
        if method_match is None:
            raise ValueError(f"unsupported UiSelector chain: {locator}")
        method = method_match.group(0)
        index += method_match.end()
        if index >= len(text) or text[index] != "(":
            raise ValueError(f"expected ( after {method} in: {locator}")
        depth = 0
        start = index + 1
        argument: str | None = None
        for position in range(index, len(text)):
            if text[position] == "(":
                depth += 1
            elif text[position] == ")":
                depth -= 1
                if depth == 0:
                    argument = text[start:position]
                    index = position + 1
                    break
        if argument is None:
            raise ValueError(f"unbalanced parentheses in: {locator}")
        if method == "childSelector":
            chain.append({"method": "childSelector", "arg": None})
            inner = argument.strip()
            if not inner.startswith(prefix):
                inner = prefix + inner
            chain.extend(_parse_ui_selector(inner))
        else:
            # The real uiautomator2 driver parses the expression verbatim (no
            # Java-style unescaping), so the fake must keep backslashes as-is.
            chain.append({"method": method, "arg": argument.strip("\"'")})
    return chain


def _split_steps(expression: str) -> list[str]:
    steps: list[str] = []
    depth = 0
    current = ""
    for character in expression:
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
        if character == "/" and depth == 0:
            steps.append(current)
            current = ""
        else:
            current += character
    steps.append(current)
    return [step for step in steps if step]


def _predicate_matches(element: Any, predicate: str) -> bool:
    predicate = predicate.strip()
    negative = predicate.startswith("not(")
    if negative:
        predicate = predicate[4:-1].strip()
        return not _predicate_matches(element, predicate)
    return all(
        _single_predicate_matches(element, part.strip())
        for part in re.split(r"\s+and\s+", predicate)
        if part.strip()
    )


def _single_predicate_matches(element: Any, part: str) -> bool:
    attribute_match = re.fullmatch(r"@([\w-]+)='([^']*)'", part)
    if attribute_match:
        return element.get(attribute_match.group(1)) == attribute_match.group(2)
    presence_match = re.fullmatch(r"@([\w-]+)", part)
    if presence_match:
        return element.get(presence_match.group(1)) is not None
    descendant_match = re.fullmatch(r"\.//([\w.]+)\[(.*)\]", part, re.DOTALL)
    if descendant_match:
        tag, inner = descendant_match.groups()
        return any(
            child.get("class") == tag and _predicate_matches(child, inner)
            for child in element.iter()
        )
    raise ValueError(f"unsupported xpath predicate: {part}")


class FakeElement:
    def __init__(self, driver: FakeDriver, element: Any):
        self._driver = driver
        self._element = element

    @property
    def rect(self) -> dict[str, int]:
        return _rect_from_bounds(self._element.get("bounds") or "[0,0][0,0]")

    def get_attribute(self, name: str) -> str | None:
        mapping = {"contentDescription": "content-desc", "resourceId": "resource-id"}
        return self._element.get(mapping.get(name, name))

    def click(self) -> None:
        self._driver._click(self._element)

    def is_displayed(self) -> bool:
        return True


class FakeDriver:
    """In-memory driver with a device page and a settings page."""

    def __init__(
        self,
        *,
        power_on: bool = True,
        brightness: int | None = 60,
        color_temperature: int | None = 5078,
        marked_scene: str | None = None,
        focus_on: bool = False,
        with_sliders: bool = True,
        ignore_swipes: bool = False,
        mark_scene_on_click: bool = True,
        hide_device_marker: bool = False,
    ):
        self.power_on = power_on
        self.brightness_value = brightness
        self.color_temperature_value = color_temperature
        self.marked_scene = marked_scene
        self.focus_on = focus_on
        self.with_sliders = with_sliders
        self.ignore_swipes = ignore_swipes
        self.mark_scene_on_click = mark_scene_on_click
        self.hide_device_marker = hide_device_marker
        self.page = DEVICE_PAGE
        self.swipes: list[dict[str, int]] = []
        self.clicks: list[str] = []
        self.closed = False
        self.settings_page_available = True

    def render(self) -> str:
        if self.page == DEVICE_PAGE:
            if self.hide_device_marker:
                return "<hierarchy><node class='android.view.ViewGroup'/></hierarchy>"
            return _device_page_xml(
                power_on=self.power_on,
                brightness=self.brightness_value,
                color_temperature=self.color_temperature_value,
                marked_scene=self.marked_scene,
                with_sliders=self.with_sliders,
            )
        return _settings_page_xml(focus_on=self.focus_on)

    def _tree(self) -> Any:
        return ET.fromstring(self.render())

    # -- Appium driver surface -------------------------------------------
    def activate_app(self, _package: str) -> None:
        self.page = DEVICE_PAGE
        self.hide_device_marker = False

    def find_element(self, by: str, locator: str) -> FakeElement:
        matches = self.find_elements(by, locator)
        if not matches:
            raise ValueError(f"element not found: {by}={locator!r} on {self.page}")
        return matches[0]

    def find_elements(self, by: str, locator: str) -> list[FakeElement]:
        tree = self._tree()
        if by == "id":
            candidates = [element for element in tree.iter("node") if element.get("resource-id") == locator]
        elif by == "accessibility id":
            candidates = [element for element in tree.iter("node") if element.get("content-desc") == locator]
        elif by == "-android uiautomator":
            if not locator.startswith("new UiSelector()"):
                raise ValueError(f"unsupported uiautomator locator: {locator}")
            chain = _parse_ui_selector(locator)
            candidates = _find_by_chain(tree, chain)
        elif by == "xpath":
            candidates = _xpath(tree, locator)
        else:
            raise ValueError(f"unsupported strategy: {by}")
        return [FakeElement(self, element) for element in candidates]

    @property
    def page_source(self) -> str:
        return self.render()

    def save_screenshot(self, path: str) -> None:
        with open(path, "wb") as handle:
            handle.write(b"fake-png")

    def back(self) -> None:
        if self.page == SETTINGS_PAGE:
            self.page = DEVICE_PAGE

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, duration: int) -> None:
        self.swipes.append({
            "start_x": start_x, "start_y": start_y,
            "end_x": end_x, "end_y": end_y, "duration": duration,
        })
        if self.ignore_swipes or self.page != DEVICE_PAGE or not self.with_sliders:
            return
        for card, (low, high), setter in (
            (BRIGHTNESS_CARD, BRIGHTNESS_RANGE, lambda v: setattr(self, "brightness_value", v)),
            (COLOR_TEMPERATURE_CARD, COLOR_TEMPERATURE_RANGE, lambda v: setattr(self, "color_temperature_value", v)),
        ):
            center_y = card["y"] + card["height"] // 2
            if abs(start_y - center_y) > card["height"] // 2:
                continue
            x_start, x_end = TRACK_START_PX, TRACK_END_PX
            if not x_start <= end_x <= x_end:
                return
            fraction = (end_x - x_start) / (x_end - x_start)
            setter(max(low, min(high, round(low + fraction * (high - low)))))
            return

    def quit(self) -> None:
        self.closed = True

    # -- simulated app behaviour ------------------------------------------
    def _click(self, element: Any) -> None:
        text = element.get("text") or ""
        desc = element.get("content-desc") or ""
        if element.get("checkable") == "true":
            self.focus_on = element.get("checked") not in _TRUE
            self.clicks.append(f"focus_switch->{self.focus_on}")
            return
        if text in SCENES:
            if self.mark_scene_on_click:
                self.marked_scene = text
                self.brightness_value, self.color_temperature_value = SCENE_PRESETS[text]
            self.clicks.append(f"scene:{text}")
            return
        if text in {"开灯", "关灯"}:
            self.power_on = text == "开灯"
            self.clicks.append(f"power:{text}")
            return
        if "更多设置" in text or "更多设置" in desc:
            if self.settings_page_available:
                self.page = SETTINGS_PAGE
            self.clicks.append("focus_entry")
            return
        self.clicks.append(f"other:{text or desc}")


def _find_by_chain(tree: Any, chain: list[dict[str, Any]]) -> list[Any]:
    """Evaluate a UiSelector chain, returning the CHILD for childSelector patterns."""
    segments: list[list[dict[str, Any]]] = [[]]
    for step in chain:
        if step["method"] == "childSelector":
            segments.append([])
        else:
            segments[-1].append(step)
    results: list[Any] = []
    for element in tree.iter("node"):
        target: Any = element
        matched = True
        for index, segment in enumerate(segments):
            if index == 0:
                if not all(_method_matches(target, step) for step in segment):
                    matched = False
                    break
                continue
            child = next(
                (
                    descendant
                    for descendant in target.iter("node")
                    if descendant is not target
                    and all(_method_matches(descendant, step) for step in segment)
                ),
                None,
            )
            if child is None:
                matched = False
                break
            target = child
        if matched:
            results.append(target)
    return results


def _method_matches(element: Any, step: dict[str, Any]) -> bool:
    method, argument = step["method"], step["arg"]
    if method == "text":
        return element.get("text") == argument
    if method == "textMatches":
        return re.fullmatch(argument, element.get("text") or "") is not None
    if method == "descriptionContains":
        return argument in (element.get("content-desc") or "")
    if method == "checkable":
        return (element.get("checkable") or "false") == argument
    raise ValueError(f"unsupported UiSelector method: {method}")


def _xpath(tree: Any, expression: str) -> list[Any]:
    if not expression.startswith("//"):
        raise ValueError(f"unsupported xpath: {expression}")
    steps = _split_steps(expression[2:])
    step_specs: list[tuple[str, str | None]] = []
    for step in steps:
        match = re.fullmatch(r"([\w.]+)(?:\[(.*)\])?", step, re.DOTALL)
        if match is None:
            raise ValueError(f"unsupported xpath step: {step}")
        step_specs.append((match.group(1), match.group(2)))

    def class_is(element: Any, tag: str) -> bool:
        return (element.get("class") or "") == tag

    results = [
        element for element in tree.iter("node")
        if class_is(element, step_specs[0][0])
        and (step_specs[0][1] is None or _predicate_matches(element, step_specs[0][1]))
    ]
    for tag, predicate in step_specs[1:]:
        narrowed: list[Any] = []
        for element in results:
            for child in list(element):
                if class_is(child, tag) and (predicate is None or _predicate_matches(child, predicate)):
                    narrowed.append(child)
        results = narrowed
    return results
