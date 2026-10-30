"""Production transport admission using the unmodified ASGI socket peer."""
import ipaddress

from starlette.responses import JSONResponse

from . import config


class ProductionTransport:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and config.APP_ENV == "production":
            try:
                config.validate_deployment()
                peer = ipaddress.ip_address(scope["client"][0])
                trusted = any(peer in ipaddress.ip_network(value.strip()) for value in config.TRUSTED_PROXY_IPS.split(","))
                headers = scope.get("headers", [])
                proto = [v for k, v in headers if k == b"x-forwarded-proto"]
                forwarded = [v for k, v in headers if k == b"x-forwarded-for"]
                if not trusted or proto != [b"https"] or len(forwarded) != 1:
                    raise ValueError("untrusted transport")
                address = ipaddress.ip_address(forwarded[0].decode("ascii"))
            except (config.ConfigError, ValueError, TypeError, KeyError):
                await JSONResponse({"error": "Secure transport required"}, status_code=503)(scope, receive, send)
                return
            # Preserve the request state object seen by outer security-header
            # middleware (X-Clinic-User / X-Clinic-Access).
            scope["scheme"] = "https"
            scope["client"] = (str(address), 0)
        await self.app(scope, receive, send)
