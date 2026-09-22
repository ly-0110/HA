from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..models import AckEvidence, AppConfig, DeviceState, EventType, PhoneConfig
from .base import AdapterError


class SelectorError(AdapterError):
    pass


def _by(strategy: str, value: str) -> tuple[str, str]:
    """Map config strategies to Selenium/Appium locator constants."""
    normalized = strategy.strip().lower().replace("_", " ")
    if normalized in {"id", "resource id", "resource-id"}:
        return "id", value
    if normalized in {"accessibility id", "accessibility", "content description", "content-description"}:
        return "accessibility id", value
    if normalized in {"xpath"}:
        return "xpath", value
    if normalized in {"text", "visible text"}:
        escaped = value.replace('\\', '\\\\').replace('"', '\\"')
        return "-android uiautomator", f'new UiSelector().text("{escaped}")'
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
    ):
        self.app = app
        self.phone = phone
        self.appium_url = appium_url
        self.system_port = system_port
        self.screenshot_dir = screenshot_dir
        self.driver = driver

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
            if self._find_optional("device_page_marker") is not None:
                return
            entry = self._find("device_entry")
            entry.click()
            self._dismiss_known_popups()
            self._find("device_page_marker")
        except Exception as exc:
            raise AdapterError(f"unable to open Mi Home device page: {exc}", code="open_device") from exc

    def read_state(self) -> DeviceState:
        self._dismiss_known_popups()
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
