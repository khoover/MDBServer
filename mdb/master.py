import asyncio
import logging
from mdb.peripherals import BillValidator, CoinAcceptor, SETUP_TIME_SECONDS
from usb_handler import USBHandler, to_ascii

logger = logging.getLogger(__name__)


class Master:
    lock: asyncio.Lock
    usb_handler: USBHandler
    bill_validator: BillValidator
    coin_acceptor: CoinAcceptor

    def __init__(self):
        self.lock = asyncio.Lock()
        self.initialized = False

    async def initialize(self,
                         usb_handler: USBHandler,
                         bill_validator: BillValidator,
                         coin_acceptor: CoinAcceptor,
                         bus_reset=True) -> None:
        logger.debug("Initializing MDB Master.")
        self.initialized = True
        self.usb_handler = usb_handler
        self.bill_validator = bill_validator
        self.coin_acceptor = coin_acceptor
        status = await self.sendread('M,1\n', 'm')
        if status != 'm,ACK':
            raise RuntimeError('Unable to start master mode on MDB board.')
        if bus_reset:
            await self.send('R,RESET\n')
            # The extra time is how long the bus reset takes.
            await asyncio.sleep(SETUP_TIME_SECONDS + 0.1)
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
        await asyncio.gather(self.bill_validator.enable(),
                             self.coin_acceptor.enable())

    async def disable(self):
        assert self.initialized
        await asyncio.gather(self.bill_validator.disable(),
                             self.coin_acceptor.disable())

    async def run(self):
        assert self.initialized
        await asyncio.gather(self.bill_validator.run(),
                             self.coin_acceptor.run())
