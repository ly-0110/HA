import importlib.util
from collections import Counter
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

import pytest

from iot_exp.journal import JsonlJournal
from iot_exp.models import ParameterDimension, ParameterizedEventSpec

module_spec = importlib.util.spec_from_file_location(
    "capture_lamp_pu", Path(__file__).parents[1] / "capture_lamp_pu.py",
)
campaign = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(campaign)


@pytest.mark.parametrize("group", campaign.GROUPS)
def test_rounds_are_counted_per_event_type_with_u_between_blocks(group):
    plan = campaign.build_plan(group)
    targets = [operation for operation in plan if operation["role"] == "target"]
    assert len(targets) == 120
    assert len(plan) == 142
    expected_each = 20 if group == "scene" else 60
    assert set(Counter(str(operation["target"]) for operation in targets).values()) == {expected_each}
    # Every block includes ten target operations, then off/on U operations.
    for block in range(11):
        start = block * 12
        assert all(operation["role"] == "target" for operation in plan[start:start + 10])
        assert [operation["event_type"] for operation in plan[start + 10:start + 12]] == ["turn_off", "turn_on"]
    assert all(operation["role"] == "target" for operation in plan[-10:])
    assert all(first["target"] != second["target"] for first, second in pairwise(targets))


def test_numeric_miss_still_dispatches_only_one_swipe(tmp_path):
    gestures = []
    runner = object.__new__(campaign.Campaign)
    runner.adapter = SimpleNamespace(
        _numeric_config=lambda dimension: SimpleNamespace(),
        _find_innermost=lambda selector: SimpleNamespace(rect={"x": 0, "y": 100, "width": 100, "height": 20}),
        _slider_x=lambda rect, value, config: value,
        driver=SimpleNamespace(swipe=lambda *args: gestures.append(args)),
    )
    operation = {"event_type": "set_brightness", "target": 80, "role": "target"}
    row = {"event_id": "test_event", "role": "target", "dispatched": False}
    runner.dispatch(operation, {"known": True, "value": 30}, row, JsonlJournal(tmp_path / "journal.jsonl"))
    assert gestures == [(30, 110, 80, 110, 600)]
    assert row["dispatched"] is True
    assert row["t_cmd_before_ns"] <= row["t_cmd_after_ns"]


def test_unreadable_slider_does_not_count_a_command(tmp_path):
    runner = object.__new__(campaign.Campaign)
    runner.adapter = SimpleNamespace()
    spec = ParameterizedEventSpec(event_type="set_brightness", target=80)
    assert spec.dimension is ParameterDimension.BRIGHTNESS
    row = {"event_id": "not_dispatched", "role": "target", "dispatched": False}
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    with pytest.raises(RuntimeError, match="unreadable"):
        runner.dispatch({"event_type": "set_brightness", "target": 80}, {"known": False}, row, journal)
    assert row["dispatched"] is False
    assert journal.read() == []
