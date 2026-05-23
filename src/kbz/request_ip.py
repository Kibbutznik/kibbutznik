"""Best-effort client-IP resolution for rate limiting.

Honors X-Forwarded-For ONLY when the immediate peer is in
`settings.trusted_proxy_cidrs` (loopback by default; nginx in prod).
Otherwise an attacker hitting FastAPI directly could spoof their IP and
slip past per-IP limits. Mirrors the logic in routers/auth.py:_client_ip
— factored here so new rate-limited endpoints (contact, …) share one
trustworthy implementation.
"""

from __future__ import annotations

import ipaddress

from fastapi import Request

from kbz.config import settings


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else None
    if peer:
        try:
            peer_ip = ipaddress.ip_address(peer)
            for cidr in (settings.trusted_proxy_cidrs or "").split(","):
                cidr = cidr.strip()
                if not cidr:
                    continue
                try:
                    if peer_ip in ipaddress.ip_network(cidr, strict=False):
                        xff = request.headers.get("x-forwarded-for")
                        if xff:
                            return xff.split(",", 1)[0].strip()
                        break
                except ValueError:
                    continue
        except ValueError:
            pass
    return peer or "unknown"
