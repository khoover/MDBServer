from abc import ABC
import argparse
import asyncio
from serial_asyncio import open_serial_connection
import websockets


class USBReader:
    pass


class Peripheral(ABC):
    pass


class BillValidator(Peripheral):
    pass


class CoinAcceptor(Peripheral):
    pass


class Master:
    pass


class Slave(ABC):
    pass


class CashlessSlave(Slave):
    pass


class WebsocketClient:
    pass


def main(args):
    pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Starts the ChezBob MDB server.")
    loop = asyncio.get_event_loop()
    loop.call_soon(main, parser.parse_args())
    loop.run_forever()
