import logging
from usb_handler import USBHandler, to_ascii


class Sniffer:
    def __init__(self):
        self.initialized = False
        self.logger = logging.getLogger('.'.join((__name__,
                                                  self.__class__.__name__)))

    async def initialize(self, usb_handler: USBHandler):
        self.logger.debug("Initializing MDB sniffer.")
        self.usb_handler = usb_handler
        status = await usb_handler.sendread(to_ascii('X,1\n'), 'x')
        if status != 'x,ACK':
            self.logger.warning('Got something other than ACK: %s', status)
        self.initialized = True
        self.logger.debug("Sniffer initialized")
        self.message_queue = usb_handler.listen('x')

    async def run(self):
        assert self.initialized
        while True:
            message = await self.message_queue.get()
            message = message.split(',')[1:]
            self.logger.debug("Message sniffed: %r", message)
