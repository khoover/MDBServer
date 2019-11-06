import asyncio
import logging
import os.path
from serial_asyncio import open_serial_connection
from typing import NewType, cast

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
        self.logger = logging.getLogger('.'.join((__name__,
                                                  self.__class__.__name__)))

    async def initialize(self, device_path: str) -> None:
        assert os.path.exists(device_path)
        self.logger.info("Initializing USBReader.")
        self.logger.debug("Opening serial connection to device at %s",
                          device_path)
        self.serial_reader, self.serial_writer = \
            await open_serial_connection(url=device_path, baudrate=115200)
        self.initialized = True
        self.logger.debug("Connected to serial device at %s.", device_path)

    async def _run(self) -> None:
        while True:
            message = await self.serial_reader.readuntil(separator=b'\r\n')
            stripped_message = message.decode(encoding='ascii').rstrip('\n\r')
            self.logger.debug("Read '%s' from MDB board.", stripped_message)
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
                    self.logger.warning('Queue for message type %s is full. '
                                        'Scheduling the put in another task.',
                                        message_type)
                    asyncio.create_task(
                        self.queues[message_type].put(stripped_message))
            else:
                self.logger.error("Unhandled message: %s", stripped_message)

    async def run(self) -> None:
        assert self.initialized
        self.logger.info('Starting runner.')
        self.run_task = asyncio.create_task(self._run())
        try:
            await self.run_task
        except asyncio.CancelledError:
            self.logger.info('Runner cancelled.')

    async def send(self, message: AsciiBytes, _drain=True) -> None:
        assert self.initialized
        self.logger.info("Sending message to MDB board: %s", message)
        self.serial_writer.write(message)
        if _drain:
            await self.serial_writer.drain()
            self.logger.info("Sent message to MDB board: %s", message)

    def _read_internal(self, prefix: str) -> asyncio.Future:
        assert len(prefix) == 1
        if prefix in self.queues or prefix in self.waiters:
            raise RuntimeError(f"Tried to wait for message type {prefix}"
                               " when there was already a queue listening to "
                               "all messages")
        fut = asyncio.get_running_loop().create_future()
        self.waiters[prefix] = fut
        return fut

    async def sendread(self, message: AsciiBytes, prefix: str) -> str:
        await self.send(message, _drain=False)
        fut = self._read_internal(prefix)
        self.logger.info("Waiting for a single message of type: %s", prefix)
        try:
            await self.serial_writer.drain()
            self.logger.info("Sent message to MDB board: %s", message)
            await fut
        except asyncio.CancelledError as e:
            self.logger.warning("Got cancelled while sending message %r or "
                                "waiting on prefix %s", message, prefix,
                                exc_info=e)
            del self.waiters[prefix]
            raise
        self.logger.info("Got message: %s", fut.result())
        return fut.result()

    async def read(self, prefix: str) -> str:
        fut = self._read_internal(prefix)
        self.logger.info("Waiting for a single message of type: %s", prefix)
        try:
            await fut
        except asyncio.CancelledError as e:
            self.logger.warning("Got cancelled while waiting for message on "
                                "%s", prefix, exc_info=e)
            del self.waiters[prefix]
            raise
        self.logger.info("Got message: %s", fut.result())
        return fut.result()

    def listen(self, prefix: str) -> asyncio.Queue:
        assert len(prefix) == 1
        if prefix in self.waiters or prefix in self.queues:
            raise RuntimeError("Tried to get a queue for message type "
                               f"{prefix} when there was already someone"
                               "waiting on it.")
        self.queues[prefix] = asyncio.Queue()
        self.logger.info("Polling for messages of type: %s", prefix)
        return self.queues[prefix]

    def unlisten(self, prefix: str) -> None:
        """Stops pushing messages with this prefix character to a Queue."""
        assert len(prefix) == 1
        del self.queues[prefix]
        self.logger.info("No longer polling for message type: %s", prefix)

    async def shutdown(self):
        if not self.initialized:
            return
        self.logger.info("Shutting down.")
        if self.run_task:
            self.run_task.cancel()
        for fut in self.waiters.values():
            fut.cancel()
        self.serial_writer.close()
        await self.serial_writer.wait_closed()
        self.logger.info("Shutdown complete.")
        self.initialized = False


__all__ = (USBHandler, to_ascii)
