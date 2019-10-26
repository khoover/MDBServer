import logging
from usb_handler import USBHandler, to_ascii

logger = logging.getLogger(__name__)


class Sniffer:
    def __init__(self):
        self.initialized = False

    async def initialize(self, usb_handler: USBHandler):
        logger.debug("Initializing MDB sniffer.")
        self.initialized = True
        self.usb_handler = usb_handler
        status = await usb_handler.sendread(to_ascii('X,1\n'), 'x')
        if status != 'x,ACK':
            logger.error(f"Unable to start MDB sniffer, got {status}")
        else:
            logger.debug("Sniffer initialized")
        self.message_queue = usb_handler.listen('x')

    async def run(self):
        assert self.initialized
        while True:
            message = await self.message_queue.get()
            message = message.split(',')[1:]
            logger.debug(f"Message sniffed: {message}")
