from __future__ import annotations

import ast
import json
import re
from pathlib import Path


def _load_schema() -> dict[str, object]:
    project_root = Path(__file__).resolve().parents[1]
    return json.loads((project_root / "_conf_schema.json").read_text(encoding="utf-8"))


def test_context_engine_mode_is_the_only_exposed_engine_control() -> None:
    schema = _load_schema()

    assert [key for key in schema if key.startswith("context_engine_")] == ["context_engine_mode"]
    mode = schema["context_engine_mode"]
    assert isinstance(mode, dict)
    assert mode["type"] == "string"
    assert mode["default"] == "active"
    assert mode["options"] == ["active", "shadow", "off"]
    assert mode["labels"] == [
        "主动（校验通过才使用）",
        "影子（只测量，不替换）",
        "关闭（仅确定性路径）",
    ]
    assert mode["obvious_hint"] is True
    assert "完整校验" in mode["hint"]
    assert "确定性回退" in mode["hint"]
    assert "/context_status" in mode["hint"]
    assert "/context_inspect" in mode["hint"]


def test_storage_key_configuration_is_explicit_and_never_accepts_key_text() -> None:
    schema = _load_schema()

    source = schema["encryption_key_source"]
    assert source["type"] == "string"
    assert source["default"] == "environment"
    assert source["options"] == ["environment", "file", "local"]
    assert source["labels"] == [
        "环境变量（推荐）",
        "外部密钥文件",
        "本机便捷模式（较弱）",
    ]
    assert source["obvious_hint"] is True
    assert "ASTRCONTINUUM_MASTER_KEY" in source["hint"]
    assert "不要把密钥粘贴" in source["hint"]

    active_file = schema["encryption_key_file"]
    assert active_file["type"] == "file"
    assert active_file["default"] == ""
    assert active_file["obvious_hint"] is True
    assert "密钥内容" in active_file["hint"]

    previous_file = schema["encryption_previous_key_file"]
    assert previous_file["type"] == "file"
    assert previous_file["default"] == ""
    assert previous_file["obvious_hint"] is True
    assert "ASTRCONTINUUM_PREVIOUS_KEY" in previous_file["hint"]
    assert "数据保护为 ACTIVE" in previous_file["hint"]
    assert "不要在 WebUI、聊天、日志或命令参数中粘贴任何密钥" in previous_file["hint"]

    forbidden_fields = {
        "master_key",
        "previous_key",
        "encryption_key",
        "encryption_previous_key",
    }
    assert forbidden_fields.isdisjoint(schema)


def test_release_version_is_consistent_across_plugin_surfaces() -> None:
    project_root = Path(__file__).resolve().parents[1]
    pyproject = (project_root / "pyproject.toml").read_text(encoding="utf-8")
    project_section = re.search(
        r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)",
        pyproject,
    )
    assert project_section is not None
    project_match = re.search(
        r'(?m)^version\s*=\s*"([^"]+)"\s*$',
        project_section.group(1),
    )
    assert project_match is not None

    metadata = (project_root / "metadata.yaml").read_text(encoding="utf-8")
    metadata_match = re.search(r"(?m)^version:\s*v?([^\s#]+)\s*$", metadata)
    assert metadata_match is not None

    main_tree = ast.parse((project_root / "main.py").read_text(encoding="utf-8"))
    plugin_class = next(
        node
        for node in main_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AstrContinuumPlugin"
    )
    register_call = next(
        decorator
        for decorator in plugin_class.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name)
        and decorator.func.id == "register"
    )
    registered_version = register_call.args[3]
    assert isinstance(registered_version, ast.Constant)
    assert isinstance(registered_version.value, str)

    assert {
        project_match.group(1),
        metadata_match.group(1),
        registered_version.value,
    } == {"0.2.0"}
