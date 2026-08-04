from __future__ import annotations

import base64
import hashlib
import json

import pytest

from astrcontinuum.storage.crypto import (
    ENCRYPTED_JSON_MARKER,
    SecureCodec,
    SecurityErrorCode,
    StorageSecurityError,
    encode_key_text,
    key_id_for,
    parse_key_text,
)

RAW_KEY = bytes(range(32))
OTHER_KEY = bytes(reversed(range(32)))


def _assert_code(
    error: pytest.ExceptionInfo[StorageSecurityError], code: SecurityErrorCode
) -> None:
    assert error.value.code is code
    assert str(error.value) == code.value
    assert repr(error.value) == f"StorageSecurityError(code={code.value!r})"


def test_key_text_round_trips_as_canonical_unpadded_base64url() -> None:
    encoded = encode_key_text(RAW_KEY)

    assert len(encoded) == 43
    assert "=" not in encoded
    assert parse_key_text(encoded) == RAW_KEY
    assert key_id_for(RAW_KEY) == hashlib.sha256(RAW_KEY).hexdigest()[:16]


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        " ",
        encode_key_text(RAW_KEY) + "=",
        encode_key_text(RAW_KEY) + "\n",
        base64.urlsafe_b64encode(b"short").decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(b"x" * 33).decode("ascii").rstrip("="),
        "*" * 43,
        "é" * 43,
    ],
)
def test_key_parser_rejects_noncanonical_or_wrong_length_text(candidate: str) -> None:
    with pytest.raises(StorageSecurityError) as raised:
        parse_key_text(candidate)

    _assert_code(raised, SecurityErrorCode.STORAGE_KEY_INVALID)
    if candidate:
        assert candidate not in repr(raised.value)


@pytest.mark.parametrize("raw_key", [b"", b"x" * 16, b"x" * 24, b"x" * 31, b"x" * 33])
def test_codec_requires_exactly_one_256_bit_key(raw_key: bytes) -> None:
    with pytest.raises(StorageSecurityError) as raised:
        SecureCodec(raw_key)

    _assert_code(raised, SecurityErrorCode.STORAGE_KEY_INVALID)


@pytest.mark.parametrize("plaintext", ["", "中文上下文", "emoji: 🐈", "a\x00b"])
def test_text_envelope_round_trips_utf8_and_uses_unique_nonces(plaintext: str) -> None:
    codec = SecureCodec(RAW_KEY)

    first = codec.encrypt_text("journal_events", "content", "evt:1", plaintext)
    second = codec.encrypt_text("journal_events", "content", "evt:1", plaintext)

    assert first.startswith(f"acenc:v1:{codec.key_id}:")
    assert second.startswith(f"acenc:v1:{codec.key_id}:")
    assert first != second
    assert codec.decrypt_text("journal_events", "content", "evt:1", first) == plaintext
    assert codec.decrypt_text("journal_events", "content", "evt:1", second) == plaintext


@pytest.mark.parametrize("value", [0, 1, 2**63 - 1])
def test_non_negative_integer_round_trips_canonical_decimal(value: int) -> None:
    codec = SecureCodec(RAW_KEY)

    envelope = codec.encrypt_non_negative_int(
        "journal_events",
        "token_count",
        "evt:1",
        value,
    )

    assert envelope.startswith(f"acenc:v1:{codec.key_id}:")
    assert (
        codec.decrypt_non_negative_int(
            "journal_events",
            "token_count",
            "evt:1",
            envelope,
        )
        == value
    )


@pytest.mark.parametrize("value", [True, False, -1, 1.0, "1", "01", None])
def test_non_negative_integer_encrypt_rejects_non_integer_domain_values(
    value: object,
) -> None:
    codec = SecureCodec(RAW_KEY)

    with pytest.raises(StorageSecurityError) as raised:
        codec.encrypt_non_negative_int(
            "journal_events",
            "token_count",
            "evt:1",
            value,
        )

    _assert_code(raised, SecurityErrorCode.STORAGE_ENVELOPE_INVALID)
    assert raised.value.__cause__ is None
    assert repr(value) not in repr(raised.value)


@pytest.mark.parametrize("plaintext", ["", "01", "+1", "-1", "1.0", "１２"])
def test_non_negative_integer_decrypt_rejects_noncanonical_decimal(
    plaintext: str,
) -> None:
    codec = SecureCodec(RAW_KEY)
    envelope = codec.encrypt_text(
        "journal_events",
        "token_count",
        "evt:1",
        plaintext,
    )

    with pytest.raises(StorageSecurityError) as raised:
        codec.decrypt_non_negative_int(
            "journal_events",
            "token_count",
            "evt:1",
            envelope,
        )

    _assert_code(raised, SecurityErrorCode.STORAGE_ENVELOPE_INVALID)
    assert raised.value.__cause__ is None
    if plaintext:
        assert plaintext not in repr(raised.value)


