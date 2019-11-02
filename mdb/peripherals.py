from abc import ABC, abstractmethod
import asyncio
import functools
import logging
import time
from typing import Sequence, Dict

SETUP_TIME_SECONDS = 0.2


class PeripheralError(Exception):
    """Base class for all peripheral-related errors."""
    pass


class NonResponseError(PeripheralError):
    """Raised when a peripheral does not respond by the non-response deadline.

    Attributes:
        command -- The command that was supposed to be sent to the peripheral.
        peripheral -- The name of the peripheral the command was being sent
                      to."""
    def __init__(self, command, peripheral):
        self.command = command
        self.peripheral = peripheral


class PeripheralResetError(PeripheralError):
    """Raised when a command is issued to a peripheral, and either the
    peripheral is resetting or the peripheral resets during the command.

    Attributes:
        peripheral -- The name of the peripheral the command was being sent
                      to."""
    def __init__(self, peripheral):
        self.peripheral = peripheral


class InvalidEscrowError(PeripheralError):
    """Raised when an escrow method is called, but no bill is in escrow."""
    pass


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
            raise PeripheralResetError(self.__class__.__name)
        try:
            await func(self, *args, **kwargs)
        except NonResponseError as e:
            self.logger.warning('Timed out communicating with %s, command '
                                'was %r', e.peripheral, e.command,
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
    _reset_task: asyncio.Task
    _logger: logging.Logger
    POLLING_INTERVAL_SECONDS = 0.1
    BOARD_RESPONSE_PREFIX = 'p'
    # These should be defined in subclasses implementing peripherals.
    NON_RESPONSE_SECONDS: float
    ADDRESS: int
    COMMANDS: Dict[str, int]
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
        self._logger = logging.getLogger('.'.join((__name__,
                                                  self._class__.__name__)))

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

    async def send(self, message: str) -> None:
        async with self._lock:
            await self._master.send(message)

    async def send_nolock(self, message: str) -> None:
        """Sends a message without acquiring the peripheral's lock. Assumes the
        caller or a parent has acquired the lock. Must not be used to
        circumvent the locking mechanism."""
        assert self._lock.locked()
        await self._master.send(message)

    async def sendread(self, message: str) -> str:
        async with self._lock:
            return await self._master.sendread(message,
                                               self.BOARD_RESPONSE_PREFIX)

    async def sendread_nolock(self, message: str) -> str:
        """Similar to send_nolock, except for sendread."""
        assert self._lock.locked()
        return await self._master.sendread(message, self.BOARD_RESPONSE_PREFIX)

    # Next are utility methods so that implementations only have to worry about
    # handling the reset after the non-response timeout.
    async def sendread_until_timeout(self, message: str) -> str:
        non_response_reply = self.BOARD_RESPONSE_PREFIX + ',NACK'
        with self._lock:
            message_status = await self.sendread_nolock(message)
            start = time.time()
            while message_status == non_response_reply and \
                    time.time() - start < self.NON_RESPONSE_SECONDS:
                asyncio.sleep(self.POLLING_INTERVAL_SECONDS)  # Ratelimiting
                message_status = await self.sendread_nolock(
                    message, self.BOARD_RESPONSE_PREFIX)
        if message_status == non_response_reply:
            raise NonResponseError(message, self.__class__.__name__)
        return message_status

    async def sendread_nolock_until_timeout(self, message: str) -> str:
        non_response_reply = self.BOARD_RESPONSE_PREFIX + ',NACK'
        message_status = await self.sendread_nolock(message)
        start = time.time()
        while message_status == non_response_reply and \
                time.time() - start < self.NON_RESPONSE_SECONDS:
            asyncio.sleep(self.POLLING_INTERVAL_SECONDS)  # Ratelimiting
            message_status = await self.sendread_nolock(message)
        if message_status == non_response_reply:
            raise NonResponseError(message, self.__class__.__name__)
        return message_status

    async def sendread_until_data_or_nack(self, message: str) -> str:
        message_status = await self.sendread_until_timeout(message)
        while message_status == self.BOARD_RESPONSE_PREFIX + ',ACK':
            asyncio.sleep(self.POLLING_INTERVAL_SECONDS)  # Ratelimiting
            message_status = await self.sendread_until_timeout(message)
        return message_status

    async def sendread_nolock_until_data_or_nack(self, message: str) -> str:
        message_status = await self.sendread_nolock_until_timeout(message)
        while message_status == self.BOARD_RESPONSE_PREFIX + ',ACK':
            asyncio.sleep(self.POLLING_INTERVAL_SECONDS)  # Ratelimiting
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
        if not self._reset_task:
            self._reset_task = asyncio.create_task(self._reset(send_reset,
                                                               poll_reset))
        await self._reset_task

    @abstractmethod
    async def enable(self) -> None:
        """Enables the peripheral for vending activities."""
        assert self._initialized

    @abstractmethod
    async def disable(self) -> None:
        """Disables the peripheral for vending activities."""
        assert self._initialized

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

    @classmethod
    def create_address_byte(cls, command: str) -> str:
        combined_hex = cls.ADDRESS | cls.COMMANDS[command]
        return f"{combined_hex:x}"


class BillValidator(Peripheral):
    _escrow_pending: bool
    # Data for these taken from the official MDB specification.
    ADDRESS = 0x30
    COMMANDS = {
        'RESET': 0x00,
        'SETUP': 0x01,
        'SECURITY': 0x02,
        'POLL': 0x03,
        'BILL TYPE': 0x04,
        'ESCROW': 0x05,
        'STACKER': 0x06,
        'EXPANSION COMMAND': 0x07
    }
    # How long the validator has to respond to a command before we reset it.
    # Given in the MDB specs.
    NON_RESPONSE_SECONDS = 5.0
    # How long the validator has to respond with stacked, return, or invalid
    # command after an escrow command.
    ESCROW_RESPONSE_SECONDS = 30.0
    # Shortcut for a frequently used command string.
    POLL_COMMAND = f"R,33\n"
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._escrow_pending = False

    async def _reset(self, send_reset, poll_reset) -> None:
        super()._reset(send_reset, poll_reset)
        async with self._lock:
            self._escrow_pending = False
            while True:
                try:
                    if send_reset:
                        command = f"R,{self.create_address_byte('RESET')}\n"
                        self._logger.info('Sending reset command to bill '
                                          'validator.')
                        response = await self.sendread_nolock(command)
                        if response == 'p,ACK':
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
                                self.POLL_COMMAND)
                        response_statuses = [int(response[i:i+2], base=16) for
                                             i in range(2, len(response), 2)]
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
                        "R," + self.create_address_byte('SETUP') + "\n")
                    setup_data_bytes = [setup_data[i:i+2] for
                                        i in range(2, len(setup_data), 2)]
                    self.feature_level = int(setup_data_bytes[0], base=16)
                    self._logger.debug('Bill validator level: %d',
                                       self.feature_level)
                    country_code = setup_data_bytes[1] + setup_data_bytes[2]
                    if country_code != '0001' and country_code != '1840':
                        raise RuntimeError('Bill validator does not use USD, '
                                           'stated country code is '
                                           f'{country_code}.')
                    # How many cents a single 'unit' of value corresponds to.
                    self.scaling_factor = int(setup_data_bytes[3] +
                                              setup_data_bytes[4], base=16)
                    self.stacker_capacity = int(setup_data_bytes[6] +
                                                setup_data_bytes[7], base=16)
                    self.security_level_bitvector = int(setup_data_bytes[8] +
                                                        setup_data_bytes[9],
                                                        base=16)
                    self.has_escrow = \
                        int(setup_data_bytes[10], base=16) == 0xff
                    self.bill_values = [int(x, base=16) for x in
                                        setup_data_bytes[11:]]

                    self._logger.info('Getting bill validator expansion '
                                      'information.')
                    expansion_command = 'R,' + \
                        self.create_address_byte('EXPANSION COMMAND')
                    if self.feature_level == 1:
                        expansion_command += ',00\n'
                    else:
                        expansion_command += ',02\n'
                    self.expansion_data = \
                        await self.sendread_nolock_until_data_or_nack(
                            expansion_command)
                    self._logger.info('Got expansion data for bill validator: '
                                      '%r', self.expansion_data)

                    self._logger.info('Getting stacked bill count.')
                    stacker_count = \
                        await self.sendread_nolock_until_data_or_nack(
                            'R,' + self.create_address_byte('STACKER') + '\n')
                    stacker_count = int(stacker_count[2:], base=16)
                    self.stacker_count = (~0x8000) & stacker_count
                    self.stacker_full = stacker_count >= 0x8000

                    # Could have a MAX_VALUE constant with the maximum bill
                    # value (in cents) that ChezBob is willing to accept? I've
                    # picked $20 as just being a reasonable number.
                    bills_to_enable = [int(x > 0 and
                                           x <= (2000 // self.scaling_factor))
                                       for x in self.bill_values]
                    bills_to_enable.extend([0] * (16 - len(self.bill_values)))
                    bills_to_enable.reverse()
                    self.bill_enable_bitvector = int(''.join(bills_to_enable),
                                                     base=2)
                    self.enable_command = \
                        f"R,{self.create_address_byte('BILL TYPE')}," \
                        f"{self.bill_enable_bitvector:x}"
                    if self.has_escrow:
                        self.enable_command += \
                            f"{self.bill_enable_bitvector:x}\n"
                    else:
                        self.enable_command += "00\n"
                    await self.sendread_nolock_until_timeout(
                        self.enable_command)
                    self._reset_task = None
                    return
                except NonResponseError as e:
                    self._logger.warning("Bill validator timed out while "
                                         "resetting, command was '%r'.",
                                         e.command, exc_info=e)
                    send_reset = True
                    poll_reset = True

    async def handle_poll_responses(self, responses: Sequence[int]) -> None:
        reset_task = None
        for response in responses:
            if response in self.POLL_CRITICAL_STATUSES:
                self._logger.critical(self.POLL_CRITICAL_STATUSES[response])
            elif response in self.POLL_INFO_STATUSES:
                self._logger.info(self.POLL_INFO_STATUSES[response])
            elif response in self.POLL_WARNING_STATUSES:
                self._logger.warning(self.POLL_WARNING_STATUSES[response])
            elif response & 0x80 == 0x80:
                # This is a payment code, should do something about that.
                # TODO: Need to check if I can get one of these from the same
                # poll as a JUST RESET; could be confusing if we could.
                activity_type = (response & 0x70) >> 4
                bill_type = response & 0x0f
                bill_value = self.bill_values(bill_type) * self.scaling_factor
                if activity_type == 0x01:
                    # Bill in escrow
                    self._escrow_pending = True
                elif activity_type == 0x00:
                    # Bill stacked
                    pass
                elif activity_type == 0x02:
                    # Bill returned
                    pass
                elif activity_type == 0x04:
                    # Disabled bill rejected
                    pass
                else:
                    self._logger.warning('Got an unknown stacker command: '
                                         '%#01d', activity_type)
            elif response == 0x06:
                # Unsolicited JUST RESET
                if not reset_task:
                    reset_task = asyncio.create_task(self.reset(False, False))
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

    # TODO: Decide how this bit is going to work. Could add stack_pending and
    # return_pending variables? At that point, though, why not just have a full
    # state machine going on?
    @reset_wrapper
    async def stack_escrow(self) -> None:
        if not self._escrow_pending:
            self._logger.warning("Told to stack bill, but none in escrow.")
            raise InvalidEscrowError()
        escrow_command = f"R,{self.create_address_byte('ESCROW')},01"
        await self.sendread_until_timeout(escrow_command)
        self._escrow_pending = False

    @reset_wrapper
    async def return_escrow(self) -> None:
        if not self._escrow_pending:
            self._logger.warning("Told to return bill, but none in escrow.")
            raise InvalidEscrowError()
        escrow_command = f"R,{self.create_address_byte('ESCROW')},00"
        await self.sendread_until_timeout(escrow_command)
        self._escrow_pending = False

    @reset_wrapper
    async def enable(self) -> None:
        await super().enable()
        await self.sendread_until_timeout(self.enable_command)

    @reset_wrapper
    async def disable(self) -> None:
        if self._escrow_pending:
            await self.return_escrow()
        await super().disable()
        disable_command = f"R,{self.create_address_byte('BILL TYPE')},0000\n"
        await self.sendread_until_timeout(disable_command)

    @reset_wrapper
    async def status(self):
        await super().status()
        # TODO: Decide what this is going to return.

    async def run(self) -> None:
        await super().run()
        while True:
            try:
                response = await self.sendread_until_data_or_nack(
                    self.POLL_COMMAND)
                response_statuses = [int(response[i:i+2], base=16) for
                                     i in range(2, len(response), 2)]
                response_handler = asyncio.create_task(
                    self.handle_poll_responses(response_statuses))
                await asyncio.sleep(self.POLLING_INTERVAL_SECONDS)
                # Make sure we've finished processing the last batch before
                # polling for a new one.
                await response_handler
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
           InvalidEscrowError, BillValidator, CoinAcceptor, SETUP_TIME_SECONDS)
