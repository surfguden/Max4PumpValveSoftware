import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import serial
from pumpvalve.drivers.tricontinent_valve import build_command
from pumpvalve.drivers.valve_session import (ValveError, ValveSession, ReplyTimeout, DetectedUnit,
                           parse_reply, validate_mappings, detected_valve_type)
from pumpvalve.drivers.valve_settings import Settings, UnitSettings, load_settings, save_settings


def reply(data='', ready=True, error=0):
    return b'/0' + bytes([(0x60 if ready else 0x40) | error]) + data.encode('ascii') + b'\x03\r\n'


class FakePort:
    def __init__(self, script):
        self.script = list(script)
        self.buffer = b''
        self.is_open = False
        self.closed = False
        self.sent = []
        self.options = None

    def factory(self, **options):
        self.options = options
        return self

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False
        self.closed = True

    def reset_input_buffer(self):
        self.buffer = b''

    def write(self, command):
        self.sent.append(command)
        expected, response = self.script.pop(0)
        if expected != command:
            raise AssertionError(f'Expected {expected!r}, received {command!r}')
        if isinstance(response, Exception):
            raise response
        self.buffer = response
        return len(command)

    @property
    def in_waiting(self):
        return len(self.buffer)

    def read(self, count):
        # Deliver fragments to exercise the framing loop.
        result, self.buffer = self.buffer[:1], self.buffer[1:]
        return result


def connection_script(firmware='VSeries: 051215', profile='4PORT-LOOP/9600/100K/AUTOINIT-ON'):
    return [(b'/3Q\r', reply()), (b'/3&\r', reply(firmware)),
            (b'/3?76\r', reply(profile)), (b'/3?6\r', reply('o'))]


class ProtocolTests(unittest.TestCase):
    def test_encoding(self):
        self.assertEqual(build_command(2, 'input'), b'/3IR\r')
        self.assertEqual(build_command(14, 'extra'), b'/FER\r')
        self.assertEqual(build_command(9, 'output'), b'/AOR\r')
        for address in (-1, 15):
            with self.assertRaises(ValueError):
                build_command(address, 'input')

    def test_reply(self):
        self.assertTrue(parse_reply(reply('i')).ready)
        self.assertFalse(parse_reply(reply(ready=False)).ready)
        self.assertEqual(parse_reply(reply('e')).data, 'e')
        self.assertEqual(parse_reply(b'\r\n' + reply('i')).data, 'i')

    def test_errors_and_malformed_replies(self):
        for value in (b'', b'/0`i', b'/1`i\x03', b'/0\x20i\x03',
                      b'/0`i\x03junk', b'/0`\xff\x03', reply(error=10)):
            with self.subTest(value=value), self.assertRaises(ValveError):
                parse_reply(value)

    def test_mapping(self):
        validate_mappings('input', 'extra')
        for pair in [('', ''), ('input', 'input'), ('bad', 'output'), ('input', '')]:
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                validate_mappings(*pair)


