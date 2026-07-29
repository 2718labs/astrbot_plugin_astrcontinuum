from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final, NoReturn

from .types import TokenizerError, TokenizerErrorCode


@dataclass(frozen=True, slots=True)
class TokenizerAsset:
    filename: str
    digest: str
    size: int


ASSET_DIGESTS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "cl100k_base": "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
        "o200k_base": "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
    }
)
ASSET_SIZES: Final[Mapping[str, int]] = MappingProxyType(
    {
        "cl100k_base": 1_681_126,
        "o200k_base": 3_613_922,
    }
)
ASSET_MANIFEST: Final[Mapping[str, TokenizerAsset]] = MappingProxyType(
    {
        encoding_name: TokenizerAsset(
            filename=f"{encoding_name}.tiktoken",
            digest=digest,
            size=ASSET_SIZES[encoding_name],
        )
        for encoding_name, digest in ASSET_DIGESTS.items()
    }
)
_LOWER_HEX_DIGITS: Final = frozenset("0123456789abcdef")


def _invalid(code: TokenizerErrorCode) -> NoReturn:
    raise TokenizerError(code)


def _is_sha256_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value.isascii()
        and all(character in _LOWER_HEX_DIGITS for character in value)
    )


def load_mergeable_ranks(path: Path, asset: TokenizerAsset) -> dict[bytes, int]:
    if (
        not isinstance(asset, TokenizerAsset)
        or isinstance(asset.size, bool)
        or not isinstance(asset.size, int)
        or asset.size < 0
        or not _is_sha256_digest(asset.digest)
    ):
        _invalid(TokenizerErrorCode.TOKENIZER_ASSET_INVALID)

    data: bytes | None = None
    read_failure: TokenizerErrorCode | None = None
    try:
        with path.open("rb") as source:
            data = source.read(asset.size + 1)
    except FileNotFoundError:
        read_failure = TokenizerErrorCode.TOKENIZER_ASSET_MISSING
    except OSError:
        read_failure = TokenizerErrorCode.TOKENIZER_ASSET_INVALID
    if read_failure is not None:
        raise TokenizerError(read_failure)
    if data is None:
        _invalid(TokenizerErrorCode.TOKENIZER_ASSET_INVALID)

    if len(data) != asset.size:
        _invalid(TokenizerErrorCode.TOKENIZER_ASSET_INVALID)
    if not hmac.compare_digest(
        hashlib.sha256(data).hexdigest(),
        asset.digest,
    ):
        _invalid(TokenizerErrorCode.TOKENIZER_ASSET_INVALID)

    ranks: dict[bytes, int] = {}
    seen_ranks: set[int] = set()
    lines = data.splitlines()
    if not lines:
        _invalid(TokenizerErrorCode.TOKENIZER_ASSET_INVALID)

    parse_failed = False
    for line in lines:
        parts = line.split()
        if len(parts) != 2:
            _invalid(TokenizerErrorCode.TOKENIZER_ASSET_INVALID)
        try:
            token = base64.b64decode(parts[0], validate=True)
            rank = int(parts[1])
        except (ValueError, binascii.Error, OverflowError):
            parse_failed = True
            break
        if not token or token in ranks or rank < 0 or rank in seen_ranks:
            _invalid(TokenizerErrorCode.TOKENIZER_ASSET_INVALID)
        ranks[token] = rank
        seen_ranks.add(rank)
    if parse_failed:
        raise TokenizerError(TokenizerErrorCode.TOKENIZER_ASSET_INVALID)

    return ranks
