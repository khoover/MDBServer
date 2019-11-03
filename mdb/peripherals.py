from abc import ABC, abstractmethod
import asyncio
import functools
import logging
import struct
import time
from typing import Sequence, Dict

SETUP_TIME_SECONDS = 0.2


class PeripheralError(Exception):
    """Base class for all peripheral-related errors."""
    pass


class NonResponseError(PeripheralError):
    """Raised when a peripheral does not respond by the non-response deadline.

    Attributes:
        message -- The message that was supposed to be sent to the peripheral.
        peripheral -- The name of the peripheral the command was being sent
                      to."""
    def __init__(self, message, peripheral):
        self.message = message
        self.peripheral = peripheral


class PeripheralResetError(PeripheralError):
    """Raised when a command is issued to a peripheral, and either the
    peripheral is resetting or the peripheral resets during the command.

    Attributes:
        peripheral -- The name of the peripheral the command was being sent
                      to."""
    def __init__(self, peripheral):
        self.peripheral = peripheral


class RequestMessage:
    BOARD_MESSAGE_PREFIX = 'R'

    def __init__(self, address_byte: bytes, payload=None):
        """Creates a request message.

        :param address_byte: The combined MDB address-command byte.
        :param payload: Any payload to be send with the command, encoded as
        bytes."""
        assert len(address_byte) == 1
        self.address_byte = address_byte
        self.payload = payload

    def pack(self) -> str:
        s = f"{self.BOARD_MESSAGE_PREFIX},{self.address_byte.hex()}"
        if self.payload:
            s += f",{self.payload.hex()}"
        return s + '\n'

    def __str__(self):
        s = f"(address_byte: {self.address_byte.hex()}"
        if self.payload:
            s += f", payload: {self.payload.hex()}"
        return s + ")"


class ResponseMessage:
    def __init__(self):
        self.is_ack = False
        self.is_nack = False
        self.data = None

    @staticmethod
    def unpack(message: str):
        response_message = ResponseMessage()
        stripped_message = message[2:]
        if stripped_message == 'ACK':
            response_message.is_ack = True
        elif stripped_message == 'NACK':
            response_message.is_nack = True
        else:
            response_message.data = bytes.fromhex(stripped_message)
        return response_message


def reset_wrapper(func):
    """Convenient decorator for the 'try MDB communication, reset on
    non-response' pattern. Assumes the wrapped function is a coroutine method
    of Peripheral or an implementing subclass.

    :raises PeripheralResetError: If the peripheral is resetting when the
    function is called, or if the peripheral needs to reset due to
    non-response."""
    assert asyncio.iscoroutinefunction(func)
    @functools.wraps(func)
    async def wrapper(self: Peripheral, *args, **kwargs):
        if self._reset_task:
            raise PeripheralResetError(self.__class__.__name__)
        try:
            await func(self, *args, **kwargs)
        except NonResponseError as e:
            self.logger.warning('Timed out communicating with %s, message '
                                'was %s', e.peripheral, e.message,
                                exc_info=e)
            asyncio.create_task(self.reset(True, True))
            raise PeripheralResetError(e.peripheral)
    return wrapper


