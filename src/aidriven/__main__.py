"""Entry point: `python -m aidriven` or the `aidriven` console script."""

from __future__ import annotations

import os

SSL_ENV_VARS = ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")


def sanitize_ssl_env() -> list[str]:
    """Drop certificate variables that point to missing paths (e.g. a conda `base` env exporting a
    non-existent cacert.pem): SDKs would fail with FileNotFoundError before any request. Without them
    httpx uses the OS trust store. Returns the names that were removed."""
    removed = []
    for name in SSL_ENV_VARS:
        value = os.environ.get(name)
        if value and not os.path.exists(value):
            os.environ.pop(name)
            removed.append(f"{name}={value}")
    return removed


def main() -> None:
    removed = sanitize_ssl_env()
    from aidriven.app import run

    if removed:
        import logging

        logging.getLogger("aidriven").warning(
            "ignored certificate variables pointing to missing files (using the OS trust store): %s", "; ".join(removed)
        )
    run()


if __name__ in {"__main__", "__mp_main__"}:
    main()
