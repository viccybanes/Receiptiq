"""Windows-user protected Google key; deliberately outside backup/settings manifests."""

import ctypes
import os
import re
from ctypes import wintypes

KEY_FILE = "google-api-key.dpapi"


def _protect(data, *, decrypt=False):
    if os.name != "nt":
        raise ValueError("Saving Google API keys requires the Windows desktop app.")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN; no machine-wide flag, so only this Windows user can decrypt.
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ValueError("Windows could not unlock the Google API key. Save it again in Settings.")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel32.LocalFree(output.data)


def save_google_key(root, key):
    key = key.strip()
    # Authorization keys issued by AI Studio use an AQ. prefix.
    if not re.fullmatch(r"[A-Za-z0-9._-]{20,256}", key):
        raise ValueError("Enter a valid Google AI Studio API key.")
    encrypted = _protect(key.encode("utf-8"))
    temporary = root / (KEY_FILE + ".tmp")
    try:
        temporary.write_bytes(encrypted)
        temporary.replace(root / KEY_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def load_google_key(root):
    path = root / KEY_FILE
    if not path.is_file():
        raise ValueError("Save a Google AI Studio API key in Settings before using Google vision.")
    try:
        return _protect(path.read_bytes(), decrypt=True).decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError("The Google API key is unavailable. Save it again in Settings.") from exc
