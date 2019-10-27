import argparse
import asyncio
import logging
from mdb.cashless_slave import CashlessSlave
from mdb.master import Master
from mdb.peripherals import CoinAcceptor, BillValidator
from mdb.sniffer import Sniffer
from usb_handler import USBHandler
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
    master = Master()
    bill_validator = BillValidator()
    coin_acceptor = CoinAcceptor()
    init_tasks = []
    init_tasks.append(asyncio.create_task(master.initialize(handler,
                                                            bill_validator,
                                                            coin_acceptor)))
    done, _ = asyncio.wait(init_tasks, return_when=asyncio.FIRST_EXCEPTION)
    bad_initialization = False
    for t in done:
        if t.exception():
            bad_initialization = True
            logger.critical("Encountered an error initializing a component.",
                            exc_info=t.exception())
    if bad_initialization:
        raise RuntimeError("Unable to initialize the server, see logs for "
                           "details.")
    run_tasks = []
    run_tasks.append(asyncio.create_task(handler.run()))
    run_tasks.append(asyncio.create_task(sniffer.run()))
    done, _ = asyncio.wait(run_tasks, return_when=asyncio.FIRST_EXCEPTION)
    for t in done:
        if t.exception():
            logger.critical("Encountered an unhandled exception while running"
                            "the server.", exc_info=t.exception())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Starts the ChezBob MDB server.")
    parser.add_argument('--device_path', help="Location of the MDB board's "
                        "device file. Defaults to '/dev/ttyUSB0'.",
                        default='/dev/ttyUSB0', type=str)
    parser.add_argument("--sniff", help="Enable the packet sniffer for "
                        "debugging.", action='store_true')
    asyncio.run(main(parser.parse_args()))
