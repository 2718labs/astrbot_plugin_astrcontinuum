from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from typing import NoReturn

import pytest

from astrcontinuum.tokenization import (
    BYTE_FALLBACK,
    CANONICAL_O200K,
    OPENAI_CL100K,
    OPENAI_O200K,
    REFERENCE_O200K,
    TOKENIZER_IMPORT_FAILED,
    TokenizerError,
    TokenizerErrorCode,
    TokenizerProfile,
    TokenizerRegistry,
)
from astrcontinuum.tokenization.assets import (
    ASSET_DIGESTS,
    ASSET_SIZES,
    TokenizerAsset,
    load_mergeable_ranks,
)


@pytest.fixture
def registry() -> TokenizerRegistry:
    return TokenizerRegistry()


@pytest.mark.parametrize(
    ("profile", "text", "expected"),
    [
        (CANONICAL_O200K, "你好，世界", 3),
        (CANONICAL_O200K, "plain English text", 3),
        (CANONICAL_O200K, "中英 mixed 🚀", 5),
        (CANONICAL_O200K, "x = {'a': 1}", 8),
        (CANONICAL_O200K, 'def greet(name: str) -> str:\n    return f"Hello, {name}!"', 19),
        (CANONICAL_O200K, "<|endoftext|>", 7),
        (OPENAI_CL100K, "你好，世界", 6),
        (OPENAI_CL100K, "👩‍💻", 7),
    ],
)
def test_counter_matches_pinned_official_vectors(
    profile: TokenizerProfile,
    text: str,
    expected: int,
    registry: TokenizerRegistry,
) -> None:
    assert registry.counter_for(profile).count_text(text) == expected


def test_exact_and_reference_profiles_bind_the_requested_profile(
    registry: TokenizerRegistry,
) -> None:
    exact = registry.counter_for(OPENAI_O200K)
    reference = registry.counter_for(REFERENCE_O200K)

    assert exact.profile is OPENAI_O200K
    assert reference.profile is REFERENCE_O200K
    assert exact.count_text("你好，世界") == 3
    assert reference.count_text("你好，世界") == 4


def test_byte_fallback_counts_utf8_without_tiktoken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import astrcontinuum.tokenization.registry as registry_module

    monkeypatch.setattr(registry_module, "_tiktoken_runtime", None)
    counter = TokenizerRegistry().counter_for(BYTE_FALLBACK)

    assert TOKENIZER_IMPORT_FAILED == "TOKENIZER_IMPORT_FAILED"
    assert counter.profile is BYTE_FALLBACK
    assert counter.count_text("") == 0
    assert counter.count_text("你好") == 6


def test_tiktoken_profile_reports_stable_import_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import astrcontinuum.tokenization.registry as registry_module

    monkeypatch.setattr(registry_module, "_tiktoken_runtime", None)

    with pytest.raises(TokenizerError) as raised:
        TokenizerRegistry().counter_for(CANONICAL_O200K)

    assert raised.value.code is TokenizerErrorCode.TOKENIZER_IMPORT_FAILED
    assert str(raised.value) == TOKENIZER_IMPORT_FAILED


