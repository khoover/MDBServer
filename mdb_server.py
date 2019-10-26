from abc import ABC, abstractmethod
import argparse
import asyncio
import logging
from .usb_handler import USBHandler, to_ascii
import websockets


logger = logging.getLogger(__name__)


class Peripheral(ABC):
    def __init__(self):
        self.lock = asyncio.Lock()
        self.initialized = False

    @abstractmethod
    async def initialize(self, master, send_reset=True):
        logger.debug("Initializing peripheral of type "
                     f"{self.__class__.__name__}.")
        self.initialized = True
        self.master = master

    async def send(self, message):
        await self.master.send(message)

    @abstractmethod
    async def enable(self):
        pass

    @abstractmethod
    async def disable(self):
        pass


class BillValidator(Peripheral):
    pass


class CoinAcceptor(Peripheral):
    pass


class Master:
    pass


class Sniffer:
    def __init__(self):
        pass

    async def initialize(self, usb_handler):
        self.usb_handler = usb_handler
        status = await usb_handler.sendread(to_ascii('X,1'), 'x')
        if status != 'x,ACK':
            logger.error(f"Unable to start MDB sniffer, got {status}")
        self.message_queue = usb_handler.read('x')

    async def run(self):
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Starts the ChezBob MDB server.")
    parser.add_argument('--device_path', help="Location of the MDB board's "
                        "device file. Defaults to '/dev/ttyUSB0'.",
                        default='/dev/ttyUSB0', type=str)
    parser.add_argument("--sniff", help="Enable the packet sniffer for"
                        "debugging.", action='store_true')
    asyncio.run(main(parser.parse_args()))
