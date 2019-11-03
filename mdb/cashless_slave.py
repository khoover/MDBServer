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
    _run_task: asyncio.Task

    def __init__(self):
        self._initialized = False
        self._run_task = None
        self._logger = logging.getLogger('.'.join((__name__,
                                                  self.__class__.__name__)))

    async def initialize(self, usb_handler: USBHandler):
        self._logger.info("Initializing")
        self._usb_handler = usb_handler
        await usb_handler.sendread(to_ascii('C,0\n'), 'c')
        self._queue = usb_handler.listen('c')
        await usb_handler.send(to_ascii('C,SETCONF,mdb-addr=0x10\n'))
        await usb_handler.send(
            to_ascii('C,SETCONF,mdb-currency-code=0x1840\n'))
        await usb_handler.send(to_ascii('C,1\n'))
        responses = []
        for _ in range(3):
            responses.append(await self._queue.get())
            self._queue.task_done()
        expected_responses = [
            'c,SET,OK',
            'c,SET,OK',
            'c,STATUS,ONLINE'
        ]
        if responses != expected_responses:
            raise RuntimeError('Cashless slave did not correctly initialized, '
                               f'expected {expected_responses}, got '
                               f'{responses}.')
        self._logger.info("Finished initializing.")
        self._initialized = True

    async def _run(self):
        while True:
            message = await self._queue.get()
            self._logger.info('Got message: %s', message)
            if message == 'c,STATUS,ENABLED':
                await self._usb_handler.send(to_ascii('C,START,1.00\n'))
            self._queue.task_done()

    async def run(self):
        assert self._initialized
        self._logger.info("Running.")
        self._run_task = asyncio.create_task(self._run())
        try:
            await self._run_task
        except asyncio.CancelledError:
            pass

    async def shutdown(self):
        if not self._run_task:
            return
        self._logger.info("Shutting down.")
        await self._usb_handler.send(to_ascii('C,0\n'))
        self._run_task.cancel()
        self._logger.info("Shutdown complete.")
