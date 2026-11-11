"""Credential-free scratch Git filter, used only by S1 execution controls."""
import os
from pathlib import Path
import sys


def packet(data):
    sys.stdout.buffer.write(f"{len(data) + 4:04x}".encode("ascii") + data)


def flush():
    sys.stdout.buffer.write(b"0000")
    sys.stdout.buffer.flush()


def section():
    chunks = []
    while True:
        header = sys.stdin.buffer.read(4)
        if not header:
            return None
        assert len(header) == 4
        size = int(header, 16)
        if size == 0:
            return chunks
        assert size >= 4
        data = sys.stdin.buffer.read(size - 4)
        assert len(data) == size - 4
        chunks.append(data)


def main():
    mode, target = sys.argv[1:]
    marker = Path(target).absolute()
    root = Path(os.environ["TEMP"]).resolve(strict=True)
    assert marker.is_relative_to(root)
    marker.write_text(str(os.getpid()), encoding="ascii")
    if mode != "process":
        assert mode in ("clean", "smudge")
        sys.stdout.buffer.write(sys.stdin.buffer.read())
        return
    assert section() == [b"git-filter-client\n", b"version=2\n"]
    packet(b"git-filter-server\n")
    packet(b"version=2\n")
    flush()
    capabilities = section()
    assert capabilities is not None
    for capability in (b"capability=clean\n", b"capability=smudge\n"):
        assert capability in capabilities
        packet(capability)
    flush()
    while (headers := section()) is not None:
        assert any(line in (b"command=clean\n", b"command=smudge\n") for line in headers)
        contents = section()
        assert contents is not None
        packet(b"status=success\n")
        flush()
        for content in contents:
            packet(content)
        flush()
        flush()


if __name__ == "__main__":
    main()
