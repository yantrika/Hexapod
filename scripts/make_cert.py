#!/usr/bin/env python3
"""Make the self-signed certificate for the phone page's HTTPS (Step 12c).

    python scripts/make_cert.py                 run it ON THE PI; writes data/tls/hexa-*.pem
    python scripts/make_cert.py --force         replace an existing certificate (new address)
    python scripts/make_cert.py --name hexa.local --ip 192.168.1.50

A phone browser opens the microphone only on a secure page, so ``main.py --https`` serves the page
with this certificate. It is self-signed, so the phone shows a one-time "not secure" warning:
accept it once. The key and certificate stay in ``data/tls/`` (gitignored, never synced to or from
the Pi). The certificate names this machine's host name, ``hexa.local``, ``localhost`` and its LAN
addresses; run it again with ``--force`` if the Pi's address changes. Needs the ``openssl``
command (the Pi has it). Exit 0: written. Exit 1: not written (the reason is printed).
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402


def alt_names(host_names: Sequence[str], addresses: Sequence[str]) -> str:
    """The ``subjectAltName`` value: DNS names first, then IP addresses, no duplicates."""
    entries: list[str] = []
    for name in host_names:
        entry = f"DNS:{name}"
        if name and entry not in entries:
            entries.append(entry)
    for address in addresses:
        entry = f"IP:{address}"
        if address and entry not in entries:
            entries.append(entry)
    return ",".join(entries)


def openssl_command(openssl: str, cert: Path, key: Path, common_name: str, san: str,
                    days: int) -> list[str]:
    return [openssl, "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", str(days),
            "-subj", f"/CN={common_name}", "-addext", f"subjectAltName={san}"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cert", type=Path, default=config.WEB_TLS_CERT)
    parser.add_argument("--key", type=Path, default=config.WEB_TLS_KEY)
    parser.add_argument("--name", action="append", default=[], help="extra host name (repeatable)")
    parser.add_argument("--ip", action="append", default=[], help="extra IP address (repeatable)")
    parser.add_argument("--days", type=int, default=config.WEB_TLS_DAYS)
    parser.add_argument("--force", action="store_true", help="replace an existing certificate")
    args = parser.parse_args(argv)

    openssl = shutil.which("openssl")
    if openssl is None:
        print("error: the `openssl` command is not installed (the Pi has it; on Windows use "
              "Git Bash's)")
        return 1
    if (args.cert.exists() or args.key.exists()) and not args.force:
        print(f"{args.cert} already exists: nothing changed (use --force to replace it)")
        return 1

    from web.server import lan_addresses

    names = [socket.gethostname(), "hexa.local", "localhost", *args.name]
    addresses = ["127.0.0.1", *lan_addresses(), *args.ip]
    san = alt_names(names, addresses)
    args.cert.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        openssl_command(openssl, args.cert, args.key, "hexa.local", san, args.days),
        capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"error: openssl failed:\n{result.stderr.strip()}")
        return 1
    try:
        os.chmod(args.key, 0o600)  # the private key is for this user only (a no-op on Windows)
    except OSError:
        pass
    print(f"wrote {args.cert} and {args.key} (valid {args.days} days)")
    print(f"names: {san}")
    print("start the robot with: python main.py --lan --https --no-mic")
    print("the phone shows a one-time warning for this certificate: accept it once")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
