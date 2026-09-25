from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from ..models import (
    AckEvidence,
    AppConfig,
    DeviceState,
    EventType,
    NumericParameterConfig,
    PageObservation,
    ParameterDimension,
    ParameterizedEventSpec,
    ParameterSettings,
    PhoneConfig,
    SceneParameterConfig,
    value_hits_target,
)
from .base import AdapterError


class SelectorError(AdapterError):
    pass


_TRUE_STATES = {"true", "1", "on"}
_BRIGHTNESS_PATTERN = re.compile(r"^\s*(\d+)\s*%\s*$")
_COLOR_TEMPERATURE_PATTERN = re.compile(r"^\s*(\d+)\s*[Kk]\s*$")


def _quote_escape(value: str) -> str:
    """Escape embedded double quotes; the driver parses the expression verbatim
    (no Java-style unescaping, so backslashes must be passed through untouched)."""
    return value.replace('"', '\\"')


def _by(strategy: str, value: str) -> tuple[str, str]:
    """Map config strategies to Selenium/Appium locator constants."""
    normalized = strategy.strip().lower().replace("_", " ")
    if normalized in {"id", "resource id", "resource-id"}:
        return "id", value
    if normalized in {"accessibility id", "accessibility", "content description", "content-description"}:
        return "accessibility id", value
    if normalized in {"description contains", "content desc contains", "content-desc contains"}:
        return "-android uiautomator", f'new UiSelector().descriptionContains("{_quote_escape(value)}")'
    if normalized in {"xpath"}:
        return "xpath", value
    if normalized in {"text", "visible text"}:
        return "-android uiautomator", f'new UiSelector().text("{_quote_escape(value)}")'
    if normalized in {"text matches", "text regex"}:
        return "-android uiautomator", f'new UiSelector().textMatches("{_quote_escape(value)}")'
    if normalized in {"android ui automator", "android uiautomator"}:
        return "-android uiautomator", value
    if normalized in {"class name", "class"}:
        return "class name", value
    raise SelectorError(f"unsupported selector strategy: {strategy}", code="selector_strategy")


