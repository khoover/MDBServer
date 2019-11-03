import asyncio
import logging
from usb_handler import USBHandler, to_ascii

# On vend success, board sends 'c,VEND,SUCCESS'
# On vend fail, board sends 'c,ERR,...'

class CashlessSlave:
    _initialized: bool
    _logger: logging.Logger
    _usb_handler: USBHandler
    _queue: asyncio.Queue

    def __init__(self):
        self._initialized = False
        self._logger = logging.getLogger('.'.join((__name__,
                                                  self.__class__.__name__)))

    async def initialize(self, usb_handler: USBHandler):
        self._usb_handler = usb_handler
        self._queue = usb_handler.listen('c')
        await usb_handler.send(to_ascii('C,0\n'))
        await usb_handler.send(to_ascii('C,SETCONF,mdb-addr=0x10\n'))
        await usb_handler.send(to_ascii('C,SETCONF,mdb-currency-code=0x1840\n'))
        await usb_handler.send(to_ascii('C,1\n'))
        responses = []
        for _ in range(4):
            responses.append(await self._queue.get())
            self._queue.task_done()
        expected_responses = ['c,STATUS,OFFLINE','c,SET,OK','c,SET,OK','c,STATUS,ONLINE']
        if not all([responses[i] == expected_responses[i] for i in range(4)]):
            raise RuntimeError(f'Cashless slave did not correctly initialized, expected {expected_responses}, got {responses}.')
        self._initialized = True

    async def run(self):
        assert self._initialized
        while True:
            message = await self._queue.get()
            self._logger.info('Got message: %s', message)
            if message == 'c,STATUS,ENABLED':
                await usb_handler.send(to_ascii('C,START,1.00\n'))
            self._queue.task_done()
