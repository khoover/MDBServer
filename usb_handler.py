import asyncio
import logging
import os.path.exists
from serial_asyncio import open_serial_connection
from typing import Union


logger = logging.getLogger(__name__)
BytesLike = Union[str, bytes, bytearray]


class USBHandler:
    """Reads from and writes to the underlying MDB USB board.

    Users can either obtain an asyncio.Queue that the handler will push
    messages to using read(), or it can ask for a one-time read using
    readonce(). For sending messages, if no reply is expected or there is a
    poller waiting for any response, send() can be used, otherwise sendread()
    will send the message and wait for a one-time reply. Registering a one-time
    listener while a polling queue is an error, See the Sniffer class
    for examples of how both can be used."""

    def __init__(self):
        self.initialized = False
        self.waiters = {}
        self.queues = {}

    async def initialize(self, device_path: str):
        assert os.path.exists(device_path)
        logger.debug("Initializing USBReader.")
        self.initialized = True
        logger.info(f"Opening serial connection to device at {device_path}.")
        self.serial_reader, self.serial_writer = \
            await open_serial_connection(url=device_path, baudrate=115200)
        logger.info(f"Connected to serial device at {device_path}.")

    async def run(self):
        assert self.initialized
        while True:
            # TODO: Make sure the end-of-line character is b'\n'
            message = await self.serial_reader.readuntil(separator=b'\n')
            stripped_message = message.rstrip(b'\n\r')
            logger.info(f"Read '{stripped_message}' from MDB board.")
            # The first byte as a bytes, not an int; quirk of indexing bytes.
            message_type = stripped_message[0:1]
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

    async def send(self, message: BytesLike, _drain=True):
        assert self.initialized
        message = self.maybe_str_to_bytes(message)
        if message[-1] != 10:  # ASCII newlines are 0x0a.
            message = b"".join(message, b"\n")
        logger.info(f"Sending message to MDB board: {message}")
        self.serial_writer.write(message)
        if _drain:
            await self.serial_writer.drain()
        logger.info(f"Sent message to MDB board: {message}")

    def _readonce_internal(self, prefix: BytesLike):
        assert len(prefix) == 1
        prefix = self.maybe_str_to_bytes(prefix)
        if prefix in self.queues or prefix in self.waiters:
            raise RuntimeError("Tried to wait for message type {prefix}"
                               " when there was already a queue listening to "
                               "all messages")
        fut = asyncio.Future()
        self.waiters[prefix] = fut
        logger.info(f"Waiting for a single message of type: {prefix}")
        return fut

    async def sendread(self, message: BytesLike, prefix: BytesLike):
        self.send(message, _drain=False)
        fut = self._readonce_internal(prefix)
        await self.serial_writer.drain()
        await fut
        logger.info(f"Got message: {fut.result()}")
        return fut.result()

    async def readonce(self, prefix: BytesLike):
        fut = self._readonce_internal(prefix)
        await fut
        logger.info(f"Got message: {fut.result()}")
        return fut.result()

    def read(self, prefix: BytesLike):
        assert len(prefix) == 1
        prefix = self.maybe_str_to_bytes(prefix)
        if prefix in self.waiters or prefix in self.queues:
            raise RuntimeError("Tried to get a queue for message type {prefix}"
                               " when there was already someone waiting on"
                               " it.")
        self.queues[prefix] = asyncio.Queue()
        logger.info(f"Polling for messages of type: {prefix}")
        return self.queues[prefix]

    def unread(self, prefix: BytesLike):
        """Stops pushing messages with this prefix character to a Queue."""
        assert len(prefix) == 1
        prefix = self.maybe_str_to_bytes(prefix)
        del self.queues[prefix]
        logger.info(f"No longer polling for message type: {prefix}")

    @staticmethod
    def maybe_str_to_bytes(s: BytesLike):
        if type(s) is str:
            s = s.encode('ascii')
        return s
