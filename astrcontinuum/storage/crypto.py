"""Closed authenticated-encryption envelopes for durable storage values."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import secrets
from enum import Enum
from typing import NoReturn

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32
NONCE_BYTES = 12
TAG_BYTES = 16
KEY_ID_HEX_CHARS = 16
ENVELOPE_PREFIX = "acenc"
ENVELOPE_VERSION = "v1"
ENCRYPTED_JSON_MARKER = "$astrcontinuum_encrypted"
DEFAULT_MAX_PLAINTEXT_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_ENVELOPE_CHARS = 12 * 1024 * 1024

_KEY_TEXT_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_KEY_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_AAD_NAMESPACE = ("astrcontinuum", "envelope-v1")


class SecurityErrorCode(str, Enum):
    """Stable content-free storage-security failures."""

    STORAGE_KEY_MISSING = "STORAGE_KEY_MISSING"
    STORAGE_KEY_INVALID = "STORAGE_KEY_INVALID"
    STORAGE_KEY_FILE_UNSAFE = "STORAGE_KEY_FILE_UNSAFE"
    STORAGE_LOCAL_KEY_CREATE_FAILED = "STORAGE_LOCAL_KEY_CREATE_FAILED"
    STORAGE_KEY_MISMATCH = "STORAGE_KEY_MISMATCH"
    STORAGE_PREVIOUS_KEY_REQUIRED = "STORAGE_PREVIOUS_KEY_REQUIRED"
    STORAGE_ENVELOPE_INVALID = "STORAGE_ENVELOPE_INVALID"
    STORAGE_AUTHENTICATION_FAILED = "STORAGE_AUTHENTICATION_FAILED"
    STORAGE_LEGACY_VALIDATION_FAILED = "STORAGE_LEGACY_VALIDATION_FAILED"
    STORAGE_MIGRATION_FAILED = "STORAGE_MIGRATION_FAILED"
    STORAGE_REKEY_FAILED = "STORAGE_REKEY_FAILED"
    STORAGE_SCRUB_FAILED = "STORAGE_SCRUB_FAILED"


class StorageSecurityError(ValueError):
    """Expose one stable code without retaining secret-bearing details."""

    __slots__ = ("_code",)

    def __init__(self, code: SecurityErrorCode) -> None:
        self._code = code
        super().__init__(code.value)

    @property
    def code(self) -> SecurityErrorCode:
        """Return the public stable failure code."""

        return self._code

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self._code.value!r})"


def _invalid(code: SecurityErrorCode = SecurityErrorCode.STORAGE_ENVELOPE_INVALID) -> NoReturn:
    raise StorageSecurityError(code) from None


def _raw_key(value: object) -> bytes:
    if not isinstance(value, bytes) or len(value) != KEY_BYTES:
        _invalid(SecurityErrorCode.STORAGE_KEY_INVALID)
    return bytes(value)


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: object) -> bytes:
    if (
        not isinstance(value, str)
        or not value
        or "=" in value
        or _BASE64URL_RE.fullmatch(value) is None
    ):
        _invalid()
    try:
        decoded = base64.b64decode(
            value + ("=" * (-len(value) % 4)),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError):
        _invalid()
    if _base64url_encode(decoded) != value:
        _invalid()
    return decoded


def encode_key_text(raw_key: bytes) -> str:
    """Encode one 256-bit key as canonical unpadded base64url."""

    return _base64url_encode(_raw_key(raw_key))


def parse_key_text(value: object) -> bytes:
    """Parse one canonical unpadded base64url 256-bit key."""

    if not isinstance(value, str) or _KEY_TEXT_RE.fullmatch(value) is None:
        _invalid(SecurityErrorCode.STORAGE_KEY_INVALID)
    try:
        decoded = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        _invalid(SecurityErrorCode.STORAGE_KEY_INVALID)
    if len(decoded) != KEY_BYTES or encode_key_text(decoded) != value:
        _invalid(SecurityErrorCode.STORAGE_KEY_INVALID)
    return decoded


def key_id_for(raw_key: bytes) -> str:
    """Return the non-secret short identifier for one valid key."""

    return hashlib.sha256(_raw_key(raw_key)).hexdigest()[:KEY_ID_HEX_CHARS]


def _aad_field(value: object) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 4_096:
        _invalid()
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        _invalid()
    if len(encoded) > 65_535:
        _invalid()
    return len(encoded).to_bytes(4, "big") + encoded


def _aad(table: object, column: object, record_key: object) -> bytes:
    return b"".join(_aad_field(value) for value in (*_AAD_NAMESPACE, table, column, record_key))


def _json_container(value: object, expected_type: type[dict | list]) -> None:
    if not isinstance(value, str):
        _invalid()
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError):
        _invalid()
    if type(parsed) is not expected_type:
        _invalid()


class SecureCodec:
    """Encrypt and authenticate one protected scalar at the SQL boundary."""

    __slots__ = (
        "_aead",
        "_key_id",
        "_max_envelope_chars",
        "_max_plaintext_bytes",
    )

    def __init__(
        self,
        raw_key: bytes,
        *,
        max_plaintext_bytes: int = DEFAULT_MAX_PLAINTEXT_BYTES,
        max_envelope_chars: int = DEFAULT_MAX_ENVELOPE_CHARS,
    ) -> None:
        key = _raw_key(raw_key)
        if (
            isinstance(max_plaintext_bytes, bool)
            or not isinstance(max_plaintext_bytes, int)
            or max_plaintext_bytes < 0
            or isinstance(max_envelope_chars, bool)
            or not isinstance(max_envelope_chars, int)
            or max_envelope_chars < 64
        ):
            _invalid()
        self._aead = AESGCM(key)
        self._key_id = key_id_for(key)
        self._max_plaintext_bytes = max_plaintext_bytes
        self._max_envelope_chars = max_envelope_chars

    @property
    def key_id(self) -> str:
        """Return the non-secret identifier of the active key."""

        return self._key_id

    def encrypt_text(
        self,
        table: str,
        column: str,
        record_key: str,
        plaintext: str,
    ) -> str:
        """Encrypt one UTF-8 scalar into a canonical envelope."""

        if not isinstance(plaintext, str):
            _invalid()
        try:
            encoded = plaintext.encode("utf-8")
        except UnicodeEncodeError:
            _invalid()
        if len(encoded) > self._max_plaintext_bytes:
            _invalid()
        nonce = secrets.token_bytes(NONCE_BYTES)
        try:
            ciphertext = self._aead.encrypt(
                nonce,
                encoded,
                _aad(table, column, record_key),
            )
        except (OverflowError, ValueError):
            _invalid()
        envelope = ":".join(
            (
                ENVELOPE_PREFIX,
                ENVELOPE_VERSION,
                self._key_id,
                _base64url_encode(nonce),
                _base64url_encode(ciphertext),
            )
        )
        if len(envelope) > self._max_envelope_chars:
            _invalid()
        return envelope

    def decrypt_text(
        self,
        table: str,
        column: str,
        record_key: str,
        envelope: str,
    ) -> str:
        """Authenticate and decrypt one canonical envelope."""

        if (
            not isinstance(envelope, str)
            or not envelope
            or len(envelope) > self._max_envelope_chars
        ):
            _invalid()
        parts = envelope.split(":")
        if len(parts) != 5:
            _invalid()
        prefix, version, key_id, nonce_text, ciphertext_text = parts
        if (
            prefix != ENVELOPE_PREFIX
            or version != ENVELOPE_VERSION
            or _KEY_ID_RE.fullmatch(key_id) is None
        ):
            _invalid()
        nonce = _base64url_decode(nonce_text)
        ciphertext = _base64url_decode(ciphertext_text)
        if len(nonce) != NONCE_BYTES or len(ciphertext) < TAG_BYTES:
            _invalid()
        if key_id != self._key_id:
            _invalid(SecurityErrorCode.STORAGE_KEY_MISMATCH)
        try:
            plaintext = self._aead.decrypt(
                nonce,
                ciphertext,
                _aad(table, column, record_key),
            )
        except InvalidTag:
            _invalid(SecurityErrorCode.STORAGE_AUTHENTICATION_FAILED)
        except (OverflowError, ValueError):
            _invalid()
        if len(plaintext) > self._max_plaintext_bytes:
            _invalid()
        try:
            return plaintext.decode("utf-8")
        except UnicodeDecodeError:
            _invalid()

    def encrypt_non_negative_int(
        self,
        table: str,
        column: str,
        record_key: str,
        value: object,
    ) -> str:
        """Encrypt one non-negative integer as canonical ASCII decimal."""

        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            _invalid()
        try:
            plaintext = str(value)
        except (OverflowError, ValueError):
            _invalid()
        return self.encrypt_text(table, column, record_key, plaintext)

    def decrypt_non_negative_int(
        self,
        table: str,
        column: str,
        record_key: str,
        envelope: object,
    ) -> int:
        """Authenticate and decode one canonical non-negative integer."""

        if not isinstance(envelope, str):
            _invalid()
        plaintext = self.decrypt_text(table, column, record_key, envelope)
        if (
            not plaintext
            or not plaintext.isascii()
            or not plaintext.isdecimal()
            or (plaintext != "0" and plaintext.startswith("0"))
        ):
            _invalid()
        try:
            value = int(plaintext)
        except (OverflowError, ValueError):
            _invalid()
        if value < 0 or str(value) != plaintext:
            _invalid()
        return value

    def encrypt_object_json(
        self,
        table: str,
        column: str,
        record_key: str,
        canonical_json: str,
    ) -> str:
        """Encrypt a JSON object into the closed object sentinel."""

        _json_container(canonical_json, dict)
        envelope = self.encrypt_text(table, column, record_key, canonical_json)
        return json.dumps(
            {ENCRYPTED_JSON_MARKER: envelope},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def decrypt_object_json(
        self,
        table: str,
        column: str,
        record_key: str,
        sentinel_json: str,
    ) -> str:
        """Decrypt one closed object sentinel."""

        if not isinstance(sentinel_json, str):
            _invalid()
        try:
            sentinel = json.loads(sentinel_json)
        except (json.JSONDecodeError, TypeError, ValueError):
            _invalid()
        if (
            type(sentinel) is not dict
            or list(sentinel) != [ENCRYPTED_JSON_MARKER]
            or not isinstance(sentinel[ENCRYPTED_JSON_MARKER], str)
        ):
            _invalid()
        plaintext = self.decrypt_text(
            table,
            column,
            record_key,
            sentinel[ENCRYPTED_JSON_MARKER],
        )
        _json_container(plaintext, dict)
        return plaintext

    def encrypt_array_json(
        self,
        table: str,
        column: str,
        record_key: str,
        canonical_json: str,
    ) -> str:
        """Encrypt a JSON array into the closed array sentinel."""

        _json_container(canonical_json, list)
        envelope = self.encrypt_text(table, column, record_key, canonical_json)
        return json.dumps(
            [ENCRYPTED_JSON_MARKER, envelope],
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def decrypt_array_json(
        self,
        table: str,
        column: str,
        record_key: str,
        sentinel_json: str,
    ) -> str:
        """Decrypt one closed array sentinel."""

        if not isinstance(sentinel_json, str):
            _invalid()
        try:
            sentinel = json.loads(sentinel_json)
        except (json.JSONDecodeError, TypeError, ValueError):
            _invalid()
        if (
            type(sentinel) is not list
            or len(sentinel) != 2
            or sentinel[0] != ENCRYPTED_JSON_MARKER
            or not isinstance(sentinel[1], str)
        ):
            _invalid()
        plaintext = self.decrypt_text(
            table,
            column,
            record_key,
            sentinel[1],
        )
        _json_container(plaintext, list)
        return plaintext
