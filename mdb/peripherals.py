from abc import ABC, abstractmethod
import asyncio
import functools
import logging
import time
from typing import Sequence, Dict

SETUP_TIME_SECONDS = 0.2


class NonResponseError(Exception):
    """Raised when a peripheral does not respond by the non-response deadline.

    Attributes:
        command -- The command that was supposed to be sent to the peripheral.
        peripheral -- The peripheral the command was being sent to."""
    def __init__(self, command, peripheral):
        self.command = command
        self.peripheral = peripheral


def reset_wrapper(timeout=None):
    """Convenient decorator for the 'try MDB communication, reset on
    non-response' pattern. Assumes the wrapped function is a method of
    Peripheral or an implementing subclass.

    :param timeout: How long to wait, in seconds, for the reset to occur before
    giving up. The timeout will be left unhandled. If None, will attempt to
    reset forever."""
    def inner(func):
        assert asyncio.iscoroutinefunction(func)
        @functools.wraps(func)
        async def wrapper(self: Peripheral, *args, **kwargs):
            try:
                await func(self, *args, **kwargs)
            except NonResponseError as e:
                self.logger.warning('Timed out communicating with %s, command '
                                    'was %r', e.peripheral, e.command,
                                    exc_info=e)
                if timeout:
                    try:
                        await asyncio.wait_for(self.reset(True, True), timeout)
                    except TimeoutError as timeout_error:
                        self.logger.error('Reset timed out for %s.',
                                          e.peripheral, exc_info=timeout_error)
                        raise timeout_error from None
                else:
                    await self.reset(True, True)
        return wrapper
    return inner


class Peripheral(ABC):
    """Generic implementation of an MDB peripheral interface.

    In addition to the methods decorated with abstractmethod, subclasses should
    also define the following class constants: NON_RESPONSE_SECONDS, ADDRESS,
    and COMMANDS."""
    lock: asyncio.Lock
    initialized: bool
    logger: logging.Logger
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
        self.lock = asyncio.Lock()
        self.initialized = False
        self.logger = logging.getLogger('.'.join((__name__,
                                                  self._class__.__name__)))

    async def initialize(self, master, send_reset=True) -> None:
        """Initialization code for the peripheral.

        Feel free to send messages here, the USB interface will be initialized
        before this is called.

        :param master: the Master instance controlling this peripheral
        :param send_reset: whether to send a reset command or not"""
        self.logger.debug("Initializing peripheral of type %s.",
                          self.__class__.__name__)
        self.master = master
        try:
            await asyncio.wait_for(self.reset(send_reset, True),
                                   self.INIT_RESET_TIMEOUT)
        except asyncio.TimeoutError:
            self.logger.critical('Unable to initialize peripheral: %s',
                                 self.__class__.__name__)
            raise
        self.initialized = True

    async def send(self, message: str) -> None:
        async with self.lock:
            await self.master.send(message)

    async def send_nolock(self, message: str) -> None:
        """Sends a message without acquiring the peripheral's lock. Assumes the
        caller or a parent has acquired the lock. Must not be used to
        circumvent the locking mechanism."""
        assert self.lock.locked()
        await self.master.send(message)

    async def sendread(self, message: str) -> str:
        async with self.lock:
            return await self.master.sendread(message,
                                              self.BOARD_RESPONSE_PREFIX)

    async def sendread_nolock(self, message: str) -> str:
        """Similar to send_nolock, except for sendread."""
        assert self.lock.locked()
        return await self.master.sendread(message, self.BOARD_RESPONSE_PREFIX)

    # Next are utility methods so that implementations only have to worry about
    # handling the reset after the non-response timeout.
    async def sendread_until_timeout(self, message: str) -> str:
        non_response_reply = self.BOARD_RESPONSE_PREFIX + ',NACK'
        message_status = await self.sendread(message)
        start = time.time()
        while message_status == non_response_reply and \
                time.time() - start < self.NON_RESPONSE_SECONDS:
            asyncio.sleep(self.POLLING_INTERVAL_SECONDS)  # Ratelimiting
            message_status = await self.sendread(message,
                                                 self.BOARD_RESPONSE_PREFIX)
        if message_status == non_response_reply:
            raise NonResponseError(message, self.__class__.__name__)
        return message_status

    async def sendread_nolock_until_timeout(self, message: str) -> str:
        non_response_reply = self.BOARD_RESPONSE_PREFIX + ',NACK'
        message_status = await self.sendread_nolock(message)
        start = time.time()
        while message_status == self.BOARD_RESPONSE_PREFIX + ',NACK' and \
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
    async def reset(self, send_reset=True, poll_reset=True) -> None:
        """Resets the peripheral.

        :param send_reset: Whether to send the reset command or not."""
        pass

    @abstractmethod
    async def enable(self) -> None:
        """Enables the peripheral for vending activities."""
        assert self.initialized

    @abstractmethod
    async def disable(self) -> None:
        """Disables the peripheral for vending activities."""
        assert self.initialized

    @abstractmethod
    async def run(self) -> None:
        """Does whatever persistent action is needed."""
        assert self.initialized

    @classmethod
    def create_address_byte(cls, command: str) -> str:
        combined_hex = cls.ADDRESS | cls.COMMANDS[command]
        return f"{combined_hex:x}"


