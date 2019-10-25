from abc import ABC, abstractmethod
import argparse
import asyncio
import logging
import os.path.exists
from serial_asyncio import open_serial_connection
from typing import Union
import websockets


logger = logging.getLogger(__name__)


class USBHandler:
    """Reads from and writes to the underlying MDB USB board.

    Intended to be used as follows: there is a single consumer of any message
    type (i.e. message prefix), which calls the read method with that prefix
    passed. Read will then wait until a message with that prefix arrives and
    return with that message. Consumers should have a dedicated polling loop
    that does not block with message processing. The Master and CashlessSlave
    ckasses contain two different implementations of this behaviour."""
    def __init__(self):
        self.initialized = False
        self.waiters = {}

    async def initialize(self, device_path: str):
        logger.debug("Initializing USBReader.")
        self.initialized = True
        logger.info(f"Opening serial connection to device at {device_path}.")
        self.serial_reader, self.serial_writer = \
            await open_serial_connection(url=device_path, baudrate=115200)
        logger.info(f"Connected to serial device at {device_path}.")

    async def run(self):
        assert self.initialized

    async def send(self, message: Union[str, bytes]):
        if type(message) is not bytes:
            message = message.encode('ascii')
        if message[-1] != 10:  # ASCII newlines are 0x0a.
            message = b"".join(message, b"\n")
        logger.info(f"Sending message to MDB board: {message}")
        self.serial_writer.write(message)
        await self.serial_writer.drain()
        logger.info(f"Sent message to MDB board: {message}")

    async def read(self, prefix: str):
        assert len(prefix) == 1


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


class Slave(ABC):
    pass


class CashlessSlave(Slave):
    pass


class WebsocketClient:
    pass


def main(args):
    pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Starts the ChezBob MDB server.")
    parser.add_argument('--device_path', help="Location of the MDB board's "
                        "device file. Defaults to '/dev/ttyUSB0'.",
                        default='/dev/ttyUSB0', type=str)
    asyncio.run(main(parser.parse_args()))
