from abc import ABC, abstractmethod
import asyncio
import logging
import time
from typing import Sequence

logger = logging.getLogger(__name__)
SETUP_TIME_SECONDS = 0.2


class Peripheral(ABC):
    """Generic implementation of an MDB peripheral interface."""
    lock: asyncio.Lock
    NON_RESPONSE_SECONDS: float
    POLLING_INTERVAL_SECONDS = 0.1
    BOARD_RESPONSE_PREFIX = 'p'

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

    async def sendread(self, message: str) -> str:
        assert self.initialized
        async with self.lock:
            return await self.master.sendread(message,
                                              self.BOARD_RESPONSE_PREFIX)

    async def sendread_nolock(self, message: str) -> str:
        """Similar to send_nolock, except for sendread."""
        assert self.initialized and self.lock.locked()
        return await self.master.sendread(message, self.BOARD_RESPONSE_PREFIX)

    # Next are utility methods so that implementations only have to worry about
    # handling the reset after the non-response timeout.
    async def sendread_until_timeout(self, message: str) -> str:
        message_status = await self.sendread(message)
        start = time.time()
        while message_status == self.BOARD_RESPONSE_PREFIX + ',NACK' and \
                time.time() - start < self.NON_RESPONSE_SECONDS:
            asyncio.sleep(self.POLLING_INTERVAL_SECONDS)  # Ratelimiting
            message_status = await self.sendread(message,
                                                 self.BOARD_RESPONSE_PREFIX)
        # If the timeout is exceeded and we're still getting NACKs, let the
        # caller know so they can do recovery.
        return message_status

    async def sendread_nolock_until_timeout(self, message: str) -> str:
        message_status = await self.sendread_nolock(message)
        start = time.time()
        while message_status == self.BOARD_RESPONSE_PREFIX + ',NACK' and \
                time.time() - start < self.NON_RESPONSE_SECONDS:
            asyncio.sleep(self.POLLING_INTERVAL_SECONDS)  # Ratelimiting
            message_status = await self.sendread_nolock(message)
        # If the timeout is exceeded and we're still getting NACKs, let the
        # caller know so they can do recovery.
        return message_status

    async def sendread_until_data_or_nack(self, message: str) -> str:
        message_status = await self.sendread_until_timeout(message)
        while message_status == self.BOARD_RESPONSE_PREFIX + ',ACK':
            asyncio.sleep(self.POLLING_INTERVAL_SECONDS)  # Ratelimiting
            message_status = await self.sendread_until_timeout(message)
        return message_status

    async def sendread_nolock_until_data_or_nack(self, message: str) -> str:
        message_status = await self.sendread_nolock_until_timeout(message)
        while message_status == self.BOARD_RESPONSE_PREFIX + ',ACK':
            asyncio.sleep(self.POLLING_INTERVAL_SECONDS)  # Ratelimiting
            message_status = await self.sendread_nolock_until_timeout(message)
        return message_status

    @abstractmethod
    async def reset(self, send_reset=True) -> None:
        """Resets the peripheral.

        :param send_reset: Whether to send the reset command or not."""
        assert self.initialized

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
    # Data for these taken from the official MDB specification.
    ADDRESS = 0x30
    COMMANDS = {
        'RESET': 0x00,
        'SETUP': 0x01,
        'SECURITY': 0x02,
        'POLL': 0x03,
        'BILL TYPE': 0x04,
        'ESCROW': 0x05,
        'STACKER': 0x06,
        'EXPANSION COMMAND': 0x07
    }
    # How long the validator has to respond to a command before we reset it.
    # Given in the MDB specs.
    NON_RESPONSE_SECONDS = 5.0

    @staticmethod
    def create_address_byte(command: str) -> str:
        assert command in BillValidator.COMMANDS
        combined_hex = BillValidator.ADDRESS | BillValidator.COMMANDS[command]
        return f"{combined_hex:x}"

    # Shortcut for a frequently used command string.
    POLL_COMMAND = f"R,33\n"

    async def initialize(self, master, send_reset=True) -> None:
        await super().initialize(master, send_reset)
        try:
            await asyncio.wait_for(self.reset(send_reset), 30)
        except asyncio.TimeoutError:
            logger.critical('Unable to start the bill validator.')
            raise

    async def reset(self, send_reset=True) -> None:
        """Resets the bill validator.

        Will only return once the validator is finished the reset command
        sequence; should be wrapped with asyncio.wait_for if you want to
        timeout the attempts."""
        with self.lock:
            while True:
                if send_reset:
                    command = "R," + self.create_address_byte('RESET') + "\n"
                    response = 'R,NACK'
                    while response == 'R,NACK':
                        logger.info('Sending reset command to bill validator.')
                        response = await self.sendread_nolock_until_timeout(
                            command)
                    await asyncio.sleep(SETUP_TIME_SECONDS)
                # Poll until JUST RESET
                logger.info('Polling bill validator for JUST RESET.')
                response = await self.sendread_nolock_until_data_or_nack(
                    self.POLL_COMMAND)
                if response == 'R,NACK':
                    logger.error("Exceeded non-response time during reset, "
                                 "check if the vending machine is on fire.")
                    return False
                response_statuses = [response[i:i+2] for
                                     i in range(2, len(response), 2)]

    async def handle_poll_responses(self, responses: Sequence[str]):
        pass


class CoinAcceptor(Peripheral):
    pass
