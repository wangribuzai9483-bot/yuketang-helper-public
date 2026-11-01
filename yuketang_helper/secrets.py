"""Windows DPAPI-protected API keys, bound to their configured endpoint."""
import ctypes
from ctypes import wintypes
import hashlib
from pathlib import Path

from .config import RUNTIME


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def protect(data: bytes, entropy: bytes, decrypt=False):
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    buffer = ctypes.create_string_buffer(data)
    salt = ctypes.create_string_buffer(entropy)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    extra = Blob(len(entropy), ctypes.cast(salt, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, ctypes.byref(extra), None, None, 1, ctypes.byref(output)):
        raise ValueError("无法读取或保存本机加密密钥，请重新填写 API Key。")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


class KeyStore:
    def __init__(self, folder: Path = RUNTIME / "keys"):
        self.folder = folder

    def _path(self, endpoint):
        return self.folder / (hashlib.sha256(endpoint.encode()).hexdigest() + ".bin")

    def save(self, endpoint, key):
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._path(endpoint)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(protect(key.encode("utf-8"), endpoint.encode()))
        temporary.replace(path)

    def load(self, endpoint):
        path = self._path(endpoint)
        return protect(path.read_bytes(), endpoint.encode(), decrypt=True).decode("utf-8") if path.exists() else ""
