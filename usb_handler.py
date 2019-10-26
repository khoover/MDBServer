import asyncio
import logging
import os.path
from serial_asyncio import open_serial_connection
from typing import NewType, cast

logger = logging.getLogger(__name__)
# Type annotations and converters
AsciiBytes = NewType('AsciiBytes', bytes)


def to_ascii(s: str) -> AsciiBytes:
    if s[-1] != '\n':
        s += '\n'
    return cast(AsciiBytes, s.encode(encoding='ascii'))


class USBHandler:
    """Reads from and writes to the underlying MDB USB board.

    Users can either obtain an asyncio.Queue that the handler will push
    messages to using listen(), or it can ask for a one-time read using read().
    For sending messages, if no reply is expected or there is a poller waiting
    for any response, send() can be used, otherwise sendread() will send the
    message and wait for a one-time reply. Having a listener and waiting for a
    single message at the same time is an error. See the Sniffer class for an
    example of both usages."""

    def __init__(self):
        self.initialized = False
        self.waiters = {}
        self.queues = {}

    async def initialize(self, device_path: str) -> None:
        assert os.path.exists(device_path)
        logger.debug("Initializing USBReader.")
        self.initialized = True
        logger.info(f"Opening serial connection to device at {device_path}.")
        self.serial_reader, self.serial_writer = \
            await open_serial_connection(url=device_path, baudrate=115200)
        logger.info(f"Connected to serial device at {device_path}.")

    async def run(self) -> None:
        assert self.initialized
        while True:
            # TODO: Make sure the end-of-line character is b'\n' on board
            # replies.
            message = await self.serial_reader.readuntil(separator=b'\n')
            stripped_message = message.decode(encoding='ascii').rstrip('\n\r')
            logger.info(f"Read '{stripped_message}' from MDB board.")
            message_type = stripped_message[0]
            if message_type in self.waiters:
                self.waiters[message_type].set_result(stripped_message)
                del self.waiters[message_type]
                # Lets the waiter run.
                await asyncio.sleep(0)
            elif message_type in self.queues:
                try:
                    self.queues[message_type].put_nowait(stripped_message)
                except asyncio.QueueFull:
                    logger.warning(f'Queue for message type {message_type} is '
                                   'full. Scheduling the put in another task.')
                    asyncio.create_task(
                        self.queues[message_type].put(stripped_message))
            else:
                logger.error(f"Unhandled message: {stripped_message}")

    async def send(self, message: AsciiBytes, _drain=True) -> None:
        assert self.initialized
        logger.info(f"Sending message to MDB board: {message}")
        self.serial_writer.write(message)
        if _drain:
            await self.serial_writer.drain()
        logger.info(f"Sent message to MDB board: {message}")

    def _read_internal(self, prefix: str) -> asyncio.Future:
        assert len(prefix) == 1
        if prefix in self.queues or prefix in self.waiters:
            raise RuntimeError("Tried to wait for message type {prefix}"
                               " when there was already a queue listening to "
                               "all messages")
        fut = asyncio.Future()
        self.waiters[prefix] = fut
        logger.info(f"Waiting for a single message of type: {prefix}")
        return fut

    async def sendread(self, message: AsciiBytes, prefix: str) -> str:
        self.send(message, _drain=False)
        fut = self._read_internal(prefix)
        await self.serial_writer.drain()
        await fut
        logger.info(f"Got message: {fut.result()}")
        return fut.result()

    async def read(self, prefix: str) -> str:
        fut = self._read_internal(prefix)
        await fut
        logger.info(f"Got message: {fut.result()}")
        return fut.result()

    def listen(self, prefix: str) -> asyncio.Queue:
        assert len(prefix) == 1
        if prefix in self.waiters or prefix in self.queues:
            raise RuntimeError("Tried to get a queue for message type {prefix}"
                               " when there was already someone waiting on"
                               " it.")
        self.queues[prefix] = asyncio.Queue()
        logger.info(f"Polling for messages of type: {prefix}")
        return self.queues[prefix]

    def unlisten(self, prefix: str) -> None:
        """Stops pushing messages with this prefix character to a Queue."""
        assert len(prefix) == 1
        del self.queues[prefix]
        logger.info(f"No longer polling for message type: {prefix}")


__all__ = (USBHandler, to_ascii)
