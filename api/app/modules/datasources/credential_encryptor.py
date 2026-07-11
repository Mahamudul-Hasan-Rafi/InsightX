# api/app/modules/datasources/credential_encryptor.py
#
# PURPOSE:
#   Encrypts credential dicts before DB writes and decrypts them when a live
#   connection is needed. Nothing else in the system touches raw credentials.
#
# ALGORITHM: AES-256-GCM (Authenticated Encryption with Associated Data)
#   GCM mode produces a 16-byte authentication tag alongside the ciphertext.
#   If the stored string is tampered with OR the wrong key is used,
#   AESGCM.decrypt() raises InvalidTag — do not catch this silently.
#
# STORAGE FORMAT: "iv_hex:tag_hex:ciphertext_hex"
#   Three colon-delimited hex-encoded components in one TEXT column.
#   The IV is fresh-random on every encrypt() call — never reuse.
#
# WHEN IS THIS CALLED?
#   encrypt() — in service.create_datasource(), before the DB write
#   decrypt() — in service.retest_datasource() and service.get_schema(),
#                when we need live credentials to open a new connection
#
# GENERATE A KEY:
#   python -c "import secrets; print(secrets.token_hex(32))"
#   Add to .env:  CREDENTIAL_ENCRYPTION_KEY=<64-char hex string>

import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings

_IV_BYTES  = 12   # 96-bit nonce — NIST recommended length for GCM
_TAG_BYTES = 16   # 128-bit authentication tag — maximum GCM security


def _get_key() -> bytes:
    """
    Validates and returns the 32-byte encryption key from settings.

    Called at runtime (not import time), so the app can start even if the
    key isn't set yet — the first actual encrypt/decrypt call fails with a
    clear, actionable error message.

    Returns:
        32-byte (256-bit) key

    Raises:
        ValueError: If the key is missing or not exactly 64 hex characters
    """
    hex_key = settings.credential_encryption_key

    if not hex_key or len(hex_key) != 64:
        raise ValueError(
            "CREDENTIAL_ENCRYPTION_KEY must be a 64-character hex string. "
            "Generate one: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    try:
        return bytes.fromhex(hex_key)
    except ValueError:
        raise ValueError("CREDENTIAL_ENCRYPTION_KEY contains invalid hex characters")


def encrypt(credentials: dict) -> str:
    """
    Encrypts a credentials dict to a single storable string.

    JSON-serialises the dict first, so any credential shape (password, wallet
    path, kerberos principal, etc.) can be stored in one TEXT column without
    needing engine-specific encrypted columns.

    A fresh random IV is generated on every call.
    NEVER reuse an IV with the same key in GCM mode.

    Args:
        credentials: Plain dict, e.g. {"username": "sa", "password": "secret"}

    Returns:
        "iv_hex:tag_hex:ciphertext_hex"
    """
    key = _get_key()
    iv  = os.urandom(_IV_BYTES)   # Cryptographically random nonce — never reuse

    aesgcm    = AESGCM(key)
    plaintext = json.dumps(credentials).encode("utf-8")

    # AESGCM.encrypt() returns ciphertext + 16-byte tag concatenated
    ciphertext_and_tag = aesgcm.encrypt(iv, plaintext, None)

    ciphertext = ciphertext_and_tag[:-_TAG_BYTES]
    tag        = ciphertext_and_tag[-_TAG_BYTES:]

    return ":".join([iv.hex(), tag.hex(), ciphertext.hex()])


def decrypt(encrypted_string: str) -> dict:
    """
    Decrypts a stored encrypted credentials string back to the original dict.

    Args:
        encrypted_string: "iv_hex:tag_hex:ciphertext_hex" from the database

    Returns:
        The original credentials dict

    Raises:
        ValueError:                         Malformed format
        cryptography.exceptions.InvalidTag: Tampered data or wrong key — propagate this
    """
    parts = encrypted_string.split(":")

    if len(parts) != 3:
        raise ValueError(
            "Malformed encrypted credential. Expected format: 'iv_hex:tag_hex:ciphertext_hex'"
        )

    try:
        iv         = bytes.fromhex(parts[0])
        tag        = bytes.fromhex(parts[1])
        ciphertext = bytes.fromhex(parts[2])
    except ValueError as exc:
        raise ValueError(f"Encrypted credential contains invalid hex: {exc}") from exc

    key    = _get_key()
    aesgcm = AESGCM(key)

    # AESGCM.decrypt() expects ciphertext + tag concatenated
    plaintext = aesgcm.decrypt(iv, ciphertext + tag, None)

    return json.loads(plaintext.decode("utf-8"))
