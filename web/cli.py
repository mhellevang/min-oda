"""Console-entrypoint for `uv run min-oda`.

Registrert i pyproject.toml som [project.scripts] min-oda = "web.cli:run".
"""

from __future__ import annotations

import argparse


def run() -> None:
    """Start uvicorn med web.main:app — wrapped slik at pyproject-entry
    kan kalle den uten å eksponere uvicorn-flagg direkte."""
    parser = argparse.ArgumentParser(description="Start Oda web-appen på localhost.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-reload", action="store_true",
                        help="Skru av auto-reload ved kodeendring")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        "web.main:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
    )


if __name__ == "__main__":
    run()
