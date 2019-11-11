import argparse
import asyncio
import logging
from mdb.cashless_slave import CashlessSlave
from mdb.master import Master
from mdb.peripherals import CoinAcceptor, BillValidator
from mdb.sniffer import Sniffer
import signal
from sys import exit
import time
from usb_handler import USBHandler, to_ascii
from websocket_client import WebsocketClient

logger = logging.getLogger()


is_shutting_down = False
_main_task = None


async def shutdown(master, cashless_slave, sniffer, usb_handler,
                   websocket_client, signame):
    global is_shutting_down
    if is_shutting_down:
        exit('Shutting down immediately.')
    else:
        is_shutting_down = True
    logger.info("Shutting down, got signal %s.", signame)
    await websocket_client.shutdown()
    await asyncio.gather(master.shutdown(), cashless_slave.shutdown())
    await sniffer.shutdown()
    await usb_handler.shutdown()
    if _main_task:
        _main_task.cancel()


async def _main(args):
    handler = USBHandler()
    master = Master()
    bill_validator = BillValidator()
    coin_acceptor = CoinAcceptor()
    cashless_slave = CashlessSlave()
    sniffer = Sniffer()
    websocket_client = WebsocketClient()
    loop = asyncio.get_running_loop()
    for signame in ('SIGINT', 'SIGTERM', 'SIGHUP'):
        loop.add_signal_handler(
            getattr(signal, signame),
            lambda: asyncio.create_task(shutdown(
                master, cashless_slave, sniffer, handler, websocket_client,
                signame=signame
            ))
        )
    runners = [master.run(), cashless_slave.run(), websocket_client.run()]
    try:
        logger.info('Initializing USB handler.')
        # Order of initialization matters here; USB Handler has to be first, in
        # case the users try sending strings in their initialization.
        await handler.initialize(args.device_path)
        # Have to get the USB Handler running now so all the MDB users can
        # communicate on the port.
        logger.info('Starting USB handler in new Task.')
        runners.append(asyncio.create_task(handler.run()))
        # Resets the MDB board
        logger.info('Resetting MDB board.')
        await handler.send(to_ascii('F,RESET\n'))
        # Lets the USB handler clear any remaining messages off the read queue.
        await asyncio.sleep(0)
        if args.sniff:
            # Get the sniffer up and running before everything else
            # MDB-related, so it can report everything.
            logger.info('Initializing MDB sniffer.')
            await sniffer.initialize(handler)
            logger.info('Starting MDB sniffer.')
            runners.append(asyncio.create_task(sniffer.run()))
        logger.info('Initializing the MDB objects and the websocket client.')
        await asyncio.gather(master.initialize(handler, bill_validator,
                                               coin_acceptor),
                             cashless_slave.initialize(handler),
                             websocket_client.initialize())
    except Exception as e:
        logger.critical("Unable to initialize the server, an error occurred.",
                        exc_info=e)
        logger.info("Shutting down websocket client, MDB sniffer, and USB "
                    "handler.")
        await asyncio.gather(sniffer.shutdown(), websocket_client.shutdown())
        await handler.shutdown()
        return
    logger.info('Starting the MDB objects and the websocket client.')
    try:
        await asyncio.gather(*runners)
    finally:
        await websocket_client.shutdown()
        await asyncio.gather(master.shutdown(), cashless_slave.shutdown())
        await sniffer.shutdown()
        await handler.shutdown()


# Easier to have asyncio manage the loop and just have this be a wrapper that
# creates a task the shutdown function can cancel.
async def main(args):
    _main_task = asyncio.create_task(_main(args))
    try:
        await _main_task
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
    log_level = logging.INFO
    if args.debug:
        log_level = logging.DEBUG
    logging.basicConfig(level=log_level,
                        format='%(asctime)s %(name)-20s %(levelname)-8s '
                        '%(message):',
                        datefmt='%H:%M:%S',
                        filename=f'/tmp/mdb_server-{time.time_ns()}.log',
                        filemode='w')
    logger.debug('Launching event loop.')
    asyncio.run(main(args), debug=args.debug)
