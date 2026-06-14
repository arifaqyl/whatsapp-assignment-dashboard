#!/usr/bin/env python3
import os

from ops_console.app import create_app


def main():
    app = create_app()
    host = os.getenv("OPS_CONSOLE_HOST", "127.0.0.1")
    port = int(os.getenv("OPS_CONSOLE_PORT", "8091"))
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
