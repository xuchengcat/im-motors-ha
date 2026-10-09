"""Offline x-encrypt=true codec recovered from pu.a in App 3.2.4.

Requires PyCryptodome. No network, device access, stored credentials, or logging.
The caller supplies synthetic/configured key text. This is unauthenticated AES;
successful decoding does not establish response authenticity.
Legacy encrypt/csop branches are deliberately outside this codec.
"""
import base64
import binascii
import json

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad


def derive_key(key_text: str) -> bytes:
    """Copy the first 16 UTF-8 bytes, padding shorter keys with ASCII spaces."""
    if not isinstance(key_text, str):
        raise TypeError("Key must be text")
    return key_text.encode("utf-8")[:16].ljust(16, b" ")


def encrypt_text(plaintext: str, key_text: str) -> str:
    if not isinstance(plaintext, str):
        raise TypeError("Plaintext must be text")
    cipher = AES.new(derive_key(key_text), AES.MODE_ECB)
    encrypted = cipher.encrypt(pad(plaintext.encode("utf-8"), AES.block_size))
    return base64.b64encode(encrypted).decode("ascii")


def decrypt_text(ciphertext: str, key_text: str) -> str:
    """Decode standard Base64, tolerating ASCII whitespace like Android DEFAULT.

    Fail closed on malformed input instead of the App's plaintext fallback.
    Exceptions contain no key, ciphertext, or plaintext values.
    """
    if not isinstance(ciphertext, str):
        raise TypeError("Ciphertext must be text")
    key = derive_key(key_text)
    try:
        compact = ciphertext.translate(str.maketrans("", "", " \t\r\n"))
        encrypted = base64.b64decode(compact, validate=True)
        if not encrypted or len(encrypted) % AES.block_size:
            raise ValueError
        decrypted = AES.new(key, AES.MODE_ECB).decrypt(encrypted)
        return unpad(decrypted, AES.block_size).decode("utf-8")
    except (ValueError, UnicodeError, binascii.Error):
        raise ValueError("Invalid encrypted payload") from None


def encrypted_request_body(plaintext: str, key_text: str) -> str:
    """Return the compact Gson-compatible envelope for signing the final body."""
    return json.dumps({"cipherText": encrypt_text(plaintext, key_text)}, separators=(",", ":"))


def decrypted_response_body(wire_body: str, key_text: str) -> str:
    """Unwrap root cipherText only; call after confirming x-encrypt=true."""
    try:
        envelope = json.loads(wire_body)
    except (TypeError, ValueError):
        raise ValueError("Invalid encrypted response envelope") from None
    if not isinstance(envelope, dict) or not isinstance(envelope.get("cipherText"), str):
        raise ValueError("Invalid encrypted response envelope")
    return decrypt_text(envelope["cipherText"], key_text)
