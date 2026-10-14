"""Authenticated loopback JSON protocol; only one hardware owner per user-data folder."""
import json
import os
from pathlib import Path
import secrets
import socket
import socketserver
import threading
from .storage import data_dir

MAX_MESSAGE = 65536


class OwnerLock:
    def __init__(self, path):
        self.file = open(path, 'a+b')
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise RuntimeError('An application already owns this session. Use send/status/watch to attach.') from exc

    def close(self):
        self.file.close()


class Host:
    def __init__(self, commands):
        self.commands = commands
        self.path = data_dir() / 'endpoint.json'
        self.owner = OwnerLock(data_dir() / 'owner.lock')
        self.token = secrets.token_hex(32)
        host = self
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(5)
                try:
                    line = self.rfile.readline(MAX_MESSAGE+1)
                    if len(line) > MAX_MESSAGE or not line.endswith(b'\n'):
                        raise ValueError('Request too long or incomplete')
                    request = json.loads(line)
                    if not isinstance(request, dict) or not isinstance(request.get('token'), str) or not secrets.compare_digest(request['token'], host.token):
                        raise ValueError('Authentication failed')
                    response = host.commands.execute(request.get('command'), request.get('args', {}),
                        request_id=request.get('id'), source='terminal')
                except Exception as exc:
                    response = dict(ok=False, code=type(exc).__name__, error=str(exc))
                try:
                    self.wfile.write((json.dumps(response, allow_nan=False)+'\n').encode('utf-8'))
                except (OSError, ValueError):
                    pass
        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = False
        try:
            self.server = Server(('127.0.0.1', 0), Handler)
            descriptor = dict(port=self.server.server_address[1], token=self.token,
                              session=commands.events.session, pid=os.getpid())
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(descriptor), encoding='utf-8')
            if os.name != 'nt':
                tmp.chmod(0o600)
            tmp.replace(self.path)
            self.thread = threading.Thread(target=self.server.serve_forever, name='Command endpoint', daemon=True)
            self.thread.start()
        except Exception:
            self.owner.close()
            raise

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        try:
            if json.loads(self.path.read_text())['session'] == self.commands.events.session:
                self.path.unlink()
        except (OSError, ValueError, KeyError):
            pass
        self.owner.close()


def request(command, args=None, request_id=None, timeout=90):
    try:
        endpoint = json.loads((data_dir() / 'endpoint.json').read_text(encoding='utf-8'))
        payload = dict(token=endpoint['token'], command=command, args=args or {})
        if request_id:
            payload['id'] = request_id
        encoded = (json.dumps(payload, allow_nan=False)+'\n').encode('utf-8')
        if len(encoded) > MAX_MESSAGE:
            raise ValueError('Request exceeds 64 KiB')
        with socket.create_connection(('127.0.0.1', endpoint['port']), timeout=5) as sock:
            sock.settimeout(timeout)
            sock.sendall(encoded)
            with sock.makefile('rb') as stream:
                line = stream.readline(8_000_001)
                if len(line) > 8_000_000 or not line.endswith(b'\n'):
                    raise ValueError('Invalid server response')
                return json.loads(line)
    except (OSError, ValueError, KeyError) as exc:
        raise RuntimeError(f'Cannot contact application: {exc}. Start Max4PumpValveCtl run '
                           'or open the GUI; use the same Windows account and PUMPVALVE_DATA_DIR.') from exc
