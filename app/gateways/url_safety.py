import socket
from collections.abc import Callable, Iterable
from ipaddress import IPv4Address, IPv6Address, ip_address
from urllib.parse import urlparse


class BaseUrlSafetyError(ValueError):
    pass


AddressResolver = Callable[[str], Iterable[str]]


def validate_https_base_url(
    base_url: str,
    *,
    trusted_public_hosts: set[str] | None = None,
    resolve_dns: bool = True,
    resolver: AddressResolver | None = None,
) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise BaseUrlSafetyError("base_url must be an https URL")
    if parsed.username or parsed.password:
        raise BaseUrlSafetyError("base_url must not contain credentials")
    if parsed.query or parsed.fragment:
        raise BaseUrlSafetyError("base_url must not contain query or fragment")

    hostname = parsed.hostname.lower()
    trusted_hosts = {host.lower() for host in (trusted_public_hosts or set())}
    _reject_unsafe_hostname(hostname)
    if resolve_dns and hostname not in trusted_hosts:
        addresses = list((resolver or _resolve_hostname)(hostname))
        if not addresses:
            raise BaseUrlSafetyError("base_url host did not resolve")
        for address in addresses:
            _reject_unsafe_address(address)
    return base_url.rstrip("/")


def _resolve_hostname(hostname: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise BaseUrlSafetyError("base_url host could not be resolved") from exc
    return sorted({item[4][0] for item in infos})


def _reject_unsafe_hostname(hostname: str) -> None:
    if hostname == "localhost" or hostname.endswith(".local"):
        raise BaseUrlSafetyError("base_url host is not allowed")
    try:
        address = ip_address(hostname)
    except ValueError:
        return
    _reject_unsafe_ip(address)


def _reject_unsafe_address(value: str) -> None:
    try:
        address = ip_address(value)
    except ValueError as exc:
        raise BaseUrlSafetyError("base_url host resolved to an invalid address") from exc
    _reject_unsafe_ip(address)


def _reject_unsafe_ip(address: IPv4Address | IPv6Address) -> None:
    if (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    ):
        raise BaseUrlSafetyError("base_url host is not allowed")
