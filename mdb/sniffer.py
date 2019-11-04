import asyncio
import logging
import struct
import time
from typing import Sequence
from usb_handler import USBHandler, to_ascii


class Sniffer:
    def __init__(self):
        self.initialized = False
        self.logger = logging.getLogger('.'.join((__name__,
                                                  self.__class__.__name__)))
        self.logger.setLevel(logging.DEBUG)
        self.run_task = None

    async def initialize(self, usb_handler: USBHandler):
        self.logger.debug("Initializing MDB sniffer.")
        self.usb_handler = usb_handler
        status = await usb_handler.sendread(to_ascii('X,1\n'), 'x')
        if status != 'x,ACK':
            self.logger.warning('Got something other than ACK: %s', status)
        self.initialized = True
        self.logger.debug("Sniffer initialized")
        self.message_queue = usb_handler.listen('x')

    def parse_message(self, message: Sequence[str]) -> str:
        block_status = struct.unpack('>H', bytes.fromhex(message[0]))
        address = struct.unpack('>H', bytes.fromhex(message[2]))
        address_str = ""
        if block_status & 0x80:
            address_str = f"To slave: {address} -- "
        error_str = ""
        if block_status & (~0x81):
            error_str = "Error: {(block_status & (~0x81)) >> 1} -- "
        return f"(Time: {time.ctime()} -- " \
               f"From master: {bool(block_status & 0x80)} -- " \
               f"{address_str}" \
               f"Bus reset: {bool(block_status & 0x01)} -- " \
               f"{error_str}" \
               f"Data: {message[4]})"

    async def _run(self):
        while True:
            message = await self.message_queue.get()
            if message == 'x,ACK' or message == 'x,NACK':
                continue
            message = message.split(',')[1:]
            self.logger.debug("Message sniffed: %s",
                              self.parse_message(message))
            self.message_queue.task_done()

    async def run(self):
        assert self.initialized
        self.run_task = asyncio.create_task(self._run())
        try:
            await self.run_task
        except asyncio.CancelledError:
            pass

    async def shutdown(self):
        if not self.run_task:
            return
        self.logger.info('Shutting down.')
        self.usb_handler.unlisten('x')
        await self.usb_handler.sendread(to_ascii('X,0\n'), 'x')
        self.run_task.cancel()
        self.logger.info('Shutdown complete.')
