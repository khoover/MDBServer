import asyncio
import logging
from usb_handler import USBHandler, to_ascii
from mdb import Peripheral, BillValidator, CoinAcceptor
from typing import Seq

logger = logging.getLogger(__name__)


class Master:
    lock: asyncio.Lock
    usb_handler: USBHandler
    peripherals: Seq[Peripheral]

    def __init__(self):
        self.lock = asyncio.Lock()
        self.initialized = False

    async def initialize(self,
                         usb_handler: USBHandler,
                         bus_reset=True) -> None:
        logger.debug("Initializing MDB Master.")
        self.initialized = True
        self.usb_handler = usb_handler
        status = await self.sendread('M,1\n', 'm')
        if status != 'm,ACK':
            raise RuntimeError('Unable to start master mode on MDB board.')
        if bus_reset:
            await self.send('R,RESET\n')
            await asyncio.sleep(0.2)

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
        tasks = [asyncio.create_task(peripheral.enable()) for peripheral in
                 self.peripherals]
        done, _ = await asyncio.wait(tasks)
        for t in done:
            if t.exception():
                logger.error("Had an error enabling an MDB device: "
                             f"{t.exception()}")

    async def disable(self):
        assert self.initialized
        tasks = [asyncio.create_task(peripheral.disable()) for peripheral in
                 self.peripherals]
        done, _ = await asyncio.wait(tasks)
        for t in done:
            if t.exception():
                logger.error("Had an error disabling an MDB device: "
                             f"{t.exception()}")