@pytest.mark.parametrize(
    ("table", "column", "record_key"),
    [
        ("snapshots", "token_count", "evt:1"),
        ("journal_events", "token_cost", "evt:1"),
        ("journal_events", "token_count", "evt:2"),
    ],
)
def test_non_negative_integer_aad_rejects_context_swaps(
    table: str,
    column: str,
    record_key: str,
) -> None:
    codec = SecureCodec(RAW_KEY)
    envelope = codec.encrypt_non_negative_int(
        "journal_events",
        "token_count",
        "evt:1",
        17,
    )

    with pytest.raises(StorageSecurityError) as raised:
        codec.decrypt_non_negative_int(table, column, record_key, envelope)

    _assert_code(raised, SecurityErrorCode.STORAGE_AUTHENTICATION_FAILED)


def test_non_negative_integer_rejects_ciphertext_swapped_between_rows() -> None:
    codec = SecureCodec(RAW_KEY)
    first = codec.encrypt_non_negative_int(
        "journal_events",
        "token_count",
        "evt:1",
        17,
    )
    second = codec.encrypt_non_negative_int(
        "journal_events",
        "token_count",
        "evt:2",
        23,
    )

    with pytest.raises(StorageSecurityError) as first_swap:
        codec.decrypt_non_negative_int(
            "journal_events",
            "token_count",
            "evt:1",
            second,
        )
    with pytest.raises(StorageSecurityError) as second_swap:
        codec.decrypt_non_negative_int(
            "journal_events",
            "token_count",
            "evt:2",
            first,
        )

    _assert_code(first_swap, SecurityErrorCode.STORAGE_AUTHENTICATION_FAILED)
    _assert_code(second_swap, SecurityErrorCode.STORAGE_AUTHENTICATION_FAILED)


def test_plaintext_and_envelope_bounds_are_enforced_before_crypto() -> None:
    codec = SecureCodec(RAW_KEY, max_plaintext_bytes=8, max_envelope_chars=160)

    envelope = codec.encrypt_text("journal_events", "content", "evt:1", "12345678")
    assert codec.decrypt_text("journal_events", "content", "evt:1", envelope) == "12345678"

    with pytest.raises(StorageSecurityError) as oversized_plaintext:
        codec.encrypt_text("journal_events", "content", "evt:1", "123456789")
    _assert_code(oversized_plaintext, SecurityErrorCode.STORAGE_ENVELOPE_INVALID)

    with pytest.raises(StorageSecurityError) as oversized_envelope:
        codec.decrypt_text("journal_events", "content", "evt:1", "x" * 161)
    _assert_code(oversized_envelope, SecurityErrorCode.STORAGE_ENVELOPE_INVALID)


def test_wrong_key_id_is_distinct_from_authenticated_tampering() -> None:
    codec = SecureCodec(RAW_KEY)
    envelope = codec.encrypt_text("journal_events", "content", "evt:1", "secret")

    with pytest.raises(StorageSecurityError) as wrong_key:
        SecureCodec(OTHER_KEY).decrypt_text("journal_events", "content", "evt:1", envelope)
    _assert_code(wrong_key, SecurityErrorCode.STORAGE_KEY_MISMATCH)

    parts = envelope.split(":")
    ciphertext = bytearray(base64.urlsafe_b64decode(parts[-1] + ("=" * (-len(parts[-1]) % 4))))
    ciphertext[-1] ^= 1
    parts[-1] = base64.urlsafe_b64encode(ciphertext).decode("ascii").rstrip("=")
    tampered = ":".join(parts)
    with pytest.raises(StorageSecurityError) as invalid_tag:
        codec.decrypt_text("journal_events", "content", "evt:1", tampered)
    _assert_code(invalid_tag, SecurityErrorCode.STORAGE_AUTHENTICATION_FAILED)
    assert envelope not in repr(invalid_tag.value)


@pytest.mark.parametrize(
    ("table", "column", "record_key"),
    [
        ("journal_events", "content", "evt:2"),
        ("journal_events", "other_content", "evt:1"),
        ("snapshots", "content", "evt:1"),
    ],
)
def test_aad_rejects_cross_row_column_and_table_swaps(
    table: str,
    column: str,
    record_key: str,
) -> None:
    codec = SecureCodec(RAW_KEY)
    envelope = codec.encrypt_text("journal_events", "content", "evt:1", "bound")

    with pytest.raises(StorageSecurityError) as raised:
        codec.decrypt_text(table, column, record_key, envelope)

    _assert_code(raised, SecurityErrorCode.STORAGE_AUTHENTICATION_FAILED)


