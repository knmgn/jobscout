"""Run the demo board by itself: `python -m jobscout.demo_board`."""

from __future__ import annotations

import argparse
import contextlib

from jobscout.demo_board.server import BoardServer, DemoBoard


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the demo listings board.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--expired",
        action="store_true",
        help="serve every feed as an expired session, to see the alert path",
    )
    args = parser.parse_args()

    server = BoardServer(DemoBoard(expired=args.expired), host=args.host, port=args.port)
    print(f"Demo board on {server.url}/feeds/newest  (Ctrl+C to stop)")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
