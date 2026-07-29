from __future__ import annotations

import ast
import json
import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
import yaml

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_PACKAGE_VERSION = "0.2.1"
EXPECTED_RELEASE_VERSION = f"v{EXPECTED_PACKAGE_VERSION}"
README_PATHS = (ROOT / "README.md", ROOT / "README.zh-CN.md")
DOCUMENTATION_PATHS = (
    *README_PATHS,
    ROOT / "CONTRIBUTING.md",
    ROOT / "SECURITY.md",
    ROOT / "docs/ARCHITECTURE.md",
    ROOT / "docs/ARCHITECTURE.zh-CN.md",
    ROOT / "docs/ASTRBOT_INTEGRATION.md",
    ROOT / "docs/ASTRBOT_INTEGRATION.zh-CN.md",
    ROOT / "docs/DATABASE_SCHEMA.md",
    ROOT / "docs/DATA_FLOW.md",
    ROOT / "docs/TEST_MATRIX.md",
    ROOT / "docs/ROADMAP.md",
    ROOT / "docs/ROADMAP.zh-CN.md",
)


def _read_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as source:
        return tomllib.load(source)


def _register_version() -> str:
    module = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    plugin_class = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "AstrContinuumPlugin"
    )
    register = next(
        decorator
        for decorator in plugin_class.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name)
        and decorator.func.id == "register"
    )
    assert len(register.args) >= 4
    version = ast.literal_eval(register.args[3])
    assert isinstance(version, str)
    return version


def _root_lock_package() -> dict[str, object]:
    packages = _read_toml(ROOT / "uv.lock")["package"]
    assert isinstance(packages, list)
    return next(
        package
        for package in packages
        if isinstance(package, dict) and package.get("name") == "astrbot-plugin-astrcontinuum"
    )


def test_v021_version_is_consistent_across_release_surfaces() -> None:
    metadata = yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))
    project = _read_toml(ROOT / "pyproject.toml")["project"]
    assert isinstance(project, dict)

    assert _register_version() == EXPECTED_PACKAGE_VERSION
    assert metadata["version"] == EXPECTED_RELEASE_VERSION
    assert project["version"] == EXPECTED_PACKAGE_VERSION
    assert _root_lock_package()["version"] == EXPECTED_PACKAGE_VERSION

    for readme_path in README_PATHS:
        readme = readme_path.read_text(encoding="utf-8")
        assert f"version-{EXPECTED_RELEASE_VERSION}-blue" in readme

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    english_heading = re.search(r"^## (v\d+\.\d+\.\d+)\b", changelog, re.MULTILINE)
    chinese_heading = re.search(r"^### (v\d+\.\d+\.\d+)\b", changelog, re.MULTILINE)
    assert english_heading is not None
    assert chinese_heading is not None
    assert english_heading.group(1) == EXPECTED_RELEASE_VERSION
    assert chinese_heading.group(1) == EXPECTED_RELEASE_VERSION


def test_readme_math_uses_balanced_github_fences() -> None:
    for readme_path in README_PATHS:
        text = readme_path.read_text(encoding="utf-8")
        assert not any(line.strip() == "$$" for line in text.splitlines())
        blocks = re.findall(r"^```math\s*\n(.*?)^```\s*$", text, re.MULTILINE | re.DOTALL)
        assert len(blocks) == 23
        for block in blocks:
            assert "$" not in block
            assert not any(line.rstrip().endswith("\\\\") for line in block.splitlines())
        assert any("B_{\\mathrm{input}}" in block for block in blocks)
        assert any("T_p(x)" in block and "10000" in block for block in blocks)


def test_readme_math_uses_github_compatible_operators() -> None:
    for readme_path in README_PATHS:
        text = readme_path.read_text(encoding="utf-8")
        assert "\\operatorname" not in text
        assert "\\mathrm{diag}" in text
        assert "\\mathrm{rank}" in text
        assert "\\mathrm{csupp}" in text
        assert "\\mathop{\\mathrm{arg\\,min}}" in text


def test_key_and_automatic_context_configuration_contract_is_documented() -> None:
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    assert schema["encryption_key_source"]["default"] == "local"
    assert schema["encryption_key_file"]["type"] == "string"
    assert schema["encryption_key_file"]["condition"] == {"encryption_key_source": "file"}
    assert schema["encryption_previous_key_file"]["type"] == "string"
    assert schema["encryption_previous_key_file"]["condition"] == {"encryption_key_source": "file"}
    assert schema["model_context_limit"]["default"] == 0

    english, chinese = (path.read_text(encoding="utf-8") for path in README_PATHS)
    assert "| `encryption_key_source` | `string` | `local` |" in english
    assert "| `encryption_key_file` | `string` | empty |" in english
    assert "shown only in `file` mode" in english
    assert "| `model_context_limit` | `int` | `0` |" in english
    assert "| `encryption_key_source` | `string` | `local` |" in chinese
    assert "| `encryption_key_file` | `string` | 空 |" in chinese
    assert "仅 `file` 模式显示" in chinese
    assert "| `model_context_limit` | `int` | `0` |" in chinese

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    v021 = changelog.split("## v0.2.1", 1)[1].split("## v0.2.0", 1)[0]
    assert "defaults to automatic local key creation and reuse" in v021
    assert "默认自动创建并复用本地密钥" in changelog


def test_contributor_sections_name_only_ayleovelle() -> None:
    headings = {
        ROOT / "README.md": ("## Contributors", "## Acknowledgements"),
        ROOT / "README.zh-CN.md": ("## 贡献者", "## 致谢"),
    }
    for path, (start_heading, end_heading) in headings.items():
        text = path.read_text(encoding="utf-8")
        section = text.split(start_heading, 1)[1].split(end_heading, 1)[0]
        contributor_lines = [
            line.strip() for line in section.splitlines() if line.lstrip().startswith("- ")
        ]
        assert contributor_lines == [
            next(line for line in contributor_lines if "Ayleovelle" in line)
        ]


def test_release_documentation_has_no_confidential_or_civil_domain_terms() -> None:
    forbidden = re.compile(
        r"confidential paper|civil engineering|土木工程|桥梁工程|隧道工程|私密论文|保密论文",
        re.IGNORECASE,
    )
    for path in DOCUMENTATION_PATHS:
        assert forbidden.search(path.read_text(encoding="utf-8")) is None
