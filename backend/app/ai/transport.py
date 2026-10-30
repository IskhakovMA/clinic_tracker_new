"""Direct pinned connections: no ambient proxy, redirects or second DNS lookup."""
import http.client
import ipaddress
import re
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.error import URLError


@dataclass(frozen=True)
class Destination:
    scheme: str
    host: str
    port: int
    prefix: str

    @property
    def origin(self):
        host = '[' + self.host + ']' if ':' in self.host else self.host
        return f'{self.scheme}://{host}:{self.port}'


def normalize(url):
    if not isinstance(url, str) or not url.isascii() or any(c.isspace() for c in url) or '\\' in url or '%' in url:
        raise ValueError('invalid_destination')
    p = urlsplit(url)
    if p.scheme not in {'http', 'https'} or not p.hostname or p.username is not None or p.password is not None or p.query or p.fragment:
        raise ValueError('invalid_destination')
    host = p.hostname.lower()
    if host.endswith('.') or not re.fullmatch(r'[a-z0-9.:-]+', host):
        raise ValueError('invalid_destination')
    # IP spelling must be canonical. Reject inet_aton octal/hex/integer aliases.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if not any(c.isalpha() for c in host) or any(label.startswith('0x') for label in host.split('.')) or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in host.split('.')):
            raise ValueError('invalid_destination') from None
    else:
        if str(ip) != host or getattr(ip, 'ipv4_mapped', None) is not None:
            raise ValueError('invalid_destination')
    prefix = p.path.rstrip('/')
    if prefix and (not re.fullmatch(r'/[A-Za-z0-9_/-]+', prefix) or '//' in prefix or '..' in prefix):
        raise ValueError('invalid_destination')
    port = p.port if p.port is not None else (443 if p.scheme == 'https' else 80)
    if not 1 <= port <= 65535:
        raise ValueError('invalid_destination')
    return Destination(p.scheme, host, port, prefix)


def resolve(destination, *, local):
    records = socket.getaddrinfo(destination.host, destination.port, type=socket.SOCK_STREAM)
    if not records:
        raise ValueError('invalid_destination')
    for _, _, _, _, addr in records:
        ip = ipaddress.ip_address(addr[0])
        if getattr(ip, 'ipv4_mapped', None) is not None or (not ip.is_loopback if local else not ip.is_global):
            raise ValueError('forbidden_address')
    return records


class PinnedConnection(http.client.HTTPConnection):
    def __init__(self, destination, record, timeout):
        super().__init__(destination.host, destination.port, timeout=timeout)
        self.destination, self.record = destination, record

    def connect(self):
        family, socktype, protocol, _, address = self.record
        sock = socket.socket(family, socktype, protocol)
        try:
            sock.settimeout(self.timeout)
            sock.connect(address)
            if ipaddress.ip_address(sock.getpeername()[0]) != ipaddress.ip_address(address[0]):
                raise OSError('peer_mismatch')
            if self.destination.scheme == 'https':
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.destination.host)
            self.sock = sock
        except BaseException:
            sock.close()
            raise


class DirectOpener:
    def __init__(self, base_url):
        self.destination = normalize(base_url)

    def open(self, request, timeout):
        from .. import config
        d = self.destination
        target = normalize(request.full_url)
        if target.origin != d.origin or target.prefix not in {d.prefix + '/models', d.prefix + '/chat/completions'}:
            raise URLError('destination_mismatch')
        # No approved remote destination or data contract exists in this phase.
        # This gate is independent of AI_ENABLED and cannot be enabled for remote by env.
        if config.AI_DATA_MODE != 'synthetic' or config.APP_ENV not in {'local', 'test'} or d.host not in {'localhost', '127.0.0.1', '::1'}:
            raise URLError('data_transfer_not_approved')
        try:
            allowed = frozenset(normalize(url) for url in config.AI_SYNTHETIC_ALLOWLIST)
            if d not in allowed:
                raise ValueError('destination_not_allowlisted')
            record = sorted(resolve(d, local=True), key=lambda r: r[0] != socket.AF_INET)[0]
            conn = PinnedConnection(d, record, timeout)
            conn.request(request.get_method(), target.prefix, body=request.data, headers=dict(request.header_items()))
            response = conn.getresponse()
            if not 200 <= response.status < 300:
                response.close()
                conn.close()
                raise URLError('provider_response_refused')
            return _Response(response, conn)
        except (ValueError, OSError, http.client.HTTPException) as exc:
            raise URLError('provider_connection_refused') from exc


class _Response:
    def __init__(self, response, conn):
        self.response, self.conn = response, conn
    def __enter__(self):
        return self
    def read(self, size):
        return self.response.read(size)
    def __exit__(self, *args):
        self.response.close()
        self.conn.close()
