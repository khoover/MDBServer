import asyncio
import logging
from usb_handler import USBHandler, to_ascii

logger = logging.getLogger(__name__)


class CashlessSlave:
    def __init__(self):
        self.initalized = False

    async def initialize(self, usb_handler: USBHandler):
        self.usb_handler = usb_handler
        self.initalized = True

    async def run(self):
        pass
