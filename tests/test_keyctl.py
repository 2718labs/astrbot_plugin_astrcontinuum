from __future__ import annotations

from pathlib import Path

from astrcontinuum import keyctl
from astrcontinuum.storage.crypto import encode_key_text, key_id_for, parse_key_text


def test_generate_creates_exclusive_key_file_without_printing_raw_key(
    tmp_path: Path,
    capsys,
) -> None:
    output = tmp_path / "secrets" / "master.key"
    output.parent.mkdir()

    result = keyctl.main(["generate", "--output", str(output)])
    captured = capsys.readouterr()
    raw_text = output.read_text(encoding="ascii").removesuffix("\n")
    raw_key = parse_key_text(raw_text)

    assert result == 0
    assert output.read_text(encoding="ascii") == encode_key_text(raw_key) + "\n"
    assert f"KEY_ID={key_id_for(raw_key)}" in captured.out
    assert f"OUTPUT={output.resolve()}" in captured.out
    assert "PERMISSIONS=" in captured.out
    assert captured.err == ""
    assert raw_text not in captured.out
    assert raw_text not in captured.err


def test_generate_never_overwrites_existing_file(tmp_path: Path, capsys) -> None:
    output = tmp_path / "master.key"
    output.write_text("operator-owned", encoding="ascii")

    result = keyctl.main(["generate", "--output", str(output)])
    captured = capsys.readouterr()

    assert result == 2
    assert output.read_text(encoding="ascii") == "operator-owned"
    assert captured.out == ""
    assert captured.err == "ERROR=STORAGE_KEY_FILE_UNSAFE\n"


def test_generate_rejects_output_inside_supplied_data_directory(
    tmp_path: Path,
    capsys,
) -> None:
    data_dir = tmp_path / "plugin-data"
    data_dir.mkdir()
    output = data_dir / "master.key"

    result = keyctl.main(
        [
            "generate",
            "--output",
            str(output),
            "--data-dir",
            str(data_dir),
        ]
    )
    captured = capsys.readouterr()

    assert result == 2
    assert not output.exists()
    assert captured.out == ""
    assert captured.err == "ERROR=STORAGE_KEY_FILE_UNSAFE\n"


def test_fingerprint_matches_generated_key_without_echoing_it(
    tmp_path: Path,
    capsys,
) -> None:
    output = tmp_path / "master.key"
    assert keyctl.main(["generate", "--output", str(output)]) == 0
    capsys.readouterr()
    raw_text = output.read_text(encoding="ascii").removesuffix("\n")
    raw_key = parse_key_text(raw_text)

    result = keyctl.main(["fingerprint", "--key-file", str(output)])
    captured = capsys.readouterr()

    assert result == 0
    assert captured.out == f"KEY_ID={key_id_for(raw_key)}\n"
    assert captured.err == ""
    assert raw_text not in captured.out
    assert raw_text not in captured.err


def test_fingerprint_failure_is_content_free(tmp_path: Path, capsys) -> None:
    key_file = tmp_path / "invalid.key"
    raw_secret = "this-value-must-not-be-echoed"
    key_file.write_text(raw_secret, encoding="ascii")

    result = keyctl.main(["fingerprint", "--key-file", str(key_file)])
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert captured.err == "ERROR=STORAGE_KEY_INVALID\n"
    assert raw_secret not in captured.err


def test_unknown_raw_key_argument_is_rejected_without_echoing_value(
    tmp_path: Path,
    capsys,
) -> None:
    raw_secret = encode_key_text(bytes(range(32)))

    result = keyctl.main(
        [
            "generate",
            "--output",
            str(tmp_path / "master.key"),
            "--key",
            raw_secret,
        ]
    )
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert captured.err == "ERROR=KEYCTL_USAGE_INVALID\n"
    assert raw_secret not in captured.err
