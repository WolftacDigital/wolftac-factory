"""Real Windows AF_UNIX special-entry fixture (CPython omits AF_UNIX)."""
import ctypes
import os
from pathlib import Path
import sys


def main():
    root = Path(os.environ["TEMP"]).resolve(strict=True)
    assert Path.cwd().resolve().is_relative_to(root)
    ws = ctypes.WinDLL("ws2_32", use_last_error=True)
    ws.WSAStartup.argtypes = [ctypes.c_ushort, ctypes.c_void_p]
    ws.WSAStartup.restype = ctypes.c_int
    ws.WSASocketW.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                            ctypes.c_uint32, ctypes.c_uint32]
    ws.WSASocketW.restype = ctypes.c_size_t
    ws.bind.argtypes = [ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int]
    ws.bind.restype = ctypes.c_int
    ws.closesocket.argtypes = [ctypes.c_size_t]
    ws.closesocket.restype = ctypes.c_int
    data = ctypes.create_string_buffer(512)
    assert ws.WSAStartup(0x202, data) == 0
    handle = ws.WSASocketW(1, 1, 0, None, 0, 0)  # AF_UNIX, SOCK_STREAM
    try:
        assert handle != ctypes.c_size_t(-1).value, ws.WSAGetLastError()
        class Address(ctypes.Structure):
            _fields_ = [("family", ctypes.c_ushort), ("path", ctypes.c_char * 108)]
        address = Address(1, b"special.sock")
        assert ws.bind(handle, ctypes.byref(address), ctypes.sizeof(address)) == 0, ws.WSAGetLastError()
        print(os.getpid(), flush=True)
        assert sys.stdin.readline().strip() == "exit"
    finally:
        if handle != ctypes.c_size_t(-1).value:
            assert ws.closesocket(handle) == 0
        assert ws.WSACleanup() == 0


if __name__ == "__main__":
    main()
