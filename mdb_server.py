from abc import ABC, abstractmethod
import argparse
import asyncio
import logging
from .usb_handler import USBHandler, to_ascii
import websockets


logger = logging.getLogger(__name__)


class Peripheral(ABC):
    """Generic implementation of an MDB peripheral interface."""
    def __init__(self):
        self.lock = asyncio.Lock()
        self.initialized = False

    @abstractmethod
    async def initialize(self, master, send_reset=True):
        """Initialization code for the peripheral.

        Feel free to send messages here, the USB interfact will be initialized
        before this is called.

        :param master: the Master instance controlling this peripheral
        :param send_reset: whether to send a reset command or not"""
        logger.debug("Initializing peripheral of type "
                     f"{self.__class__.__name__}.")
        self.initialized = True
        self.master = master

    async def send(self, message):
        async with self.lock:
            await self.master.send(message)

    async def sendread(self, message, prefix):
        async with self.lock:
            return await self.master.sendread(message, prefix)

    @abstractmethod
    async def enable(self):
        """Enables the peripheral for vending activities."""
        pass

    @abstractmethod
    async def disable(self):
        """Disables the peripheral for vending activities."""
        pass

    @abstractmethod
    async def run(self):
        """Does whatever persistent action is needed."""
        assert self.initialized


class BillValidator(Peripheral):
    pass


class CoinAcceptor(Peripheral):
    pass


class Master:
    pass


class Sniffer:
    def __init__(self):
        self.initialized = False

    async def initialize(self, usb_handler):
        logger.debug("Initializing MDB sniffer.")
        self.initialized = True
        self.usb_handler = usb_handler
        status = await usb_handler.sendread(to_ascii('X,1'), 'x')
        if status != 'x,ACK':
            logger.error(f"Unable to start MDB sniffer, got {status}")
        else:
            logger.debug("Sniffer initialized")
        self.message_queue = usb_handler.listen('x')

    async def run(self):
        assert self.initialized
        while True:
            message = await self.message_queue.get()
            message = message.split(',')[1:]
            logger.debug(f"Message sniffed: {message}")


class CashlessSlave:
    pass


class WebsocketClient:
    pass


async def main(args):
    handler = USBHandler()
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
