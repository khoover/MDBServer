import asyncio
import logging
from mdb.peripherals import BillValidator, CoinAcceptor, SETUP_TIME_SECONDS
from usb_handler import USBHandler, to_ascii


class Master:
    lock: asyncio.Lock
    usb_handler: USBHandler
    bill_validator: BillValidator
    coin_acceptor: CoinAcceptor

    def __init__(self):
        self.lock = asyncio.Lock()
        self.initialized = False
        self.logger = logging.getLogger('.'.join((__name__,
                                                  self.__class__.__name__)))

    async def initialize(self,
                         usb_handler: USBHandler,
                         bill_validator: BillValidator,
                         coin_acceptor: CoinAcceptor,
                         bus_reset=True) -> None:
        self.logger.info("Initializing MDB Master.")
        self.initialized = True
        self.usb_handler = usb_handler
        self.bill_validator = bill_validator
        self.coin_acceptor = coin_acceptor
        self.logger.debug('Enabling Master driver.')
        status = await self.sendread('M,1\n', 'm')
        if status != 'm,ACK':
            raise RuntimeError('Unable to start master mode on MDB board.')
        if bus_reset:
            self.logger.debug('Bus-reseting peripherals.')
            await self.send('R,RESET\n')
            # The extra time is how long the bus reset takes.
            await asyncio.sleep(SETUP_TIME_SECONDS + 0.1)
        self.logger.info('Initializing MDB peripherals.')
        await asyncio.gather(bill_validator.initialize(self, not bus_reset),
                             coin_acceptor.initialize(self, not bus_reset))

    async def send(self, message: str) -> None:
        assert self.initialized
        async with self.lock:
            await self.usb_handler.send(to_ascii(message))

    async def sendread(self, message: str, prefix: str) -> str:
        assert self.initialized
        async with self.lock:
            return await self.usb_handler.sendread(to_ascii(message), prefix)

    async def enable(self):
        assert self.initialized
        self.logger.info("Enabling MDB peripherals.")
        await asyncio.gather(self.bill_validator.enable(),
                             self.coin_acceptor.enable())

    async def disable(self):
        assert self.initialized
        self.logger.info("Disabling MDB peripherals.")
        await asyncio.gather(self.bill_validator.disable(),
                             self.coin_acceptor.disable())

    async def run(self):
        assert self.initialized
        self.logger.info('Running MDB peripherals.')
        await asyncio.gather(self.bill_validator.run(),
                             self.coin_acceptor.run())
