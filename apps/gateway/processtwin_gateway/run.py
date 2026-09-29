from __future__ import annotations

import asyncio
import logging
import signal

from .runtime import GatewayRuntime


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    runtime = GatewayRuntime()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for name in ("SIGINT", "SIGTERM"):
        signal_name = getattr(signal, name, None)
        if signal_name is not None:
            try:
                loop.add_signal_handler(signal_name, runtime.stop)
            except NotImplementedError:
                pass
    try:
        loop.run_until_complete(runtime.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