def test_package_imports_without_tiktoken_in_a_fresh_interpreter() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(repo_root), existing_pythonpath) if part
    )
    script = textwrap.dedent(
        """
        import sys

        PRIVATE_IMPORT_DETAIL = "private-tiktoken-import-detail"

        class BlockTiktoken:
            seen = []

            def find_spec(self, fullname, path=None, target=None):
                if fullname == "tiktoken" or fullname.startswith("tiktoken."):
                    self.seen.append(fullname)
                    raise ImportError(PRIVATE_IMPORT_DETAIL)
                return None

        assert "astrcontinuum.tokenization" not in sys.modules
        blocker = BlockTiktoken()
        sys.meta_path.insert(0, blocker)

        from astrcontinuum.tokenization import (
            BYTE_FALLBACK,
            CANONICAL_O200K,
            TOKENIZER_IMPORT_FAILED,
            TokenizerError,
            TokenizerErrorCode,
            TokenizerRegistry,
        )

        registry = TokenizerRegistry()
        assert blocker.seen
        assert registry.counter_for(BYTE_FALLBACK).count_text("你好") == 6
        try:
            registry.counter_for(CANONICAL_O200K)
        except TokenizerError as error:
            assert error.code is TokenizerErrorCode.TOKENIZER_IMPORT_FAILED
            assert str(error) == TOKENIZER_IMPORT_FAILED
            assert error.__context__ is None
            assert PRIVATE_IMPORT_DETAIL not in str(error)
            assert PRIVATE_IMPORT_DETAIL not in repr(error)
        else:
            raise AssertionError("canonical tokenizer unexpectedly constructed")

        print("fresh optional-import boundary verified")
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "fresh optional-import boundary verified"


def test_bundled_assets_have_pinned_sizes_and_digests() -> None:
    asset_root = Path(__file__).parents[2] / "astrcontinuum" / "tokenization" / "assets"

    for encoding_name, expected_size in ASSET_SIZES.items():
        data = (asset_root / f"{encoding_name}.tiktoken").read_bytes()
        assert len(data) == expected_size
        assert hashlib.sha256(data).hexdigest() == ASSET_DIGESTS[encoding_name]


def test_missing_asset_has_stable_content_free_error(tmp_path: Path) -> None:
    missing = tmp_path / "private" / "o200k_base.tiktoken"
    asset = TokenizerAsset(
        filename=missing.name,
        digest=ASSET_DIGESTS["o200k_base"],
        size=ASSET_SIZES["o200k_base"],
    )

    with pytest.raises(TokenizerError) as raised:
        load_mergeable_ranks(missing, asset)

    assert raised.value.code is TokenizerErrorCode.TOKENIZER_ASSET_MISSING
    assert raised.value.__context__ is None
    assert "private" not in str(raised.value)
    assert str(missing) not in repr(raised.value)


def test_asset_os_error_has_no_dynamic_exception_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_path = Path("private-os-error-asset.tiktoken")
    asset = TokenizerAsset(
        filename=private_path.name,
        digest=ASSET_DIGESTS["o200k_base"],
        size=ASSET_SIZES["o200k_base"],
    )

    def fail_open(path: Path, mode: str) -> NoReturn:
        raise OSError(f"private-os-detail:{path}")

    monkeypatch.setattr(Path, "open", fail_open)

    with pytest.raises(TokenizerError) as raised:
        load_mergeable_ranks(private_path, asset)

    assert raised.value.code is TokenizerErrorCode.TOKENIZER_ASSET_INVALID
    assert raised.value.__context__ is None
    assert "private-os-detail" not in str(raised.value)
    assert "private-os-detail" not in repr(raised.value)
    assert str(private_path) not in repr(raised.value)


def test_replaced_asset_has_stable_content_free_error(tmp_path: Path) -> None:
    asset = tmp_path / "private-asset.tiktoken"
    data = b"YQ== 0\n"
    asset.write_bytes(data)
    contract = TokenizerAsset(
        filename=asset.name,
        digest=ASSET_DIGESTS["o200k_base"],
        size=len(data),
    )

    with pytest.raises(TokenizerError) as raised:
        load_mergeable_ranks(asset, contract)

    assert raised.value.code is TokenizerErrorCode.TOKENIZER_ASSET_INVALID
    assert "private-asset" not in str(raised.value)
    assert str(asset) not in repr(raised.value)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\n",
        b"%%% 0\n",
        b"YQ== private-rank\n",
        b"YQ== -1\n",
        b"YQ== 0 extra\n",
        b"YQ== 0\nYQ== 1\n",
        b"YQ== 0\nYg== 0\n",
    ],
)
def test_malformed_asset_lines_are_rejected(data: bytes, tmp_path: Path) -> None:
    asset = tmp_path / "malformed.tiktoken"
    asset.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    contract = TokenizerAsset(filename=asset.name, digest=digest, size=len(data))

    with pytest.raises(TokenizerError) as raised:
        load_mergeable_ranks(asset, contract)

    assert raised.value.code is TokenizerErrorCode.TOKENIZER_ASSET_INVALID
    assert raised.value.__context__ is None
    assert "malformed" not in str(raised.value)
    assert "private-rank" not in str(raised.value)
    assert "private-rank" not in repr(raised.value)
    assert str(asset) not in repr(raised.value)


def test_valid_asset_lines_are_parsed_without_runtime_cache(tmp_path: Path) -> None:
    data = b"YQ== 0\nYg== 1\n"
    asset = tmp_path / "valid.tiktoken"
    asset.write_bytes(data)
    contract = TokenizerAsset(
        filename=asset.name,
        digest=hashlib.sha256(data).hexdigest(),
        size=len(data),
    )

    assert load_mergeable_ranks(asset, contract) == {
        b"a": 0,
        b"b": 1,
    }


def test_truncated_asset_is_rejected_before_digest_acceptance(tmp_path: Path) -> None:
    data = b"YQ== 0\n"
    path = tmp_path / "private-truncated.tiktoken"
    path.write_bytes(data)
    asset = TokenizerAsset(
        filename=path.name,
        digest=hashlib.sha256(data).hexdigest(),
        size=len(data) + 1,
    )

    with pytest.raises(TokenizerError) as raised:
        load_mergeable_ranks(path, asset)

    assert raised.value.code is TokenizerErrorCode.TOKENIZER_ASSET_INVALID
    assert raised.value.__context__ is None
    assert "private-truncated" not in str(raised.value)
    assert str(path) not in repr(raised.value)


def test_oversized_asset_is_rejected_even_when_full_digest_matches(tmp_path: Path) -> None:
    expected_prefix = b"YQ== 0\n"
    data = expected_prefix + b"Yg== 1\n"
    path = tmp_path / "private-oversized.tiktoken"
    path.write_bytes(data)
    asset = TokenizerAsset(
        filename=path.name,
        digest=hashlib.sha256(data).hexdigest(),
        size=len(expected_prefix),
    )

    with pytest.raises(TokenizerError) as raised:
        load_mergeable_ranks(path, asset)

    assert raised.value.code is TokenizerErrorCode.TOKENIZER_ASSET_INVALID
    assert raised.value.__context__ is None
    assert "private-oversized" not in str(raised.value)
    assert str(path) not in repr(raised.value)


def test_asset_reader_requests_only_manifest_size_plus_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"YQ== 0\nYg== 1\n"
    path = Path("private-bounded-read.tiktoken")
    asset = TokenizerAsset(
        filename=path.name,
        digest=hashlib.sha256(data).hexdigest(),
        size=len(data),
    )
    read_sizes: list[int] = []

    class SpyReader(BytesIO):
        def read(self, size: int = -1) -> bytes:
            read_sizes.append(size)
            return super().read(size)

    reader = SpyReader(data)

    def bounded_open(opened_path: Path, mode: str) -> BytesIO:
        assert opened_path == path
        assert mode == "rb"
        return reader

    def forbid_read_bytes(opened_path: Path) -> NoReturn:
        raise AssertionError(f"unbounded read attempted: {opened_path}")

    monkeypatch.setattr(Path, "open", bounded_open)
    monkeypatch.setattr(Path, "read_bytes", forbid_read_bytes)

    assert load_mergeable_ranks(path, asset) == {b"a": 0, b"b": 1}
    assert read_sizes == [asset.size + 1]


@pytest.mark.parametrize(
    "invalid_digest",
    [
        "0" * 63,
        "0" * 65,
        "A" * 64,
        "g" * 64,
        "é" * 64,
    ],
)
def test_invalid_expected_digest_is_rejected_before_comparison(
    invalid_digest: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import astrcontinuum.tokenization.assets as assets_module

    data = b"YQ== 0\n"
    path = tmp_path / "private-invalid-digest.tiktoken"
    path.write_bytes(data)
    asset = TokenizerAsset(
        filename=path.name,
        digest=invalid_digest,
        size=len(data),
    )

    def forbid_compare_digest(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("invalid digest reached compare_digest")

    monkeypatch.setattr(assets_module.hmac, "compare_digest", forbid_compare_digest)

    with pytest.raises(TokenizerError) as raised:
        load_mergeable_ranks(path, asset)

    assert raised.value.code is TokenizerErrorCode.TOKENIZER_ASSET_INVALID
    assert raised.value.__context__ is None
    assert invalid_digest not in str(raised.value)
    assert "private-invalid-digest" not in repr(raised.value)


def test_registry_never_uses_network_loader_or_temp_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import tiktoken
    import tiktoken.load

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("offline registry crossed a forbidden boundary")

    cache_root = tmp_path / "runtime-cache"
    data_gym_root = tmp_path / "data-gym-cache"
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(cache_root))
    monkeypatch.setenv("DATA_GYM_CACHE_DIR", str(data_gym_root))
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(tempfile, "gettempdir", forbidden)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", forbidden)
    monkeypatch.setattr(tiktoken, "get_encoding", forbidden)
    monkeypatch.setattr(tiktoken.load, "load_tiktoken_bpe", forbidden)

    counter = TokenizerRegistry().counter_for(CANONICAL_O200K)

    assert counter.count_text("离线") > 0
    assert not cache_root.exists()
    assert not data_gym_root.exists()


def test_encoding_construction_failure_has_no_dynamic_exception_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import astrcontinuum.tokenization.registry as registry_module

    class BrokenTiktokenRuntime:
        @staticmethod
        def Encoding(*args: object, **kwargs: object) -> NoReturn:
            raise RuntimeError("private-construction-detail")

    monkeypatch.setattr(
        registry_module,
        "_tiktoken_runtime",
        BrokenTiktokenRuntime(),
    )

    with pytest.raises(TokenizerError) as raised:
        TokenizerRegistry().counter_for(CANONICAL_O200K)

    assert raised.value.code is TokenizerErrorCode.TOKENIZER_CONSTRUCTION_FAILED
    assert raised.value.__context__ is None
    assert "private-construction-detail" not in str(raised.value)
    assert "private-construction-detail" not in repr(raised.value)


def test_registry_reuses_one_encoding_safely_across_threads(
    registry: TokenizerRegistry,
) -> None:
    def count_with_shared_encoding(_: int) -> tuple[int, int]:
        counter = registry.counter_for(CANONICAL_O200K)
        return counter.count_text("thread-safe"), id(counter._encoding)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = tuple(pool.map(count_with_shared_encoding, range(32)))

    assert {count for count, _ in results} == {2}
    assert len({encoding_id for _, encoding_id in results}) == 1
