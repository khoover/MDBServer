import asyncio
import logging
from mdb.peripherals import BillValidator, CoinAcceptor
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
            await asyncio.sleep(0.2)
        validator_init = asyncio.create_task(
            bill_validator.initialize(self, not bus_reset))
        acceptor_init = asyncio.create_task(
            coin_acceptor.initialize(self, not bus_reset))
        asyncio.wait((validator_init, acceptor_init))

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
        validator_enable = asyncio.create_task(self.bill_validator.enable())
        acceptor_enable = asyncio.create_task(self.coin_acceptor.enable())
        done, _ = await asyncio.wait((validator_enable, acceptor_enable))
        for t in done:
            if t.exception():
                logger.error("Had an error enabling an MDB device: "
                             f"{t.exception()}")

    async def disable(self):
        assert self.initialized
        validator_disable = asyncio.create_task(self.bill_validator.disable())
        acceptor_disable = asyncio.create_task(self.coin_acceptor.disable())
        done, _ = await asyncio.wait((validator_disable, acceptor_disable))
        for t in done:
            if t.exception():
                logger.error("Had an error disabling an MDB device: "
                             f"{t.exception()}")
