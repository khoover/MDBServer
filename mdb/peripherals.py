from abc import ABC, abstractmethod
import asyncio
import logging

logger = logging.getLogger(__name__)


class Peripheral(ABC):
    """Generic implementation of an MDB peripheral interface."""
    lock: asyncio.Lock

    def __init__(self):
        self.lock = asyncio.Lock()
        self.initialized = False

    @abstractmethod
    async def initialize(self, master, send_reset=True) -> None:
        """Initialization code for the peripheral.

        Feel free to send messages here, the USB interface will be initialized
        before this is called.

        :param master: the Master instance controlling this peripheral
        :param send_reset: whether to send a reset command or not"""
        logger.debug("Initializing peripheral of type "
                     f"{self.__class__.__name__}.")
        self.initialized = True
        self.master = master

    async def send(self, message: str) -> None:
        assert self.initialized
        async with self.lock:
            await self.master.send(message)

    async def send_nolock(self, message: str) -> None:
        """Sends a message without acquiring the peripheral's lock. Assumes the
        caller or a parent has acquired the lock. Must not be used to
        circumvent the locking mechanism."""
        assert self.initialized and self.lock.locked()
        await self.master.send(message)

    async def sendread(self, message: str, prefix: str) -> str:
        assert self.initialized
        async with self.lock:
            return await self.master.sendread(message, prefix)

    async def sendread_nolock(self, message: str, prefix: str) -> str:
        """Similar to send_nolock, except for sendread."""
        assert self.initialized and self.lock.locked()
        return await self.master.sendread(message, prefix)

    @abstractmethod
    async def enable(self) -> None:
        """Enables the peripheral for vending activities."""
        assert self.initialized

    @abstractmethod
    async def disable(self) -> None:
        """Disables the peripheral for vending activities."""
        assert self.initialized

    @abstractmethod
    async def run(self) -> None:
        """Does whatever persistent action is needed."""
        assert self.initialized


class BillValidator(Peripheral):
    pass


class CoinAcceptor(Peripheral):
    pass
