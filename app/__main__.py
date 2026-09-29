"""Start the server:  python -m app

Listens on BOTH 127.0.0.1 and ::1, port 8000 (or $PORT). Why: "localhost"
resolves to the IPv6 address ::1 first on many systems. `uvicorn --port 8000`
listens only on IPv4, so on Windows every new connection to
http://localhost:8000 first waits ~2 s for the IPv6 attempt to fail. With
both addresses open, localhost connects at once.
"""

import os
import socket

import uvicorn


def _listen(family: int, host: str, port: int) -> socket.socket:
    sock = socket.socket(family, socket.SOCK_STREAM)
    if family == socket.AF_INET6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    if os.name != "nt":  # on Windows SO_REUSEADDR would let two servers share the port
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    return sock


def main() -> None:
    port = int(os.getenv("PORT", "8000"))
    sockets = [_listen(socket.AF_INET, "127.0.0.1", port)]
    if socket.has_ipv6:
        try:
            sockets.append(_listen(socket.AF_INET6, "::1", port))
        except OSError:
            pass  # IPv6 disabled on this machine: IPv4 alone is fine
    print(f"CatalogIQ on http://localhost:{port}")
    uvicorn.Server(uvicorn.Config("app.main:app")).run(sockets=sockets)


if __name__ == "__main__":
    main()
