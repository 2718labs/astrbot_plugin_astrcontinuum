from __future__ import annotations

import ast
import json
import re
from pathlib import Path


def _load_schema() -> dict[str, object]:
    project_root = Path(__file__).resolve().parents[1]
    return json.loads((project_root / "_conf_schema.json").read_text(encoding="utf-8"))


def _astrbot_dashboard_type_errors(
    schema: dict[str, object], config: dict[str, object]
) -> list[str]:
    """Mirror the relevant AstrBot 4.11/4.24/4.26 dashboard type contracts."""
    errors: list[str] = []
    for key, value in config.items():
        metadata = schema.get(key)
        if not isinstance(metadata, dict):
            continue
        field_type = metadata.get("type")
        if field_type == "file" and not isinstance(value, list):
            errors.append(f"{key}: expected list")
        elif field_type == "string" and not isinstance(value, str):
            errors.append(f"{key}: expected string")
    return errors


def test_default_config_with_selected_provider_is_saveable_by_astrbot_dashboard() -> None:
    schema = _load_schema()
    config = {
        key: value["default"]
        for key, value in schema.items()
        if isinstance(value, dict) and "default" in value
    }
    config["compaction_provider_id"] = "minimax-token-plan/MiniMax-M3"

    assert _astrbot_dashboard_type_errors(schema, config) == []


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
    assert source["default"] == "local"
    assert source["options"] == ["local", "file", "environment"]
    assert source["labels"] == [
        "自动管理（推荐）",
        "服务器密钥文件（高级）",
        "环境变量（高级）",
    ]
    assert source["obvious_hint"] is True
    assert "ASTRCONTINUUM_MASTER_KEY" in source["hint"]
    assert "自动生成" in source["hint"]
    assert "持久 data 卷" in source["hint"]
    assert "单独泄漏数据库文件" in source["hint"]
    assert "整个 data 卷泄漏" in source["hint"]
    assert "不受此模式保护" in source["hint"]
    assert "不要把密钥粘贴" in source["hint"]

    active_file = schema["encryption_key_file"]
    assert active_file["type"] == "string"
    assert active_file["default"] == ""
    # AstrBot 4.11+ evaluates flat field metadata conditions with strict equality.
    assert active_file["condition"] == {"encryption_key_source": "file"}
    assert active_file["obvious_hint"] is True
    assert "填写服务器内可读的当前密钥文件绝对路径" in active_file["hint"]
    assert "密钥内容" in active_file["hint"]

    previous_file = schema["encryption_previous_key_file"]
    assert previous_file["type"] == "string"
    assert previous_file["default"] == ""
    assert previous_file["condition"] == {"encryption_key_source": "file"}
    assert previous_file["obvious_hint"] is True
    assert "填写服务器内可读" in previous_file["hint"]
    assert "旧密钥文件绝对路径" in previous_file["hint"]
    assert "ASTRCONTINUUM_PREVIOUS_KEY" in previous_file["hint"]
    assert "数据保护为 ACTIVE" in previous_file["hint"]
    assert "不要在 WebUI、聊天、日志或命令参数中粘贴任何密钥" in previous_file["hint"]

    compaction_provider = schema["compaction_provider_id"]
    assert compaction_provider["type"] == "string"
    assert compaction_provider["_special"] == "select_provider"

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
