import asyncio
import logging
from mdb.peripherals import BillValidator, CoinAcceptor, SETUP_TIME_SECONDS, \
                            PeripheralResetError
from usb_handler import USBHandler, to_ascii
from typing import Sequence


class Master:
    _lock: asyncio.Lock
    _usb_handler: USBHandler
    _bill_validator: BillValidator
    _coin_acceptor: CoinAcceptor
    _initialized: bool
    _logger: logging.Logger
    _run_task: asyncio.Task

    def __init__(self):
        self._lock = asyncio.Lock()
        self._initialized = False
        self._run_task = None
        self._logger = logging.getLogger('.'.join((__name__,
                                                  self.__class__.__name__)))

    async def initialize(self,
                         usb_handler: USBHandler,
                         bill_validator: BillValidator,
                         coin_acceptor: CoinAcceptor,
                         bus_reset=True) -> None:
        self._logger.info("Initializing MDB Master.")
        self._usb_handler = usb_handler
        self._bill_validator = bill_validator
        self._coin_acceptor = coin_acceptor
        self._logger.debug('Enabling Master driver.')
        status = None
        async with self._lock:
            status = await self._usb_handler.sendread(to_ascii('M,1\n'), 'm')
        if status != 'm,ACK':
            raise RuntimeError('Unable to start master mode on MDB board.')
        self._initialized = True
        if bus_reset:
            self._logger.debug('Bus-reseting peripherals.')
            await self.send('R,RESET\n')
            # The extra time is how long the bus reset takes.
            await asyncio.sleep(SETUP_TIME_SECONDS + 0.1)
        self._logger.info('Initializing MDB peripherals.')
        await asyncio.gather(bill_validator.initialize(self, not bus_reset),
                             coin_acceptor.initialize(self, not bus_reset))

    async def send(self, message: str) -> None:
        assert self._initialized
        async with self._lock:
            await self._usb_handler.send(to_ascii(message))

    async def sendread(self, message: str, prefix: str) -> str:
        assert self._initialized
        async with self._lock:
            return await self._usb_handler.sendread(to_ascii(message), prefix)

    async def enable(self) -> Sequence[Exception]:
        assert self._initialized
        self._logger.info("Enabling MDB peripherals.")
        return await asyncio.gather(self._bill_validator.enable(),
                                    self._coin_acceptor.enable(),
                                    return_exceptions=True)

    async def disable(self) -> Sequence[Exception]:
        assert self._initialized
        self._logger.info("Disabling MDB peripherals.")
        return await asyncio.gather(self._bill_validator.disable(),
                                    self._coin_acceptor.disable(),
                                    return_exceptions=True)

    async def status(self):
        assert self._initialized
        self._logger.info("Getting MDB peripheral statuses.")
        # TODO: Figure out what this returns, exactly, and if I need to do any
        # aggregating here.
        return await asyncio.gather(self._bill_validator.status(),
                                    self._coin_acceptor.status(),
                                    return_exceptions=True)

    async def run(self):
        assert self._initialized
        self._logger.info('Running MDB peripherals.')
        self._run_task = asyncio.gather(self._bill_validator.run(),
                                        self._coin_acceptor.run())
        try:
            await self._run_task
        except asyncio.CancelledError:
            pass

    async def shutdown(self):
        if not self._run_task:
            return
        self._logger.info('Shutting down.')
        await self.disable()
        await self.sendread('M,0\n', 'm')
        self._run_task.cancel()
        self._logger.info('Shutdown complete.')

    # Bill validator specific commands.
    async def notify_escrow(self, bill_value):
        self._logger.info('Bill with value %d cents in escrow.', bill_value)
        await self.return_escrow()

    async def notify_stack(self, bill_value):
        self._logger.info('Bill with value %d cents stacked.', bill_value)

    async def notify_return(self, bill_value):
        self._logger.info('Bill with value %d cents returned.', bill_value)

    async def return_escrow(self):
        try:
            await self._bill_validator.return_escrow()
        except PeripheralResetError as e:
            self._logger.info('Bill validator reset during escrow return.',
                              exc_info=e)

    async def stack_escrow(self):
        try:
            await self._bill_validator.stack_escrow()
        except PeripheralResetError as e:
            self._logger.info('Bill validator reset during escrow stack.',
                              exc_info=e)
            # Notify the websocket client, probably, that the stack failed.