class Peripheral(ABC):
    """Generic implementation of an MDB peripheral interface.

    In addition to the methods decorated with abstractmethod, subclasses should
    also define the following class constants: NON_RESPONSE_SECONDS, ADDRESS,
    and COMMANDS."""
    _lock: asyncio.Lock
    _initialized: bool
    _enabled: bool
    _reset_task: asyncio.Task
    _logger: logging.Logger
    BOARD_RESPONSE_PREFIX = 'p'
    POLLING_INTERVAL_SECONDS = 0.1
    # These should be defined in subclasses implementing peripherals.
    NON_RESPONSE_SECONDS: float
    COMMANDS: Dict[str, bytes]
    # How long to try resetting before giving up and timing out when
    # initializing a peripheral.
    INIT_RESET_TIMEOUT = 60
    # How long to wait in-between sending RESETs to a peripheral when the
    # peripheral isn't responding.
    RESET_RETRY_SLEEP = 5

    def __init__(self):
        self._lock = asyncio.Lock()
        self._initialized = False
        self._reset_task = None
        self._enabled = False
        self._logger = logging.getLogger('.'.join((__name__,
                                                  self.__class__.__name__)))

    async def initialize(self, master, send_reset=True) -> None:
        """Initialization code for the peripheral.

        Feel free to send messages here, the USB interface will be _initialized
        before this is called.

        :param master: the Master instance controlling this peripheral
        :param send_reset: whether to send a reset command or not"""
        self._logger.debug("Initializing peripheral of type %s.",
                           self.__class__.__name__)
        self._master = master
        self._initialized = True
        self._init_task = asyncio.create_task(self.reset(send_reset, True))

    async def send(self, message: RequestMessage) -> None:
        async with self._lock:
            await self._master.send(message.pack())

    async def send_nolock(self, message: RequestMessage) -> None:
        """Sends a message without acquiring the peripheral's lock. Assumes the
        caller or a parent has acquired the lock. Must not be used to
        circumvent the locking mechanism."""
        assert self._lock.locked()
        await self._master.send(message.pack())

    async def sendread(self, message: RequestMessage) -> ResponseMessage:
        async with self._lock:
            response = await self._master.sendread(message.pack(),
                                                   self.BOARD_RESPONSE_PREFIX)
            return ResponseMessage.unpack(response)

    async def sendread_nolock(self, message: RequestMessage) -> \
            ResponseMessage:
        """Similar to send_nolock, except for sendread."""
        assert self._lock.locked()
        response = await self._master.sendread(message.pack(),
                                               self.BOARD_RESPONSE_PREFIX)
        return ResponseMessage.unpack(response)

    # Next are utility methods so that implementations only have to worry about
    # handling the reset after the non-response timeout.
    async def sendread_until_timeout(self, message: RequestMessage) -> \
            ResponseMessage:
        async with self._lock:
            message_status = await self.sendread_nolock(message)
            start = time.time()
            while message_status.is_nack and \
                    time.time() - start < self.NON_RESPONSE_SECONDS:
                # Ratelimiting
                await asyncio.sleep(self.POLLING_INTERVAL_SECONDS)
                message_status = await self.sendread_nolock(message)
        if message_status.is_nack:
            raise NonResponseError(message, self.__class__.__name__)
        return message_status

    async def sendread_nolock_until_timeout(self, message: RequestMessage) -> \
            ResponseMessage:
        message_status = await self.sendread_nolock(message)
        start = time.time()
        while message_status.is_nack and \
                time.time() - start < self.NON_RESPONSE_SECONDS:
            await asyncio.sleep(self.POLLING_INTERVAL_SECONDS)  # Ratelimiting
            message_status = await self.sendread_nolock(message)
        if message_status.is_nack:
            raise NonResponseError(message, self.__class__.__name__)
        return message_status

    async def sendread_until_data_or_nack(self, message: RequestMessage) -> \
            ResponseMessage:
        message_status = await self.sendread_until_timeout(message)
        while message_status.is_ack:
            await asyncio.sleep(self.POLLING_INTERVAL_SECONDS)  # Ratelimiting
            message_status = await self.sendread_until_timeout(message)
        return message_status

    async def sendread_nolock_until_data_or_nack(
            self, message: RequestMessage) -> ResponseMessage:
        message_status = await self.sendread_nolock_until_timeout(message)
        while message_status.is_ack:
            await asyncio.sleep(self.POLLING_INTERVAL_SECONDS)  # Ratelimiting
            message_status = await self.sendread_nolock_until_timeout(message)
        return message_status

    @abstractmethod
    async def _reset(self, send_reset, poll_reset) -> None:
        pass

    async def reset(self, send_reset=True, poll_reset=True) -> None:
        """Resets the peripheral.

        :param send_reset: Whether to send the reset command or not.
        :param poll_reset: Whether to poll for JUST RESET. Must be True if
        send_reset is."""
        # assert self._initialized and (send_reset implies poll_reset)
        assert self._initialized and ((not send_reset) or poll_reset)
        self._enabled = False
        if not self._reset_task:
            self._reset_task = asyncio.create_task(self._reset(send_reset,
                                                               poll_reset))
        await self._reset_task

    @abstractmethod
    async def enable(self) -> None:
        """Enables the peripheral for vending activities."""
        assert self._initialized
        self._enabled = True

    @abstractmethod
    async def disable(self) -> None:
        """Disables the peripheral for vending activities."""
        assert self._initialized
        self._enabled = False

    # TODO: Decide what exactly this is going to return.
    @abstractmethod
    async def status(self):
        """Returns the status of the peripheral."""
        assert self._initialized

    @abstractmethod
    async def run(self) -> None:
        """Does whatever persistent action is needed."""
        assert self._initialized
        await self._init_task