class SessionTests(unittest.TestCase):
    def session(self, script):
        port = FakePort(script)
        return ValveSession(port.factory), port

    def test_connect_and_cleanup(self):
        session, port = self.session(connection_script())
        self.assertEqual(session.connect('COM99')[2], 'output')
        self.assertTrue(session.verified)
        self.assertFalse(port.dtr)
        self.assertFalse(port.rts)
        self.assertEqual(port.options['baudrate'], 9600)
        self.assertFalse(port.options['xonxoff'])
        self.assertFalse(port.options['rtscts'])
        self.assertFalse(port.options['dsrdtr'])
        session.disconnect()
        self.assertTrue(port.closed)
        self.assertFalse(session.connected)
        self.assertFalse(session.verified)

    def test_wrong_device_query_only(self):
        for firmware, profile in [('C3000: 060314', '4PORT-LOOP'), ('VSeries: 051215', '3P-Y')]:
            session, port = self.session(connection_script(firmware, profile))
            session.connect('COM99')
            self.assertFalse(session.verified)
            with self.assertRaises(ValveError):
                session.move('input')
            self.assertEqual(len(port.sent), 4)

    def test_busy_then_ready_and_verified(self):
        script = connection_script() + [(b'/3Q\r', reply()), (b'/3IR\r', reply(ready=False)),
            (b'/3Q\r', reply(ready=False)), (b'/3Q\r', reply()), (b'/3?6\r', reply('i'))]
        session, port = self.session(script)
        session.connect('COM99')
        self.assertEqual(session.move('input'), 'input')
        self.assertEqual(port.script, [])

    def test_already_busy_prevents_movement(self):
        session, port = self.session(connection_script() + [(b'/3Q\r', reply(ready=False))])
        session.connect('COM99')
        with self.assertRaisesRegex(ValveError, 'busy'):
            session.move('input')
        self.assertNotIn(b'/3IR\r', port.sent)

    def test_position_mismatch(self):
        session, port = self.session(connection_script() + [(b'/3Q\r', reply()),
            (b'/3IR\r', reply()), (b'/3Q\r', reply()), (b'/3?6\r', reply('o'))])
        session.connect('COM99')
        with self.assertRaisesRegex(ValveError, 'mismatch'):
            session.move('input')

    def test_device_error(self):
        session, port = self.session(connection_script() + [(b'/3Q\r', reply()),
            (b'/3IR\r', reply(error=10))])
        session.connect('COM99')
        with self.assertRaisesRegex(ValveError, 'Valve overload'):
            session.move('input')

    def test_connection_failure_releases_port(self):
        session, port = self.session([(b'/3Q\r', b'bad\x03')])
        with self.assertRaises(ValveError):
            session.connect('COM99')
        self.assertTrue(port.closed)

    def test_serial_failure_releases_port(self):
        session, port = self.session(connection_script() + [(b'/3?6\r', serial.SerialException('unplugged'))])
        session.connect('COM99')
        with self.assertRaises(serial.SerialException):
            session.query_position()
        self.assertTrue(port.closed)
        self.assertFalse(session.verified)

    def test_reply_timeout(self):
        session, port = self.session(connection_script() + [(b'/3Q\r', b'')])
        session.connect('COM99')
        with self.assertRaisesRegex(ValveError, 'timed out'):
            session.exchange('Q', timeout=0.01)

    def test_motion_timeout(self):
        session, port = self.session(connection_script())
        session.connect('COM99')
        # Use a controlled clock rather than a sub-timer-resolution Windows wait.
        with patch('pumpvalve.drivers.valve_session.time.monotonic', side_effect=[0, 0, 0, 0.1, 0.21]), \
             patch.object(session.cancel, 'wait', return_value=False), \
             patch.object(session, 'exchange', side_effect=[parse_reply(reply()),
                 parse_reply(reply(ready=False)), parse_reply(reply(ready=False))]) as exchange:
            with self.assertRaisesRegex(ValveError, 'Motion timed out'):
                session.move('input', timeout=0.2)
            self.assertEqual(exchange.call_count, 3)
        self.assertEqual(port.script, [])

    def test_cancel_and_reconnect(self):
        session, port = self.session(connection_script() + connection_script())
        session.connect('COM99')
        session.cancel.set()
        with self.assertRaisesRegex(ValveError, 'cancelled'):
            session.query_position()
        session.disconnect()
        session.connect('COM99')
        self.assertFalse(session.cancel.is_set())
        self.assertTrue(session.connected)

    def test_unknown_position(self):
        session, port = self.session(connection_script() + [(b'/3?6\r', reply('1'))])
        session.connect('COM99')
        with self.assertRaisesRegex(ValveError, 'Unrecognized'):
            session.query_position()


