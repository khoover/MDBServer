import argparse
import asyncio
import logging
from mdb import Sniffer, Master, BillValidator, CoinAcceptor, CashlessSlave
from usb_handler import USBHandler, to_ascii
import websockets

logger = logging.getLogger(__name__)


class WebsocketClient:
    pass


async def main(args):
    handler = USBHandler()
    # Order of initialization matters here; USB Handler has to be first, in
    # case the users try sending strings in their initialization.
    await handler.initialize(args.device_path)
    if args.sniff:
        sniffer = Sniffer()
        await sniffer.initialize(handler)
    run_tasks = []
    run_tasks.append(asyncio.create_task(handler.run()))
    run_tasks.append(asyncio.create_task(sniffer.run()))
    asyncio.wait(run_tasks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Starts the ChezBob MDB server.")
    parser.add_argument('--device_path', help="Location of the MDB board's "
                        "device file. Defaults to '/dev/ttyUSB0'.",
                        default='/dev/ttyUSB0', type=str)
    parser.add_argument("--sniff", help="Enable the packet sniffer for "
                        "debugging.", action='store_true')
    asyncio.run(main(parser.parse_args()))
