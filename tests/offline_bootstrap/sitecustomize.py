"""Block network access in offline test processes and their Python children.

Opt in by prepending this directory to PYTHONPATH before starting Python.
An audit hook blocks real socket operations even when libraries retain their
own socket references. Injected test clients do not perform these operations.
"""

import ipaddress
import sys


def _loopback(address):
    host = address[0] if isinstance(address, tuple) else address
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


def _deny_network(event, args):
    # Windows implements asyncio's local wake-up socketpair with a TCP
    # loopback connection. Keep local IPC working without allowing DNS or
    # connections to AWS, providers, proxies or other external addresses.
    if event in {"socket.connect", "socket.sendto", "socket.sendmsg"}:
        if _loopback(args[1]):
            return
    elif event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
        if _loopback(args[0]):
            return
    if event in {
        "socket.connect",
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "socket.gethostbyaddr",
        "socket.sendto",
        "socket.sendmsg",
    }:
        raise RuntimeError(f"Offline test run blocked network operation: {event}")


sys.addaudithook(_deny_network)