class BillValidator(Peripheral):
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

    async def reset(self, send_reset=True, poll_reset=True) -> None:
        """Resets the bill validator.

        Will only return once the validator is finished the reset command
        sequence; should be wrapped with asyncio.wait_for if you want to
        timeout the attempts.

        :param send_reset: Whether to send a reset command.
        :param poll_reset: Whether to poll for a JUST RESET."""
        # assert (send_reset implies poll_reset)
        assert (not send_reset) or poll_reset
        await super().reset(send_reset, poll_reset)
        with self.lock:
            while True:
                try:
                    if send_reset:
                        command = f"R,{self.create_address_byte('RESET')}\n"
                        self.logger.info('Sending reset command to bill '
                                         'validator.')
                        response = await self.sendread_nolock(command)
                        if response == 'p,ACK':
                            await asyncio.sleep(SETUP_TIME_SECONDS)
                        else:
                            self.logger.warning('No response to RESET, '
                                                'sleeping and retrying.')
                            await asyncio.sleep(self.RESET_RETRY_SLEEP)
                            # send_reset = True
                            # poll_reset = True, from the assert.
                            continue
                    # Poll until JUST RESET
                    if poll_reset:
                        self.logger.info('Polling bill validator for JUST '
                                         'RESET.')
                        response = \
                            await self.sendread_nolock_until_data_or_nack(
                                self.POLL_COMMAND)
                        response_statuses = [int(response[i:i+2], base=16) for
                                             i in range(2, len(response), 2)]
                        # 0x06 is the code for JUST RESET.
                        if 0x06 not in response_statuses:
                            self.logger.warning("Did not get JUST RESET in the"
                                                " first poll after resetting,"
                                                " trying again.")
                            send_reset = True
                            # poll_reset = True
                            continue
                        # Pass off the remaining responses to the poll handler
                        # in the background.
                        asyncio.create_task(self.handle_poll_responses(
                            [x for x in response_statuses if x != 0x06]))

                    self.logger.info('Getting bill validator setup '
                                     'information.')
                    setup_data = await self.sendread_nolock_until_data_or_nack(
                        "R," + self.create_address_byte('SETUP') + "\n")
                    setup_data_bytes = [setup_data[i:i+2] for
                                        i in range(2, len(setup_data), 2)]
                    self.feature_level = int(setup_data_bytes[0], base=16)
                    self.logger.debug('Bill validator level: %d',
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
                    self.has_escrow = setup_data_bytes[10].upper() == 'FF'
                    self.bill_values = [int(x, base=16) for x in
                                        setup_data_bytes[11:]]

                    self.logger.info('Getting bill validator expansion '
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
                    self.logger.info('Got expansion data for bill validator: '
                                     '%r', self.expansion_data)

                    self.logger.info('Getting stacked bill count.')
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
                    # TODO: Figure out how this should be converted into a
                    # bitvector. It's not clear if I need to reverse the list
                    # before doing the shift-and-add, or if something else
                    # weird happens.
                    self.bill_enable_bitvector = int(''.join(bills_to_enable),
                                                     base=2)
                    self.enable_command = \
                        f"R,{self.create_address_byte('BILL TYPE')}," \
                        f"{self.bill_enable_bitvector:x}" \
                        f"{self.bill_enable_bitvector:x}\n"
                    await self.sendread_nolock_until_timeout(
                        self.enable_command)
                except NonResponseError as e:
                    self.logger.warning("Bill validator timed out while "
                                        "resetting, command was '%r'.",
                                        e.command, exc_info=e)
                    send_reset = True
                    poll_reset = True

    async def handle_poll_responses(self, responses: Sequence[int]) -> None:
        reset_task = None
        for response in responses:
            if response in self.POLL_CRITICAL_STATUSES:
                self.logger.critical(self.POLL_CRITICAL_STATUSES[response])
            elif response in self.POLL_INFO_STATUSES:
                self.logger.info(self.POLL_INFO_STATUSES[response])
            elif response in self.POLL_WARNING_STATUSES:
                self.logger.warning(self.POLL_WARNING_STATUSES[response])
            elif response & 0x80 == 0x80:
                # This is a payment code, should do something about that.
                pass
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
                self.logger.info('Since last poll, people tried inserting %d '
                                 '%s while the validator was disabled.', count,
                                 bill)
            else:
                self.logger.warning('Unknown poll response received: %#02d',
                                    response)
        if reset_task:
            await reset_task

    @reset_wrapper
    async def enable(self) -> None:
        await super().enable()
        await self.sendread_until_timeout(self.enable_command)

    @reset_wrapper
    async def disable(self) -> None:
        await super().disable()
        disable_command = f"R,{self.create_address_byte('BILL TYPE')},0000\n"
        await self.sendread_until_timeout(disable_command)

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
                self.logger.warning('Bill validator timed out while polling, '
                                    'resetting.', exc_info=e)
                await self.reset(True, True)


class CoinAcceptor(Peripheral):
    pass


__all__ = (Peripheral, NonResponseError, BillValidator, CoinAcceptor)
