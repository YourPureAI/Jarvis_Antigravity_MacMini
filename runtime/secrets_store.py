"""Secret store encrypted with Windows DPAPI on Windows, or authenticated per-user encryption on POSIX/macOS.
Values are NEVER printed. Skill scripts receive them only as env vars via `agent.py run`."""
from __future__ import annotations

import base64
import ctypes
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
from ctypes import wintypes

from core import DATA_DIR, fail, out

STORE = DATA_DIR / "secrets.dpapi.json"
KEY_FILE = DATA_DIR / ".secret_vault.key"
NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _posix_key() -> bytes:
    if not KEY_FILE.exists():
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_EXCL", 0)
        fd = os.open(KEY_FILE, flags, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        return key
    return KEY_FILE.read_bytes()


def _posix_keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    stream = bytearray()
    counter = 0
    while len(stream) < length:
        h = hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        stream.extend(h)
        counter += 1
    return bytes(stream[:length])


def _posix_crypt(data: bytes, protect: bool) -> bytes:
    key = _posix_key()
    if protect:
        nonce = secrets.token_bytes(16)
        ks = _posix_keystream(key, nonce, len(data))
        cipher = bytes(b ^ k for b, k in zip(data, ks))
        mac = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
        return b"POSX:" + nonce + mac + cipher
    else:
        if not data.startswith(b"POSX:"):
            raise ValueError("Corrupted or incompatible secret blob")
        raw = data[5:]
        if len(raw) < 48:
            raise ValueError("Invalid secret payload length")
        nonce, mac, cipher = raw[:16], raw[16:48], raw[48:]
        expected_mac = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected_mac):
            raise ValueError("Secret decryption integrity check failed")
        ks = _posix_keystream(key, nonce, len(cipher))
        return bytes(b ^ k for b, k in zip(cipher, ks))


def _crypt(data: bytes, protect: bool) -> bytes:
    if sys.platform == "win32":
        crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
        src, dst = _blob(data), _Blob()
        fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
        if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(dst)):  # 0x1 = UI_FORBIDDEN
            raise ctypes.WinError()
        try:
            return ctypes.string_at(dst.pbData, dst.cbData)
        finally:
            kernel32.LocalFree(dst.pbData)
    else:
        return _posix_crypt(data, protect)


def _load() -> dict:
    return json.loads(STORE.read_text(encoding="utf-8")) if STORE.exists() else {}


def _save(d: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(d, indent=1), encoding="utf-8")
    if sys.platform != "win32" and STORE.exists():
        try:
            os.chmod(STORE, 0o600)
        except OSError:
            pass


def get_secret(name: str) -> str | None:
    """Internal use only (runner injection). Never print the result."""
    enc = _load().get(name)
    return _crypt(base64.b64decode(enc), False).decode("utf-8") if enc else None


def _set(a):
    if not NAME_RE.match(a.name):
        fail("secret name must be UPPER_SNAKE_CASE")
    if a.stdin:
        value = sys.stdin.readline().rstrip("\r\n")
    else:
        value = getpass.getpass(f"Value for {a.name} (input hidden): ")
    if not value:
        fail("empty value")
    d = _load()
    d[a.name] = base64.b64encode(_crypt(value.encode("utf-8"), True)).decode()
    _save(d)
    out({"ok": True, "stored": a.name})


def _list(_a):
    out({"ok": True, "names": sorted(_load())})


def _has(a):
    out({"ok": True, "name": a.name, "exists": a.name in _load()})


def _rm(a):
    d = _load()
    existed = d.pop(a.name, None) is not None
    _save(d)
    out({"ok": True, "removed": existed})


def register(sub) -> None:
    p = sub.add_parser("secret", help="DPAPI-encrypted secret store (values never printed)")
    s = p.add_subparsers(dest="action", required=True)
    st = s.add_parser("set", help="interactive hidden prompt; --stdin to pipe a value")
    st.add_argument("name")
    st.add_argument("--stdin", action="store_true")
    st.set_defaults(fn=_set)
    s.add_parser("list").set_defaults(fn=_list)
    h = s.add_parser("has")
    h.add_argument("name")
    h.set_defaults(fn=_has)
    r = s.add_parser("rm")
    r.add_argument("name")
    r.set_defaults(fn=_rm)

