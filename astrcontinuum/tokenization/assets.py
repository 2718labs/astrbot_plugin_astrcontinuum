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


def _invalid(code: TokenizerErrorCode) -> NoReturn:
    raise TokenizerError(code)


def load_mergeable_ranks(path: Path, expected_digest: str) -> dict[bytes, int]:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise TokenizerError(TokenizerErrorCode.TOKENIZER_ASSET_MISSING) from None
    except OSError:
        raise TokenizerError(TokenizerErrorCode.TOKENIZER_ASSET_INVALID) from None

    if not isinstance(expected_digest, str) or not hmac.compare_digest(
        hashlib.sha256(data).hexdigest(),
        expected_digest,
    ):
        _invalid(TokenizerErrorCode.TOKENIZER_ASSET_INVALID)

    ranks: dict[bytes, int] = {}
    seen_ranks: set[int] = set()
    lines = data.splitlines()
    if not lines:
        _invalid(TokenizerErrorCode.TOKENIZER_ASSET_INVALID)

    try:
        for line in lines:
            parts = line.split()
            if len(parts) != 2:
                _invalid(TokenizerErrorCode.TOKENIZER_ASSET_INVALID)
            token = base64.b64decode(parts[0], validate=True)
            rank = int(parts[1])
            if not token or token in ranks or rank < 0 or rank in seen_ranks:
                _invalid(TokenizerErrorCode.TOKENIZER_ASSET_INVALID)
            ranks[token] = rank
            seen_ranks.add(rank)
    except TokenizerError:
        raise
    except (ValueError, binascii.Error, OverflowError):
        raise TokenizerError(TokenizerErrorCode.TOKENIZER_ASSET_INVALID) from None

    return ranks
