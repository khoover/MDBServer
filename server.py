import argparse
import asyncio
import logging
from mdb.cashless_slave import CashlessSlave
from mdb.master import Master
from mdb.peripherals import CoinAcceptor, BillValidator
from mdb.sniffer import Sniffer
from usb_handler import USBHandler, to_ascii
from websocket_client import WebsocketClient

logging.basicConfig(level=logging.DEBUG, filename='server.log')
logger = logging.getLogger()


async def main(args):
    handler = USBHandler()
    master = Master()
    bill_validator = BillValidator()
    coin_acceptor = CoinAcceptor()
    cashless_slave = CashlessSlave()
    websocket_client = WebsocketClient()
    runners = [master.run(), cashless_slave.run(), websocket_client.run()]
    try:
        # Order of initialization matters here; USB Handler has to be first, in
        # case the users try sending strings in their initialization.
        await handler.initialize(args.device_path)
        # Have to get the USB Handler running now so all the MDB users can
        # communicate on the port.
        runners.append(asyncio.create_task(handler.run()))
        # Resets the MDB board
        await handler.send(to_ascii('F,RESET'))
        if args.sniff:
            # Get the sniffer up and running before everything else
            # MDB-related, so it can report everything.
            sniffer = Sniffer()
            await sniffer.initialize(handler)
            runners.append(asyncio.create_task(sniffer.run()))
        await asyncio.gather(master.initialize(handler, bill_validator,
                                               coin_acceptor),
                             cashless_slave.initialize(handler),
                             websocket_client.initialize())
    except Exception as e:
        logger.critical("Unable to initialize the server, an error occurred.",
                        exc_info=e)
        return
    try:
        await asyncio.gather(*runners)
    except Exception as e:
        logger.critical("Encountered an unhandled exception while running the"
                        " server, exiting.", exc_info=e)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Starts the ChezBob MDB server.")
    parser.add_argument('--device_path', help="Location of the MDB board's "
                        "device file. Defaults to '/dev/ttyUSB0'.",
                        default='/dev/ttyUSB0', type=str)
    parser.add_argument("--sniff", help="Enable the packet sniffer for "
                        "debugging.", action='store_true')
    parser.add_argument("--debug", help="Run the server in debug mode.",
                        action='store_true')
    args = parser.parse_args()
    logger.debug('Launching event loop.')
    asyncio.run(main(args), debug=args.debug)
