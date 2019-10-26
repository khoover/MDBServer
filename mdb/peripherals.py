from abc import ABC, abstractmethod
import asyncio
import logging
from . import Master

logger = logging.getLogger(__name__)


class Peripheral(ABC):
    lock: asyncio.Lock
    master: Master

    """Generic implementation of an MDB peripheral interface."""
    def __init__(self):
        self.lock = asyncio.Lock()
        self.initialized = False

    @abstractmethod
    async def initialize(self, master: Master, send_reset=True):
        """Initialization code for the peripheral.

        Feel free to send messages here, the USB interface will be initialized
        before this is called.

        :param master: the Master instance controlling this peripheral
        :param send_reset: whether to send a reset command or not"""
        logger.debug("Initializing peripheral of type "
                     f"{self.__class__.__name__}.")
        self.initialized = True
        self.master = master

    async def send(self, message: str):
        assert self.initialized
        async with self.lock:
            await self.master.send(message)

    async def sendread(self, message: str, prefix: str):
        assert self.initialized
        async with self.lock:
            return await self.master.sendread(message, prefix)

    @abstractmethod
    async def enable(self):
        """Enables the peripheral for vending activities."""
        assert self.initialized

    @abstractmethod
    async def disable(self):
        """Disables the peripheral for vending activities."""
        assert self.initialized

    @abstractmethod
    async def run(self):
        """Does whatever persistent action is needed."""
        assert self.initialized


class BillValidator(Peripheral):
    pass


class CoinAcceptor(Peripheral):
    pass