class BillValidator(Peripheral):
    _escrow_pending: bool
    # Data for these taken from the official MDB specification.
    COMMANDS = {
        'RESET': b'\x30',
        'SETUP': b'\x31',
        'SECURITY': b'\x32',
        'POLL': b'\x33',
        'BILL TYPE': b'\x34',
        'ESCROW': b'\x35',
        'STACKER': b'\x36',
        'EXPANSION COMMAND': b'\x37'
    }
    # How long the validator has to respond to a command before we reset it.
    # Given in the MDB specs.
    NON_RESPONSE_SECONDS = 5.0
    # How long the validator has to respond with stacked, return, or invalid
    # command after an escrow command.
    ESCROW_RESPONSE_SECONDS = 30.0
    # Shortcut for a frequently used command string.
    POLL_INFO_STATUSES = {
        0x03: 'Validator busy - cannot answer a detailed command right now.',
        0x09: 'Validator disabled.',
        0x0b: 'Bill rejected - could not be identified.'
    }
    POLL_WARNING_STATUSES = {
        0x07: 'A bill was returned by unknown means.',
        0x0a: 'Invalid ESCROW request - an ESCROW request was made while no'
              'bill was in escrow.'
    }
    POLL_CRITICAL_STATUSES = {
        0x01: "Defective motor - one of the motors failed to perform.",
        0x02: "Sensor problem - one of the sensors has failed to provide a "
              "response.",
        0x04: "ROM checksum error - the validator's internal checksum does "
              "not match the computed one.",
        0x05: "Validator jammed - a bill has jammed in the acceptance path.",
        0x08: "Cash box out of position - the cash box has been opened or "
              "removed.",
        0x0c: "Possible credited bill removal - someone tried to remove a "
              "credited bill."
    }
    # Largest bill denomination the validator should accept, in cents.
    MAX_VALUE_CENTS = 2000  # $20

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._escrow_pending = False

    async def _reset(self, send_reset, poll_reset) -> None:
        await super()._reset(send_reset, poll_reset)
        async with self._lock:
            self._escrow_pending = False
            while True:
                try:
                    if send_reset:
                        message = RequestMessage(self.COMMANDS['RESET'])
                        self._logger.info('Sending reset command to bill '
                                          'validator.')
                        response = await self.sendread_nolock(message)
                        if response.is_ack:
                            await asyncio.sleep(SETUP_TIME_SECONDS)
                        else:
                            self._logger.warning('No response to RESET, '
                                                 'sleeping and retrying.')
                            await asyncio.sleep(self.RESET_RETRY_SLEEP)
                            # send_reset = True
                            # poll_reset = True, from the assert.
                            continue
                    # Poll until JUST RESET
                    if poll_reset:
                        self._logger.info('Polling bill validator for JUST '
                                          'RESET.')
                        response = \
                            await self.sendread_nolock_until_data_or_nack(
                                RequestMessage(self.COMMANDS['POLL']))
                        response_statuses = list(response.data)
                        # 0x06 is the code for JUST RESET.
                        if 0x06 not in response_statuses:
                            self._logger.warning("Did not get JUST RESET in "
                                                 "the first poll after "
                                                 "resetting, trying again.")
                            send_reset = True
                            # poll_reset = True
                            continue
                        # Pass off the remaining responses to the poll handler
                        # in the background.
                        asyncio.create_task(self.handle_poll_responses(
                            [x for x in response_statuses if x != 0x06]))

                    self._logger.info('Getting bill validator setup '
                                      'information.')
                    setup_data = await self.sendread_nolock_until_data_or_nack(
                        RequestMessage(self.COMMANDS['SETUP']))
                    self._logger.debug("Got %r", setup_data.data)
                    self.feature_level, country_code, self.scaling_factor, \
                        self.stacker_capacity, self.security_level_bitvector, \
                        escrow_byte = struct.unpack_from('>BHHHHB',
                                                         setup_data.data)
                    self._logger.debug('Bill validator level: %d',
                                       self.feature_level)
                    if country_code != 0x0001 and country_code != 0x1840:
                        raise RuntimeError('Bill validator does not use USD, '
                                           'stated country code is '
                                           f'{country_code:x}.')
                    self.has_escrow = escrow_byte == 0xff
                    self._logger.debug('Has escrow: %s, escrow byte: %#02x',
                                       self.has_escrow, escrow_byte)
                    self.bill_values = list(setup_data.data[11:])
                    self._logger.debug('Bill values: %s', self.bill_values)

                    self._logger.info('Getting bill validator expansion '
                                      'information.')
                    expansion_message = RequestMessage(
                        self.COMMANDS['EXPANSION COMMAND'])
                    if self.feature_level == 1:
                        expansion_message.payload = b'\x00'
                    else:
                        expansion_message.payload = b'\x02'
                    self.expansion_data = \
                        (await self.sendread_nolock_until_data_or_nack(
                            expansion_message)).data
                    self._logger.info('Got expansion data for bill validator: '
                                      '%r', self.expansion_data)

                    self._logger.info('Getting stacked bill count.')
                    stacker_count = \
                        await self.sendread_nolock_until_data_or_nack(
                            RequestMessage(self.COMMANDS['STACKER']))
                    stacker_count = struct.unpack('>H', stacker_count.data)[0]
                    self.stacker_count = (~0x8000) & stacker_count
                    self.stacker_full = stacker_count >= 0x8000
                    self._logger.debug('Stacker full: %s, stacker count: %d',
                                       self.stacker_full, self.stacker_count)

                    bills_to_enable = [int(x > 0 and
                                           x <= (self.MAX_VALUE_CENTS //
                                                 self.scaling_factor))
                                       for x in self.bill_values]
                    self._logger.debug('Bills to enable: %s', bills_to_enable)
                    bills_to_enable.reverse()
                    self.bill_enable_bitvector = 0
                    for b in bills_to_enable:
                        self.bill_enable_bitvector = \
                                (self.bill_enable_bitvector << 1) | (b & 1)
                    self._logger.debug('Bill enable bitvector: %#02x',
                                       self.bill_enable_bitvector)
                    enable_payload = struct.pack('>H',
                                                 self.bill_enable_bitvector)
                    if self.has_escrow:
                        enable_payload *= 2
                    else:
                        enable_payload += b'\x00\x00'
                    self.enable_message = RequestMessage(
                        self.COMMANDS['BILL TYPE'], payload=enable_payload)
                    self._reset_task = None
                    return
                except NonResponseError as e:
                    self._logger.warning("Bill validator timed out while "
                                         "resetting, message was %s.",
                                         e.message, exc_info=e)
                    send_reset = True
                    poll_reset = True

    async def handle_poll_responses(self, responses: Sequence[int]) -> None:
        reset_task = None
        if 0x06 in responses:
            # Unsolicited JUST RESET, do the rest of the reset process.
            self._enabled = False
            reset_task = asyncio.create_task(self.reset(False, False))
        elif 0x09 not in responses and not self._enabled:
            try:
                await self.disable()
            except PeripheralResetError:
                # It'll be disabled after the reset anyway.
                pass
        elif 0x09 in responses and self._enabled:
            try:
                await self.enable()
            except PeripheralResetError as e:
                # Peripheral reset while enabling, don't try re-enabling
                self._logger.info('Reset while enabling.', exc_info=e)
        for response in (x for x in responses if x != 0x06):
            if response in self.POLL_CRITICAL_STATUSES:
                self._logger.critical(self.POLL_CRITICAL_STATUSES[response])
                try:
                    await self.disable()
                except PeripheralResetError:
                    # Same logic as above.
                    pass
            elif response in self.POLL_INFO_STATUSES:
                self._logger.info(self.POLL_INFO_STATUSES[response])
            elif response in self.POLL_WARNING_STATUSES:
                self._logger.warning(self.POLL_WARNING_STATUSES[response])
            elif (not reset_task) and (response & 0x80 == 0x80):
                # This is a payment code, should do something about that.
                # TODO: Need to check if I can get one of these from the same
                # poll as a JUST RESET; could be confusing if we could.
                activity_type = (response & 0x70) >> 4
                bill_type = response & 0x0f
                bill_value = self.bill_values[bill_type] * self.scaling_factor
                self._logger.info('Activity type: %#01x, value: %d cents.',
                                  activity_type, bill_value)
                if activity_type == 0x01:
                    # Bill in escrow
                    self._escrow_pending = True
                    await self._master.notify_escrow(bill_value)
                elif activity_type == 0x00:
                    # Bill stacked
                    await self._master.notify_stack(bill_value)
                elif activity_type == 0x02:
                    # Bill returned
                    await self._master.notify_return(bill_value)
                elif activity_type == 0x04:
                    # Disabled bill rejected
                    self._logger.info('Rejected a disabled bill type: %#01x',
                                      bill_type)
                else:
                    self._logger.warning('Got an unknown stacker command: '
                                         '%#01d', activity_type)
            elif response & 0x40 == 0x40:
                # Attempted bill insertion while disabled
                count = response & (0x20 - 1)
                if count > 1:
                    bill = 'bills'
                else:
                    bill = 'bill'
                self._logger.info('Since last poll, people tried inserting %d '
                                  '%s while the validator was disabled.',
                                  count, bill)
            else:
                self._logger.warning('Unknown poll response received: %#02d',
                                     response)
        if reset_task:
            await reset_task

    @reset_wrapper
    async def stack_escrow(self) -> None:
        if not self._escrow_pending:
            self._logger.warning("Told to stack bill, but none in escrow.")
            return
        escrow_message = RequestMessage(self.COMMANDS['ESCROW'],
                                        payload=b'\x01')
        await self.sendread_until_timeout(escrow_message)
        self._escrow_pending = False

    @reset_wrapper
    async def return_escrow(self) -> None:
        if not self._escrow_pending:
            self._logger.warning("Told to return bill, but none in escrow.")
            return
        escrow_message = RequestMessage(self.COMMANDS['ESCROW'],
                                        payload=b'\x00')
        await self.sendread_until_timeout(escrow_message)
        self._escrow_pending = False

    @reset_wrapper
    async def enable(self) -> None:
        await super().enable()
        await self.sendread_until_timeout(self.enable_message)

    @reset_wrapper
    async def disable(self) -> None:
        if self._escrow_pending:
            await self.return_escrow()
        await super().disable()
        disable_message = RequestMessage(self.COMMANDS['BILL TYPE'],
                                         payload=b'\x00\x00\x00\x00')
        await self.sendread_until_timeout(disable_message)

    @reset_wrapper
    async def status(self):
        await super().status()
        # TODO: Decide what this is going to return.

    async def run(self) -> None:
        await super().run()
        await self.enable()
        poll_message = RequestMessage(self.COMMANDS['POLL'])
        while True:
            try:
                response = await self.sendread_until_data_or_nack(
                    poll_message)
                response_statuses = list(response.data)
                # Ensures we've finished processing a poll response and that
                # it's been sufficiently long since the last poll before
                # continuing.
                await asyncio.gather(
                    asyncio.sleep(self.POLLING_INTERVAL_SECONDS),
                    self.handle_poll_responses(response_statuses)
                )
            except NonResponseError as e:
                self._logger.warning('Bill validator timed out while polling, '
                                     'resetting.', exc_info=e)
                await self.reset(True, True)


class CoinAcceptor(Peripheral):
    async def _reset(self, send_reset, poll_reset) -> None:
        await super()._reset(send_reset, poll_reset)

    @reset_wrapper
    async def enable(self) -> None:
        await super().enable()

    @reset_wrapper
    async def disable(self) -> None:
        await super().disable()

    @reset_wrapper
    async def status(self):
        await super().status()
        # TODO: Decide what this is going to return.

    async def run(self) -> None:
        await super().run()


__all__ = (Peripheral, NonResponseError, PeripheralResetError, PeripheralError,
           BillValidator, CoinAcceptor, SETUP_TIME_SECONDS)
