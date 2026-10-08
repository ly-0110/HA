import shutil
from pathlib import Path

import pytest

from iot_exp.config import configuration_provenance
from iot_exp.paths import PathLayout, current_layout


def test_workspace_preserves_user_configuration_and_separates_data(tmp_path):
    resources = tmp_path / "安装资源"
    resources.mkdir()
    shutil.copytree(Path(__file__).parents[1] / "experiment", resources / "experiment")
    shutil.copytree(Path(__file__).parents[1] / "runtime", resources / "runtime")
    layout = PathLayout.workspace(resources, tmp_path / "实验 工作区", tmp_path / "用户状态")
    layout.initialize()
    config = layout.config_root / "experiment/mi_desk_lamp_1s.yaml"
    config.write_text("用户修改", encoding="utf-8")
    layout.initialize()
    assert config.read_text(encoding="utf-8") == "用户修改"
    assert layout.database.parent == layout.state_root
    assert layout.output_base == layout.config_root.parent
    assert not layout.output_root.is_relative_to(resources)
    assert configuration_provenance(config, layout)["user_override"] is True


def test_child_context_survives_an_arbitrary_cwd(tmp_path, monkeypatch):
    layout = PathLayout.workspace(tmp_path / "安装", tmp_path / "数据", tmp_path / "状态")
    monkeypatch.setenv("IOT_EXP_CONTEXT", layout.child_environment()["IOT_EXP_CONTEXT"])
    monkeypatch.chdir(tmp_path)
    assert current_layout() == layout


def test_workspace_cannot_write_installation(tmp_path):
    layout = PathLayout.workspace(tmp_path, tmp_path / "data", tmp_path / "state")
    with pytest.raises(ValueError, match="安装"):
        layout.initialize()