class MiHomeDeskLamp1SAdapter:
    """米家台灯 1S adapter; UI details stay outside the orchestrator."""

    def __init__(
        self,
        app: AppConfig,
        phone: PhoneConfig,
        *,
        appium_url: str,
        system_port: int = 8200,
        screenshot_dir: Path | None = None,
        driver: Any | None = None,
        parameters: ParameterSettings | None = None,
    ):
        self.app = app
        self.phone = phone
        self.appium_url = appium_url
        self.system_port = system_port
        self.screenshot_dir = screenshot_dir
        self.driver = driver
        self.parameters = parameters or ParameterSettings()

    def connect(self) -> None:
        if self.driver is not None:
            return
        try:
            from appium import webdriver
            from appium.options.android import UiAutomator2Options
        except ImportError as exc:
            raise AdapterError(
                "Appium Python Client is not installed; run the environment setup first",
                code="missing_appium_client",
            ) from exc
        options = UiAutomator2Options()
        options.platform_name = "Android"
        options.automation_name = "UiAutomator2"
        options.udid = self.phone.udid
        options.app_package = self.app.package
        options.no_reset = True
        options.new_command_timeout = 120
        options.system_port = self.system_port
        self.driver = webdriver.Remote(self.appium_url, options=options)

    def launch_and_open_device(self) -> None:
        self.connect()
        assert self.driver is not None
        try:
            self.driver.activate_app(self.app.package)
            self._dismiss_known_popups()
            self._navigate_to_device_page()
            self._scroll_device_page_to_top()
        except Exception as exc:
            raise AdapterError(f"unable to open Mi Home device page: {exc}", code="open_device") from exc

    def _navigate_to_device_page(self) -> None:
        """Reach the device page from any app state: subpages need back, the list needs a click."""
        for _ in range(6):
            if self._find_optional("device_page_marker") is not None:
                return
            if self._find_optional("device_entry") is not None:
                self._find("device_entry").click()
                self._dismiss_known_popups()
                if self._wait_for_selector("device_page_marker", 6.0):
                    return
                assert self.driver is not None
                self.driver.back()
                time.sleep(0.6)
            else:
                assert self.driver is not None
                self.driver.back()
                time.sleep(0.8)
            self._dismiss_known_popups()
            if self._find_optional("device_page_marker") is None and self._find_optional("device_entry") is None:
                # Back may have left the app entirely; bring it to the foreground again.
                assert self.driver is not None
                self.driver.activate_app(self.app.package)
                time.sleep(1.5)
        raise AdapterError("device page marker never appeared", code="open_device")

    def read_state(self) -> DeviceState:
        self._dismiss_known_popups()
        state = self._read_power_state()
        if state is DeviceState.UNKNOWN and self._on_device_page_somewhere():
            # The device page may be scrolled; bring the power row back into view.
            self._scroll_device_page_to_top()
            state = self._read_power_state()
        return state

    def _on_device_page_somewhere(self) -> bool:
        """True when a device-page element is visible, even with the power row scrolled away."""
        if self._find_optional("device_page_marker") is not None:
            return True
        return (
            self._find_optional("brightness_value") is not None
            or self._find_optional("color_temperature_value") is not None
            or self._find_optional("scene_area_marker") is not None
        )

    def _read_power_state(self) -> DeviceState:
        on = self._find_optional("state_on")
        if on is not None and self._is_visible_or_checked(on):
            return DeviceState.ON
        off = self._find_optional("state_off")
        if off is not None and self._is_visible_or_checked(off):
            return DeviceState.OFF
        toggle = self._find_optional("power_toggle")
        if toggle is not None:
            checked = str(toggle.get_attribute("checked") or "").lower()
            if checked in {"true", "1", "on"}:
                return DeviceState.ON
            if checked in {"false", "0", "off"}:
                return DeviceState.OFF
        return DeviceState.UNKNOWN

    def _scroll_device_page_to_top(self) -> None:
        if (
            self._find_optional("state_on") is not None
            or self._find_optional("state_off") is not None
            or self._find_optional("power_toggle") is not None
        ):
            return
        assert self.driver is not None
        for _ in range(3):
            try:
                self.driver.find_element(
                    "-android uiautomator",
                    "new UiScrollable(new UiSelector().scrollable(true).instance(0)).scrollToBeginning(5)",
                )
            except Exception:  # noqa: BLE001 - fall back to a plain upward swipe.
                self.driver.swipe(720, 700, 720, 2600, 400)
            time.sleep(0.6)
            if (
                self._find_optional("state_on") is not None
                or self._find_optional("state_off") is not None
            ):
                return

    def perform_event(self, event_type: EventType) -> None:
        key = "turn_on" if event_type is EventType.TURN_ON else "turn_off"
        try:
            self._find(key).click()
        except Exception as exc:
            raise SelectorError(f"cannot click {key}: {exc}", code="event_control_not_found") from exc

    def wait_for_ack(self, expected_state: DeviceState, timeout_seconds: float) -> AckEvidence:
        deadline = time.monotonic() + timeout_seconds
        last_state = DeviceState.UNKNOWN
        while time.monotonic() < deadline:
            last_state = self.read_state()
            if last_state is expected_state:
                return AckEvidence(
                    acknowledged=True,
                    observed_state=last_state,
                    observed_at_unix_ns=time.time_ns(),
                    message="state selector matched",
                )
            time.sleep(0.25)
        return AckEvidence(
            acknowledged=False,
            observed_state=last_state,
            message=f"expected {expected_state.value}, observed {last_state.value}",
        )

    # --- parameterized (value / scene / switch) controls ---

    def read_parameter(
        self,
        dimension: ParameterDimension,
        *,
        evidence: tuple[Path, str] | None = None,
    ) -> PageObservation:
        handlers = {
            ParameterDimension.BRIGHTNESS: self._read_brightness,
            ParameterDimension.COLOR_TEMPERATURE: self._read_color_temperature,
            ParameterDimension.SCENE: self._read_scene,
            ParameterDimension.FOCUS_MODE: self._read_focus,
        }
        handler = handlers.get(dimension)
        if handler is None:
            raise AdapterError(f"unsupported parameter dimension: {dimension.value}", code="parameter_dimension")
        return handler(evidence=evidence)

    def perform_parameterized_event(self, spec: ParameterizedEventSpec) -> None:
        if spec.dimension is ParameterDimension.FOCUS_MODE:
            self._perform_focus(spec)
        elif spec.dimension is ParameterDimension.SCENE:
            try:
                self._find_scene_element(str(spec.target)).click()
            except AdapterError:
                raise
            except Exception as exc:
                raise SelectorError(
                    f"cannot click scene button {spec.target}: {exc}", code="event_control_not_found"
                ) from exc
        else:
            self._slide_numeric(spec)

    def _find_scene_element(self, scene_id: str) -> Any:
        """Locate a scene button, scrolling the mode area into view when needed."""
        element = self._find_optional(f"scene_{scene_id}_button")
        if element is None:
            assert self.driver is not None
            locator = (
                "new UiScrollable(new UiSelector().scrollable(true).instance(0))"
                f'.scrollIntoView(new UiSelector().text("{_quote_escape(scene_id)}"))'
            )
            try:
                element = self.driver.find_element("-android uiautomator", locator)
            except Exception as exc:
                raise SelectorError(
                    f"scene button not reachable even after scrolling: {scene_id}",
                    code=f"selector_scene_{scene_id}_button",
                ) from exc
        return element

    def wait_for_parameter(
        self,
        spec: ParameterizedEventSpec,
        timeout_seconds: float,
        *,
        evidence: tuple[Path, str] | None = None,
    ) -> PageObservation:
        deadline = time.monotonic() + timeout_seconds
        if spec.dimension is ParameterDimension.FOCUS_MODE:
            last = self._wait_focus(spec, deadline, evidence)
            return last
        last = self.read_parameter(spec.dimension)
        while time.monotonic() < deadline:
            if last.known and self._hits(spec, last.value):
                break
            time.sleep(0.4)
            last = self.read_parameter(spec.dimension)
        if evidence is not None:
            self._capture_evidence(*evidence)
        return last

    def _hits(self, spec: ParameterizedEventSpec, value: bool | int | str | None) -> bool:
        return value_hits_target(spec, value, tolerance=self._tolerance_for(spec.dimension))

    def _tolerance_for(self, dimension: ParameterDimension) -> int:
        declaration = self.parameters.for_dimension(dimension)
        if isinstance(declaration, NumericParameterConfig):
            return declaration.tolerance
        return 0

    def _numeric_config(self, dimension: ParameterDimension) -> NumericParameterConfig:
        declaration = self.parameters.for_dimension(dimension)
        if not isinstance(declaration, NumericParameterConfig):
            raise AdapterError(
                f"{dimension.value} parameters (range/unit/tolerance) are not configured; "
                "refusing to act on an unverified slider",
                code="parameter_unconfigured",
            )
        return declaration

    def _read_brightness(self, *, evidence: tuple[Path, str] | None = None) -> PageObservation:
        return self._read_numeric(
            ParameterDimension.BRIGHTNESS, "brightness_value", _BRIGHTNESS_PATTERN, evidence=evidence,
        )

    def _read_color_temperature(self, *, evidence: tuple[Path, str] | None = None) -> PageObservation:
        return self._read_numeric(
            ParameterDimension.COLOR_TEMPERATURE,
            "color_temperature_value",
            _COLOR_TEMPERATURE_PATTERN,
            evidence=evidence,
        )

    def _read_numeric(
        self,
        dimension: ParameterDimension,
        selector: str,
        pattern: re.Pattern[str],
        *,
        evidence: tuple[Path, str] | None = None,
    ) -> PageObservation:
        element = self._find_optional(selector)
        if element is None:
            observation = PageObservation(
                dimension=dimension,
                known=False,
                value=None,
                observed_at_unix_ns=time.time_ns(),
                detail=f"selector {selector} not found on page",
            )
        else:
            text = str(element.get_attribute("text") or "")
            match = pattern.match(text)
            if match is None:
                observation = PageObservation(
                    dimension=dimension,
                    known=False,
                    value=None,
                    unit=self._unit_or_none(dimension),
                    observed_at_unix_ns=time.time_ns(),
                    detail=f"unparseable label {text!r}",
                )
            else:
                observation = PageObservation(
                    dimension=dimension,
                    known=True,
                    value=int(match.group(1)),
                    unit=self._unit_or_none(dimension),
                    observed_at_unix_ns=time.time_ns(),
                )
        if evidence is not None:
            self._capture_evidence(*evidence)
        return observation

    def _unit_or_none(self, dimension: ParameterDimension) -> str | None:
        try:
            return self._numeric_config(dimension).unit
        except AdapterError:
            return None

    def _read_scene(self, *, evidence: tuple[Path, str] | None = None) -> PageObservation:
        declaration = self.parameters.for_dimension(ParameterDimension.SCENE)
        if declaration is None:
            raise AdapterError(
                "scene parameters are not configured; refusing to guess scene ids",
                code="parameter_unconfigured",
            )
        if isinstance(declaration, SceneParameterConfig) and declaration.presets:
            return self._read_scene_preset(declaration, evidence=evidence)
        marked: list[str] = []
        scene_ids = [str(scene) for scene in declaration.scenes]
        for scene_id in scene_ids:
            marker = self._find_optional(f"scene_{scene_id}_selected")
            if marker is not None and self._is_visible_or_checked(marker):
                marked.append(scene_id)
                continue
            button = self._find_optional(f"scene_{scene_id}_button")
            if button is not None and self._attribute_is_true(button, "selected"):
                marked.append(scene_id)
        if not marked and all(
            self._find_optional(f"scene_{scene_id}_button") is None for scene_id in scene_ids
        ):
            # The mode area is scrolled out of view; scroll it in once and re-check.
            try:
                self._find_scene_element(scene_ids[0])
            except AdapterError:
                pass
            for scene_id in scene_ids:
                button = self._find_optional(f"scene_{scene_id}_button")
                if button is not None and self._attribute_is_true(button, "selected"):
                    marked.append(scene_id)
        if len(marked) == 1:
            observation = PageObservation(
                dimension=ParameterDimension.SCENE,
                known=True,
                value=marked[0],
                observed_at_unix_ns=time.time_ns(),
            )
        elif len(marked) > 1:
            observation = PageObservation(
                dimension=ParameterDimension.SCENE,
                known=False,
                value=None,
                observed_at_unix_ns=time.time_ns(),
                detail=f"multiple scenes marked as current: {marked}",
            )
        else:
            observation = PageObservation(
                dimension=ParameterDimension.SCENE,
                known=True,
                value=None,
                observed_at_unix_ns=time.time_ns(),
                detail="no scene marked as current on the page",
            )
        if evidence is not None:
            self._capture_evidence(*evidence)
        return observation

    def _read_scene_preset(
        self,
        declaration: SceneParameterConfig,
        *,
        evidence: tuple[Path, str] | None,
    ) -> PageObservation:
        """Infer the active preset from two separate numeric controls on the App page."""
        self._scroll_device_page_to_top()
        brightness = self._read_brightness()
        temperature = self._read_color_temperature()
        readable = (
            brightness.known and isinstance(brightness.value, int)
            and not isinstance(brightness.value, bool)
            and temperature.known and isinstance(temperature.value, int)
            and not isinstance(temperature.value, bool)
        )
        readback_values = (
            {"brightness": brightness.value, "color_temperature": temperature.value}
            if readable else {}
        )
        matches: list[str] = []
        if readable:
            brightness_tolerance = self._numeric_config(ParameterDimension.BRIGHTNESS).tolerance
            temperature_tolerance = self._numeric_config(ParameterDimension.COLOR_TEMPERATURE).tolerance
            for scene_id, preset in declaration.presets.items():
                if (
                    abs(brightness.value - preset.brightness) <= brightness_tolerance
                    and abs(temperature.value - preset.color_temperature) <= temperature_tolerance
                ):
                    matches.append(scene_id)
        observation = PageObservation(
            dimension=ParameterDimension.SCENE,
            known=readable and len(matches) <= 1,
            value=matches[0] if len(matches) == 1 else None,
            observed_at_unix_ns=time.time_ns(),
            readback_values=readback_values,
            detail=(
                f"preset readback brightness={brightness.value!r}% "
                f"color_temperature={temperature.value!r}K matches={matches!r}"
            ),
        )
        if evidence is not None:
            self._capture_evidence(*evidence)
        return observation

    def _read_focus(self, *, evidence: tuple[Path, str] | None = None) -> PageObservation:
        try:
            if self._find_optional("focus_page_marker") is None:
                self._open_focus_page()
            observation = self._read_focus_switch_now()
            if evidence is not None:
                self._capture_evidence(*evidence)
        finally:
            self._return_to_device_page()
        return observation

    def _read_focus_switch_now(self) -> PageObservation:
        switch = self._find_optional("focus_switch")
        if switch is None:
            return PageObservation(
                dimension=ParameterDimension.FOCUS_MODE,
                known=False,
                value=None,
                observed_at_unix_ns=time.time_ns(),
                detail="focus switch not found on the settings page",
            )
        checked = self._attribute_is_true(switch, "checked")
        return PageObservation(
            dimension=ParameterDimension.FOCUS_MODE,
            known=True,
            value=checked,
            observed_at_unix_ns=time.time_ns(),
        )

    def _open_focus_page(self) -> None:
        if self._find_optional("focus_page_marker") is not None:
            return
        if self._find_optional("device_page_marker") is None:
            self.launch_and_open_device()
        entry = self._find("focus_entry")
        entry.click()
        if not self._wait_for_selector("focus_page_marker", 6.0):
            raise SelectorError(
                "focus settings page did not open after clicking focus_entry",
                code="focus_navigation",
            )

    def _wait_focus(
        self,
        spec: ParameterizedEventSpec,
        deadline: float,
        evidence: tuple[Path, str] | None,
    ) -> PageObservation:
        on_settings = self._find_optional("focus_page_marker") is not None
        if not on_settings:
            self._open_focus_page()
        last = self._read_focus_switch_now()
        while time.monotonic() < deadline:
            if last.known and last.value is spec.target:
                break
            time.sleep(0.4)
            last = self._read_focus_switch_now()
        if evidence is not None:
            self._capture_evidence(*evidence)
        self._return_to_device_page()
        return last

    def _perform_focus(self, spec: ParameterizedEventSpec) -> None:
        self._open_focus_page()
        switch = self._find("focus_switch")
        checked = self._attribute_is_true(switch, "checked")
        if checked is spec.target:
            raise AdapterError(
                f"focus mode already {'on' if spec.target else 'off'}",
                code="parameter_precondition",
            )
        switch.click()

    def _slide_numeric(self, spec: ParameterizedEventSpec) -> None:
        dimension = spec.dimension
        config = self._numeric_config(dimension)
        slider = self._find_innermost(f"{dimension.value}_slider")
        current = self.read_parameter(dimension)
        if not current.known or isinstance(current.value, bool) or not isinstance(current.value, int):
            raise AdapterError(
                f"cannot slide {dimension.value}: current value is unreadable; refusing a blind drag",
                code="parameter_unreadable",
            )
        rect = slider.rect
        center_y = int(rect["y"] + rect["height"] / 2)
        assert self.driver is not None
        target_value = int(spec.target)
        thumb_x = self._slider_x(rect, current.value, config)
        drag_x = self._slider_x(rect, target_value, config)
        previous_value = current.value
        for attempt in range(3):
            self.driver.swipe(thumb_x, center_y, drag_x, center_y, 600)
            time.sleep(1.5)
            observation = self.read_parameter(dimension)
            if not observation.known or isinstance(observation.value, bool) \
                    or not isinstance(observation.value, int):
                return  # unreadable after the drag; the orchestrator's wait classifies it
            if value_hits_target(spec, observation.value, tolerance=config.tolerance):
                return
            if attempt == 2 or observation.value == previous_value:
                return  # no usable response; the wait records the miss honestly
            # Secant correction from the measured response of the last two positions.
            if drag_x != thumb_x and observation.value != previous_value:
                slope = (observation.value - previous_value) / (drag_x - thumb_x)
                if slope != 0:
                    corrected = int(drag_x - (observation.value - target_value) / slope)
                    low_x, high_x = self._track_bounds(rect, config)
                    thumb_x = drag_x
                    drag_x = min(high_x, max(low_x, corrected))
                    previous_value = observation.value
                    continue
            break

    def _find_innermost(self, name: str) -> Any:
        """Pick the smallest-area match; container selectors also match outer wrappers."""
        if self.driver is None:
            raise AdapterError("Appium driver is not connected", code="driver_not_connected")
        best: Any | None = None
        best_area: int | None = None
        for selector in self.app.selectors.get(name, []):
            by, locator = _by(selector.strategy, selector.value)
            try:
                elements = self.driver.find_elements(by, locator)
            except Exception:  # noqa: BLE001, S112 - Appium exposes driver-specific exceptions.
                continue
            for element in elements:
                try:
                    rect = element.rect
                except Exception:  # noqa: BLE001, S112 - a missing rect is non-fatal.
                    continue
                area = int(rect["width"]) * int(rect["height"])
                if best_area is None or area < best_area:
                    best, best_area = element, area
        if best is None:
            raise SelectorError(f"no selector matched: {name}", code=f"selector_{name}")
        return best

    def _track_bounds(self, rect: dict[str, Any], config: NumericParameterConfig) -> tuple[int, int]:
        """Usable track x-range: calibrated pixels when present, else 3% card inset."""
        if config.track_start_px is not None and config.track_end_px is not None:
            return config.track_start_px + 2, config.track_end_px - 2
        inset = max(4, int(int(rect["width"]) * 0.03))
        return int(rect["x"]) + inset, int(rect["x"] + rect["width"]) - inset

    def _slider_x(self, rect: dict[str, Any], value: int, config: NumericParameterConfig) -> int:
        low, high = config.range
        if high <= low:
            raise AdapterError("invalid slider range declaration", code="parameter_range")
        start_x, end_x = self._track_bounds(rect, config)
        fraction = (value - low) / (high - low)
        fraction = min(1.0, max(0.0, fraction))
        return int(start_x + fraction * (end_x - start_x))

    def _wait_for_selector(self, name: str, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._find_optional(name) is not None:
                return True
            time.sleep(0.25)
        return False

    def _return_to_device_page(self) -> None:
        for _ in range(2):
            if self._find_optional("device_page_marker") is not None:
                self._scroll_device_page_to_top()
                return
            assert self.driver is not None
            try:
                self.driver.back()
            except Exception as exc:
                raise AdapterError(f"cannot press back: {exc}", code="navigation_recovery") from exc
            time.sleep(0.6)
            if self._on_device_page_somewhere():
                self._scroll_device_page_to_top()
                return
        try:
            self.launch_and_open_device()
        except Exception as exc:
            raise AdapterError(f"cannot return to device page: {exc}", code="navigation_recovery") from exc

    def _capture_evidence(self, destination: Path, tag: str) -> None:
        if self.driver is None:
            return
        destination.mkdir(parents=True, exist_ok=True)
        try:
            (destination / f"{tag}.xml").write_text(self.driver.page_source, encoding="utf-8")
            self.driver.save_screenshot(str(destination / f"{tag}.png"))
        except Exception as exc:
            raise AdapterError(f"evidence capture failed: {exc}", code="diagnostics") from exc

    def inspect_pages(self, destination: Path) -> dict[str, Any]:
        """Read-only page inspection: dump XML/screenshot per page and readable facts."""
        self.launch_and_open_device()
        summary: dict[str, Any] = {"pages": []}

        def record(page: str, extra: dict[str, Any] | None = None) -> None:
            entry: dict[str, Any] = {"page": page}
            if extra:
                entry.update(extra)
            summary["pages"].append(entry)

        self._capture_evidence(destination, "device_page")
        record("device_page", {"state": self.read_state().value})
        for dimension in (ParameterDimension.BRIGHTNESS, ParameterDimension.COLOR_TEMPERATURE):
            try:
                observation = self.read_parameter(dimension)
                record(dimension.value, {
                    "known": observation.known,
                    "value": observation.value,
                    "unit": observation.unit,
                    "detail": observation.detail,
                })
            except AdapterError as exc:
                record(dimension.value, {"error": exc.code, "detail": str(exc)})
        try:
            self._scroll_page_down()
        except Exception:  # noqa: BLE001,S110 - the scenes area may already be visible.
            pass
        self._capture_evidence(destination, "scenes_page")
        try:
            declaration = self.parameters.for_dimension(ParameterDimension.SCENE)
            scenes: dict[str, Any] = {}
            if declaration is not None:
                for scene_id in declaration.scenes:
                    button = self._find_optional(f"scene_{scene_id}_button")
                    scenes[scene_id] = "found" if button is not None else "missing"
            observation = self._read_scene()
            record("scenes", {
                "buttons": scenes,
                "known": observation.known,
                "value": observation.value,
                "detail": observation.detail,
            })
        except AdapterError as exc:
            record("scenes", {"error": exc.code, "detail": str(exc)})
        try:
            self._open_focus_page()
            self._capture_evidence(destination, "focus_page")
            observation = self._read_focus_switch_now()
            record("focus_page", {
                "known": observation.known,
                "value": observation.value,
                "detail": observation.detail,
            })
        except AdapterError as exc:
            record("focus_page", {"error": exc.code, "detail": str(exc)})
        finally:
            self._return_to_device_page()
        return summary

    def _scroll_page_down(self) -> None:
        assert self.driver is not None
        self.driver.swipe(720, 2400, 720, 1100, 400)
        time.sleep(0.8)

    @staticmethod
    def _attribute_is_true(element: Any, attribute: str) -> bool:
        return str(element.get_attribute(attribute) or "").lower() in _TRUE_STATES

    def recover_navigation(self) -> None:
        try:
            self.launch_and_open_device()
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(f"navigation recovery failed: {exc}", code="navigation_recovery") from exc

    def capture_diagnostics(self, destination: Path, event_id: str) -> None:
        if self.driver is None:
            return
        destination.mkdir(parents=True, exist_ok=True)
        try:
            (destination / f"{event_id}.xml").write_text(self.driver.page_source, encoding="utf-8")
            self.driver.save_screenshot(str(destination / f"{event_id}.png"))
        except Exception as exc:
            raise AdapterError(f"diagnostic capture failed: {exc}", code="diagnostics") from exc

    def close(self) -> None:
        if self.driver is not None:
            try:
                self.driver.quit()
            finally:
                self.driver = None

    def _find(self, name: str) -> Any:
        element = self._find_optional(name)
        if element is None:
            raise SelectorError(f"no selector matched: {name}", code=f"selector_{name}")
        return element

    def _find_optional(self, name: str) -> Any | None:
        if self.driver is None:
            raise AdapterError("Appium driver is not connected", code="driver_not_connected")
        selectors = self.app.selectors.get(name, [])
        for selector in selectors:
            by, locator = _by(selector.strategy, selector.value)
            try:
                return self.driver.find_element(by, locator)
            except Exception:  # noqa: BLE001, S112 - Appium exposes driver-specific exceptions.
                continue
        return None

    @staticmethod
    def _is_visible_or_checked(element: Any) -> bool:
        try:
            if element.is_displayed():
                return True
        except Exception:  # noqa: BLE001, S110 - a missing visibility attribute is non-fatal.
            pass
        checked = str(element.get_attribute("checked") or "").lower()
        return checked in {"true", "1", "on"}

    def _dismiss_known_popups(self) -> None:
        for name in ("dismiss_popup", "close_update_popup", "allow_button"):
            element = self._find_optional(name)
            if element is not None:
                try:
                    element.click()
                except Exception:  # noqa: BLE001, S112 - popup may disappear between lookup and click.
                    continue
