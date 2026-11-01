"""A tiny program that SERVES, for the v1.422.0 tests (B4): it listens on a
free TCP port of 127.0.0.1, writes ``<pid> <port>`` to the file named by its
first argument, and waits until it is killed. Every other argument is ignored
(a test passes a pretend key to prove the command line is masked).

With ``--child <file>`` it is a pretend coding TOOL instead: it starts the
server as its own child (a real parent link), listens itself too (a tool's
own port never counts), writes ``<pid> <port>`` to its own file, and waits.
"""

import os
import socket
import subprocess
import sys
import time


def serve(out: str) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(f"{os.getpid()} {s.getsockname()[1]}")
    os.replace(tmp, out)
    return s


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--child":
        own, child_out = args[1], args[2]
        sock = serve(own)
        subprocess.Popen([sys.executable, __file__, child_out])  # noqa: S603 — a test fixture
    else:
        sock = serve(args[0])
    try:
        while True:
            time.sleep(0.5)
    finally:
        sock.close()


if __name__ == "__main__":
    main()
