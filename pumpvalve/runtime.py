"""One device-owner process, optional GUI and attached terminal."""
import threading
from .commands import Commands
from .ipc import Host
from .logging_support import configure


def run(gui=True, attached=False, stdin=False, smoke_report=None):
    configure()
    commands = Commands()
    host = None
    threads = []
    previous_signal = None
    try:
        host = Host(commands)
        if attached:
            import signal
            previous_signal = signal.signal(signal.SIGINT, lambda *_: commands.execute('shutdown', source='terminal'))
            from .cli import watch, stdin_loop, output
            def stream():
                try:
                    watch(stop=commands.closed)
                except Exception as exc:
                    if not commands.closed.is_set():
                        output({'event': 'console.error', 'error': str(exc)})
            t = threading.Thread(target=stream, name='Attached event stream', daemon=True)
            t.start()
            threads.append(t)
            if stdin:
                t = threading.Thread(target=stdin_loop, args=(commands.closed,), name='Console input', daemon=True)
                t.start()
        if gui:
            import sys
            from PySide6.QtWidgets import QApplication
            from PySide6.QtCore import QTimer
            from .ui import MainWindow
            qt = QApplication(sys.argv[:1])
            window = MainWindow(commands)
            window.show()
            timer = QTimer(window)
            timer.timeout.connect(lambda: window.close() if commands.closed.is_set() else None)
            timer.start(100)
            if smoke_report:
                from .smoke import smoke
                QTimer.singleShot(100, lambda: smoke(qt, commands.app, window, smoke_report))
            return qt.exec()
        try:
            while not commands.closed.wait(0.2):
                pass
        except KeyboardInterrupt:
            commands.execute('shutdown', source='terminal')
        return 0
    finally:
        commands.closed.set()
        for thread in threads:
            thread.join(2)
        if host:
            host.close()
        commands.close()
        if previous_signal is not None:
            import signal
            signal.signal(signal.SIGINT, previous_signal)
