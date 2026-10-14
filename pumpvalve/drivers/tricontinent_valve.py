"""Change the valve position on a TriContinent C-Series syringe pump.

The C-Series DT protocol command format is:

    /<device-address><command>R<CR>

For RS232, the transmitted device address is the physical address-switch
position plus one. Therefore a pump with its address switch set to 1 is
addressed as device 2, for example ``/2IR\r``.
"""

from __future__ import annotations

import argparse
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError as exc:  # Give a useful message instead of a traceback.
    raise SystemExit(
        "pyserial is required. Install it with: python -m pip install pyserial"
    ) from exc


VALVE_COMMANDS = {
    "input": "I",
    "output": "O",
    "bypass": "B",
    "extra": "E",  # Only valid for a four-position valve.
}


def build_command(address_switch: int, valve_position: str) -> bytes:
    """Build a C-Series DT-mode valve command."""
    if not 0 <= address_switch <= 14:
        raise ValueError("The address switch must be between 0 and 14.")

    device_address = address_switch + 1
    command = VALVE_COMMANDS[valve_position]
    return f"/{device_address:X}{command}R\r".encode("ascii")


def read_reply(port: serial.Serial, overall_timeout: float = 1.0) -> bytes:
    """Read the complete reply, stopping after the serial line becomes quiet."""
    deadline = time.monotonic() + overall_timeout
    reply = bytearray()

    while time.monotonic() < deadline:
        chunk = port.read(port.in_waiting or 1)
        if chunk:
            reply.extend(chunk)
            # A DT response normally contains ETX. Collect any trailing CR/LF too.
            if b"\x03" in reply:
                time.sleep(0.02)
                reply.extend(port.read(port.in_waiting))
                break
        elif reply:
            break

    return bytes(reply)


def change_valve(
    port_name: str,
    valve_position: str,
    address_switch: int = 1,
    baudrate: int = 9_600,
    timeout: float = 0.25,
) -> bytes:
    """Open the serial port, move the valve, return the pump's raw reply."""
    message = build_command(address_switch, valve_position)

    with serial.Serial(
        port=port_name,
        baudrate=baudrate,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=timeout,
        write_timeout=1.0,
        rtscts=False,
        dsrdtr=False,
        xonxoff=False,
    ) as pump:
        pump.reset_input_buffer()
        pump.reset_output_buffer()
        pump.write(message)
        pump.flush()
        return read_reply(pump)


def available_ports() -> str:
    ports = [f"{p.device} ({p.description})" for p in list_ports.comports()]
    return ", ".join(ports) if ports else "none detected"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Set the valve position of a TriContinent C-Series pump."
    )
    parser.add_argument("port", help="Serial port, for example COM3 or /dev/ttyUSB0")
    parser.add_argument("position", choices=VALVE_COMMANDS, help="Desired valve position")
    parser.add_argument(
        "--address-switch",
        type=int,
        default=1,
        metavar="N",
        help="Physical pump address-switch setting (default: 1)",
    )
    parser.add_argument(
        "--baudrate",
        type=int,
        choices=(9600, 38400),
        default=9600,
        help="Serial baud rate selected by pump jumper J2-4 (default: 9600)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        message = build_command(args.address_switch, args.position)
        print(f"Opening {args.port} at {args.baudrate} baud (8-N-1)")
        print(f"Sending: {message!r}")
        reply = change_valve(
            port_name=args.port,
            valve_position=args.position,
            address_switch=args.address_switch,
            baudrate=args.baudrate,
        )
    except (serial.SerialException, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(f"Detected serial ports: {available_ports()}", file=sys.stderr)
        return 1

    if not reply:
        print("No reply received from the pump.", file=sys.stderr)
        return 2

    printable = reply.decode("ascii", errors="backslashreplace")
    print(f"Raw reply: {reply!r}")
    print(f"Text reply: {printable!r}")
    print(
        f"Command reply received for valve position '{args.position}'. "
        "The final position is not verified by this small utility; query ?6 "
        "after motion if confirmation is required."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
