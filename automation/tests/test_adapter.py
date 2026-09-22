from iot_exp.adapters.mi_home_lamp import _by


def test_selector_mapping():
    assert _by("resource-id", "com.example:id/power") == ("id", "com.example:id/power")
    by, locator = _by("text", "开灯")
    assert by == "-android uiautomator"
    assert "开灯" in locator
