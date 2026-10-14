"""A dedicated serialized owner for each bus, with priority stop requests."""
from concurrent.futures import Future, TimeoutError
from itertools import count
from queue import PriorityQueue
from threading import Thread, Lock


class BusWorker:
    def __init__(self, name):
        self.name = name
        self.queue = PriorityQueue()
        self.counter = count()
        self.stalled = False
        self.closed = False
        self.lock = Lock()
        self.thread = Thread(target=self._run, name=name, daemon=True)
        self.thread.start()

    def submit(self, action, *, priority=10, recovery=False):
        future = Future()
        with self.lock:
            if self.closed or (self.stalled and not recovery):
                future.set_exception(RuntimeError(f'{self.name} unavailable; restart after resolving stalled I/O'))
            else:
                self.queue.put((priority, next(self.counter), action, future, recovery))
        return future

    def call(self, action, timeout=5, *, recovery=False, priority=10):
        future = self.submit(action, recovery=recovery, priority=priority)
        try:
            return future.result(timeout)
        except TimeoutError as exc:
            self.stalled = True
            raise TimeoutError(f'{self.name} I/O deadline exceeded; state unverified. '
                               'Stop requested; restart required if native I/O does not return.') from exc

    def _run(self):
        while True:
            _, _, action, future, recovery = self.queue.get()
            if action is None:
                return
            if not future.set_running_or_notify_cancel():
                continue
            try:
                if self.stalled and not recovery:
                    raise RuntimeError(f'{self.name} quarantined after I/O timeout')
                future.set_result(action())
            except BaseException as exc:
                future.set_exception(exc)

    def close(self):
        with self.lock:
            self.closed = True
            self.queue.put((100, next(self.counter), None, None, True))
