"""Validated production launcher; local synthetic uvicorn remains supported."""
import argparse
import ipaddress

from .app import config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=config.PORT)
    args = parser.parse_args()
    if not ipaddress.ip_address(args.host).is_loopback:
        parser.error("backend transport must bind loopback behind the approved proxy")
    config.validate_deployment(check_launcher=False)
    config._transport_validated = True
    import uvicorn
    # Forwarding is handled by the application using the ORIGINAL socket peer.
    # The ASGI server must not rewrite it before the trust check.
    uvicorn.run("backend.app.main:app", host=args.host, port=args.port, workers=1, proxy_headers=False)


if __name__ == "__main__":
    main()