@pytest.mark.parametrize(
    "envelope",
    [
        "",
        "acenc:v1",
        "other:v1:0000000000000000:AA:AA",
        "acenc:v2:0000000000000000:AA:AA",
        "acenc:v1:ABCDEFABCDEFABCD:AA:AA",
        "acenc:v1:000000000000000:AA:AA",
        "acenc:v1:000000000000000g:AA:AA",
        "acenc:v1:0000000000000000:AA==:AA",
        "acenc:v1:0000000000000000:AA:AA==",
        "acenc:v1:0000000000000000:AA:AA",
        "acenc:v1:0000000000000000:AAAAAAAAAAAAAAAA:AA",
        "acenc:v1:0000000000000000:AAAAAAAAAAAAAAAA:AAAAAAAAAAAAAAAAAAAA",
        "acenc:v1:0000000000000000:AAAAAAAAAAAAAAAA:AAAAAAAAAAAAAAAAAAAA:extra",
    ],
)
def test_malformed_envelopes_fail_closed_without_echo(envelope: str) -> None:
    codec = SecureCodec(RAW_KEY)

    with pytest.raises(StorageSecurityError) as raised:
        codec.decrypt_text("journal_events", "content", "evt:1", envelope)

    _assert_code(raised, SecurityErrorCode.STORAGE_ENVELOPE_INVALID)
    if envelope:
        assert envelope not in repr(raised.value)


def test_object_and_array_sentinels_are_exact_and_round_trip_original_json() -> None:
    codec = SecureCodec(RAW_KEY)
    object_json = '{"text":"中文","count":1}'
    array_json = '["anchor:1","anchor:2"]'

    object_sentinel = codec.encrypt_object_json(
        "capsules",
        "canonical_capsule_json",
        "cap:1",
        object_json,
    )
    array_sentinel = codec.encrypt_array_json(
        "snapshots",
        "exact_anchor_ids_json",
        "snap:1",
        array_json,
    )

    parsed_object = json.loads(object_sentinel)
    parsed_array = json.loads(array_sentinel)
    assert list(parsed_object) == [ENCRYPTED_JSON_MARKER]
    assert isinstance(parsed_object[ENCRYPTED_JSON_MARKER], str)
    assert parsed_array[0] == ENCRYPTED_JSON_MARKER
    assert len(parsed_array) == 2
    assert (
        codec.decrypt_object_json(
            "capsules",
            "canonical_capsule_json",
            "cap:1",
            object_sentinel,
        )
        == object_json
    )
    assert (
        codec.decrypt_array_json(
            "snapshots",
            "exact_anchor_ids_json",
            "snap:1",
            array_sentinel,
        )
        == array_json
    )


@pytest.mark.parametrize(
    ("kind", "sentinel"),
    [
        ("object", "{}"),
        ("object", '{"$astrcontinuum_encrypted":1}'),
        ("object", '{"$astrcontinuum_encrypted":"x","extra":true}'),
        ("object", '["$astrcontinuum_encrypted","x"]'),
        ("array", "[]"),
        ("array", '["$astrcontinuum_encrypted"]'),
        ("array", '["$astrcontinuum_encrypted","x","extra"]'),
        ("array", '{"$astrcontinuum_encrypted":"x"}'),
    ],
)
def test_json_sentinel_parser_rejects_open_or_wrong_shapes(kind: str, sentinel: str) -> None:
    codec = SecureCodec(RAW_KEY)

    with pytest.raises(StorageSecurityError) as raised:
        if kind == "object":
            codec.decrypt_object_json("capsules", "canonical_capsule_json", "cap:1", sentinel)
        else:
            codec.decrypt_array_json("snapshots", "exact_anchor_ids_json", "snap:1", sentinel)

    _assert_code(raised, SecurityErrorCode.STORAGE_ENVELOPE_INVALID)


@pytest.mark.parametrize(
    ("method", "plaintext"),
    [
        ("object", "[]"),
        ("object", "not-json"),
        ("array", "{}"),
        ("array", "not-json"),
    ],
)
def test_json_encryptor_requires_the_declared_json_container(
    method: str,
    plaintext: str,
) -> None:
    codec = SecureCodec(RAW_KEY)

    with pytest.raises(StorageSecurityError) as raised:
        if method == "object":
            codec.encrypt_object_json(
                "capsules",
                "canonical_capsule_json",
                "cap:1",
                plaintext,
            )
        else:
            codec.encrypt_array_json(
                "snapshots",
                "exact_anchor_ids_json",
                "snap:1",
                plaintext,
            )

    _assert_code(raised, SecurityErrorCode.STORAGE_ENVELOPE_INVALID)
