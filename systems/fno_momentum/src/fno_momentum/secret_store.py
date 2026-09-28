"""Windows Credential Manager storage for the Upstox Analytics Token.

The token is never accepted on a command line or through an environment variable.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import getpass
import sys


UPSTOX_CREDENTIAL_TARGET = "AdityaResearch/UpstoxAnalyticsToken"
_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168
_MAX_CREDENTIAL_BLOB_BYTES = 2560
_UTF8_MARKER = b"UTF8\x00"


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _encode_secret(value: str) -> bytes:
    encoded = _UTF8_MARKER + value.encode("utf-8")
    if len(encoded) > _MAX_CREDENTIAL_BLOB_BYTES:
        raise ValueError(
            "token exceeds the Windows Credential Manager 2560-byte limit"
        )
    return encoded


def _decode_secret(raw: bytes) -> str:
    if raw.startswith(_UTF8_MARKER):
        return raw[len(_UTF8_MARKER) :].decode("utf-8").strip()
    # Backward compatibility for any credential stored by the first local implementation.
    return raw.decode("utf-16-le").strip()


def _credential_api():
    if sys.platform != "win32":
        raise RuntimeError("the approved OS secret store requires Windows")
    api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    api.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)),
    ]
    api.CredReadW.restype = wintypes.BOOL
    api.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    return api


def read_upstox_analytics_token() -> str:
    """Read the token in-process without printing, logging, or persisting it elsewhere."""
    api = _credential_api()
    pointer = ctypes.POINTER(_CREDENTIALW)()
    if not api.CredReadW(
        UPSTOX_CREDENTIAL_TARGET,
        _CRED_TYPE_GENERIC,
        0,
        ctypes.byref(pointer),
    ):
        error = ctypes.get_last_error()
        if error == _ERROR_NOT_FOUND:
            raise RuntimeError(
                "Upstox Analytics Token is not present in Windows Credential Manager"
            )
        raise OSError(error, "Windows Credential Manager read failed")
    try:
        credential = pointer.contents
        if credential.CredentialBlobSize == 0:
            raise RuntimeError("stored Upstox Analytics Token is empty")
        raw = ctypes.string_at(
            credential.CredentialBlob, credential.CredentialBlobSize
        )
        token = _decode_secret(raw)
        if not token:
            raise RuntimeError("stored Upstox Analytics Token is empty")
        return token
    finally:
        api.CredFree(pointer)


def has_upstox_analytics_token() -> bool:
    try:
        read_upstox_analytics_token()
    except RuntimeError as exc:
        if "not present" in str(exc):
            return False
        raise
    return True


def store_upstox_analytics_token(token: str) -> None:
    """Persist a token as a Windows generic credential encrypted for this user."""
    value = token.strip()
    if not value:
        raise ValueError("token cannot be empty")
    encoded = _encode_secret(value)
    blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
    credential = _CREDENTIALW()
    credential.Type = _CRED_TYPE_GENERIC
    credential.TargetName = UPSTOX_CREDENTIAL_TARGET
    credential.Comment = "Read-only Upstox Analytics Token for controlled verification"
    credential.CredentialBlobSize = len(encoded)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = _CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = "aditya-lakhotia"
    api = _credential_api()
    if not api.CredWriteW(ctypes.byref(credential), 0):
        error = ctypes.get_last_error()
        raise OSError(error, "Windows Credential Manager write failed")


def prompt_and_store() -> None:
    token = getpass.getpass("Paste the Upstox Analytics Token (input is hidden): ")
    store_upstox_analytics_token(token)
    print("Token stored in Windows Credential Manager; token value was not displayed.")


if __name__ == "__main__":
    prompt_and_store()