class SettingsTests(unittest.TestCase):
    def test_roundtrip_in_path_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix='Valve settings ') as folder:
            path = Path(folder) / 'settings.json'
            self.assertEqual(load_settings(path), Settings())
            settings = Settings('COM12', 38400, [UnitSettings('Loop', 14, '4-port loop', 'bypass', 'extra'),
                UnitSettings('Pump', 0, '3-port valve', 'input', 'output')])
            save_settings(path, settings)
            self.assertEqual(load_settings(path), settings)

    def test_corrupt_and_invalid_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            for data in ('broken', '{"address":15}', '{"open_position":"bad"}', '{"unknown":1}',
                         '{"units":null}', '{"version":99,"units":[]}', '[]'):
                path.write_text(data)
                with self.subTest(data=data), self.assertRaises((ValueError, TypeError)):
                    load_settings(path)

    def test_save_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(Path, 'write_text', side_effect=PermissionError('Read only')):
                with self.assertRaises(PermissionError):
                    save_settings(Path(folder) / 'settings.json', Settings())

    def test_legacy_configuration_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            path.write_text('{"port":"COM7","baudrate":38400,"address":9,"open_position":"input","close_position":"extra"}')
            settings = load_settings(path)
            self.assertEqual(settings, Settings('COM7', 38400, [UnitSettings('Unit 1', 9, '4-port loop', 'input', 'extra')]))
            save_settings(path, settings)
            self.assertIn('"version": 3', path.read_text())
            self.assertEqual(load_settings(path), settings)

    def test_duplicate_addresses_and_unsupported_extra(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate address'):
            Settings(units=[UnitSettings('A', 0), UnitSettings('B', 0)]).validate()
        with self.assertRaisesRegex(ValueError, 'unsupported position'):
            UnitSettings('Three', 0, '3-port valve', 'input', 'extra').validate()

    def test_partial_mappings_can_be_saved(self):
        Settings(units=[UnitSettings(open_position='input')]).validate()


class BusTests(unittest.TestCase):
    def test_detected_controller_and_pump_profiles(self):
        self.assertEqual(detected_valve_type('VSeries: 051215', '3PORT-Y/9600/100K'), '3-port valve')
        self.assertEqual(detected_valve_type('VSeries: 051215', '4PORT-LOOP/9600/100K'), '4-port loop')
        self.assertEqual(detected_valve_type('C3000: 060314', '3P-Y/9600/100K'), '3-port valve')
        self.assertEqual(detected_valve_type('C3000: 060314', 'LOOP/9600/100K'), '4-port loop')
        self.assertEqual(detected_valve_type('VSeries: 051215', '6WD/9600/100K'), 'unknown')
        self.assertEqual(detected_valve_type('Other', '4PORT-LOOP'), 'unknown')

    def test_scan_0_to_e_sends_1_to_f_and_infers_capabilities(self):
        script = []
        for address in range(15):
            wire = f'/{address + 1:X}'
            if address not in (2, 3):
                script.append(((wire + 'Q\r').encode(), ReplyTimeout('Absent')))
            else:
                profile = '4PORT-LOOP' if address == 2 else '3PORT-Y'
                script.extend([((wire + 'Q\r').encode(), reply()),
                    ((wire + '&\r').encode(), reply('VSeries: 051215')),
                    ((wire + '?76\r').encode(), reply(profile + '/9600/100K/AUTOINIT-ON')),
                    ((wire + '?6\r').encode(), reply('o'))])
        port = FakePort(script)
        session = ValveSession(port.factory)
        session.open_bus('COM99')
        session.verified_units[0] = '3-port valve'
        units = session.scan()
        self.assertEqual([unit.address for unit in units], [2, 3])
        self.assertEqual([unit.valve_type for unit in units], ['4-port loop', '3-port valve'])
        self.assertEqual(session.verified_units, {2: '4-port loop', 3: '3-port valve'})
        self.assertEqual(port.script, [])
        self.assertIn(b'/1Q\r', port.sent)
        self.assertIn(b'/FQ\r', port.sent)
        self.assertTrue(all(command.endswith(b'\r') and not command.endswith(b'R\r') for command in port.sent))

    def test_scan_keeps_responding_unsupported_and_error_units_query_only(self):
        session = ValveSession()
        def exchange(command, **kwargs):
            address = kwargs['address']
            if address == 0:
                if command == '&':
                    return parse_reply(reply('VSeries: 051215'))
                if command == '?76':
                    return parse_reply(reply('6WD/9600/100K'))
                if command == '?6':
                    return parse_reply(reply('1'))
                return parse_reply(reply())
            if address == 1:
                raise ValveError('Device error 10')
            raise ReplyTimeout('Absent')
        with patch.object(session, 'exchange', side_effect=exchange):
            units = session.scan()
        self.assertEqual([unit.address for unit in units], [0, 1])
        self.assertEqual(units[0].valve_type, 'unknown')
        self.assertIn('Unrecognized', units[0].error)
        self.assertIn('Device error', units[1].error)
        self.assertEqual(session.verified_units, {})

    def test_mixed_bus_routes_all_queries_and_moves_to_correct_address(self):
        script = connection_script() + [
            (b'/1Q\r', reply()), (b'/1&\r', reply('C3000: 060314')),
            (b'/1?76\r', reply('3P-Y/9600/100K')), (b'/1?6\r', reply('o')),
            (b'/1Q\r', reply()), (b'/1BR\r', reply()),
            (b'/1Q\r', reply()), (b'/1?6\r', reply('b')),
            (b'/3Q\r', reply()), (b'/3ER\r', reply()),
            (b'/3Q\r', reply()), (b'/3?6\r', reply('e'))]
        port = FakePort(script)
        session = ValveSession(port.factory)
        session.open_bus('COM99')
        session.identify(2, '4-port loop')
        session.identify(0, '3-port valve')
        self.assertEqual(session.verified_units, {2: '4-port loop', 0: '3-port valve'})
        self.assertEqual(session.move('bypass', address=0, valve_type='3-port valve'), 'bypass')
        self.assertEqual(session.move('extra', address=2, valve_type='4-port loop'), 'extra')
        self.assertEqual(port.script, [])
        session.disconnect()
        self.assertEqual(session.verified_units, {})

    def test_extra_rejected_for_three_port_without_writing(self):
        port = FakePort([(b'/1Q\r', reply()), (b'/1&\r', reply('C3000: 060314')),
            (b'/1?76\r', reply('3P-Y/9600/100K')), (b'/1?6\r', reply('o'))])
        session = ValveSession(port.factory)
        session.open_bus('COM99')
        session.identify(0, '3-port valve')
        with self.assertRaisesRegex(ValveError, 'not supported'):
            session.move('extra', address=0)
        with self.assertRaisesRegex(ValveError, 'identification'):
            session.move('input', address=1)
        with self.assertRaisesRegex(ValveError, 'identification'):
            session.move('input', address=0, valve_type='4-port loop')
        self.assertEqual(len(port.sent), 4)

    def test_profile_mismatch_is_local_to_one_unit(self):
        port = FakePort(connection_script() + [(b'/1Q\r', reply()),
            (b'/1&\r', reply('C3000: 060314')), (b'/1?76\r', reply('4P-90/9600/100K')),
            (b'/1?6\r', reply('o'))])
        session = ValveSession(port.factory)
        session.open_bus('COM99')
        session.identify(2, '4-port loop')
        session.identify(0, '3-port valve')
        self.assertEqual(session.verified_units, {2: '4-port loop'})

    def test_failed_reidentification_clears_only_target_verification(self):
        port = FakePort(connection_script() + [(b'/3Q\r', reply(error=10))])
        session = ValveSession(port.factory)
        session.connect('COM99')
        with self.assertRaises(ValveError):
            session.identify(2, '4-port loop')
        self.assertEqual(session.verified_units, {})
        self.assertTrue(session.connected)


if __name__ == '__main__':
    unittest.main()
