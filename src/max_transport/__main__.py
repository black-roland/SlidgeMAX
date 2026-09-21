# Copyright 2026 @black-roland
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from max_transport import __version__
from max_transport.config import load_config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="max-transport",
        description="XMPP external component that bridges MAX messenger.",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config.toml",
        help="path to TOML config (default: ./config.toml)",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="validate config and exit",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"max-transport {__version__}",
    )
    return parser


def _setup_logging(level: str, file_path: str) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if file_path:
        handlers.append(logging.FileHandler(file_path, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        handlers=handlers,
        force=True,
    )
    logging.getLogger("slixmpp").setLevel(logging.WARNING)


async def _run(config) -> None:
    from max_transport.component import MaxComponent

    component = MaxComponent(config)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    component.connect()
    try:
        await stop.wait()
    finally:
        await component.shutdown()


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config_path = Path(args.config)
    try:
        config = load_config(config_path)
    except (OSError, ValueError) as exc:
        print(f"max-transport: {exc}", file=sys.stderr)
        return 2
    if args.check_config:
        print(f"config ok: {config_path.resolve()}")
        print(f"  component {config.component.jid} -> {config.component.server}:{config.component.port}")
        print(f"  data_dir  {config.data_dir}")
        return 0
    _setup_logging(config.logging.level, config.logging.file)
    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
