"""Bounded, process-wide HTTP work; no executor or waiting queue per tenant.

Admission precedes body reads. A cancelled ASGI waiter does not cancel its synchronous work:
that work retains its slot until its actual completion. See docs/production-operations.md.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import os
import threading


class Busy(Exception):
    """No capacity, or the application is draining."""


class _Lease:
    def __init__(self, lane):
        self.lane = lane
        self._references = 1
        self._lock = threading.Lock()

    def retain(self):
        with self._lock:
            self._references += 1

    def release(self):
        with self._lock:
            self._references -= 1
            if self._references == 0:
                self.lane.release()


class _Lane:
    def __init__(self, name, limit, workers=None):
        self.limit = limit
        self.workers = 0 if name == 'stream' else workers or limit
        self.slots = threading.BoundedSemaphore(limit)
        self._active = 0
        self._counter_lock = threading.Lock()
        # Threads are created only as work arrives, and shared across every application/tenant.
        self.executor = None if name == 'stream' else ThreadPoolExecutor(self.workers, thread_name_prefix=f'evlet-http-{name}')

    def acquire(self):
        if not self.slots.acquire(blocking=False):
            raise Busy()
        with self._counter_lock:
            self._active += 1
        return _Lease(self)

    def release(self):
        with self._counter_lock:
            self._active -= 1
        self.slots.release()

    def active(self):
        with self._counter_lock:
            return self._active


class HTTPRuntime:
    DEFAULTS = {'read': 32, 'write': 4, 'probe': 2, 'heavy': 2, 'stream': 64, 'route': 8, 'init': 8}

    def __init__(self, limits=None):
        values = dict(self.DEFAULTS)
        for name in values:
            value = (limits or {}).get(name, os.environ.get(f'EVLET_HTTP_{name.upper()}', values[name]))
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ValueError(f'EVLET_HTTP_{name.upper()} must be an integer from 1 to 256') from None
            if not 1 <= value <= 256:
                raise ValueError(f'EVLET_HTTP_{name.upper()} must be an integer from 1 to 256')
            values[name] = value
        read_workers = int(os.environ.get('EVLET_HTTP_READ_WORKERS', min(8, values['read'])))
        if not 1 <= read_workers <= values['read']:
            raise ValueError('EVLET_HTTP_READ_WORKERS must be from 1 to EVLET_HTTP_READ')
        self.lanes = {name: _Lane(name, limit, read_workers if name == 'read' else None) for name, limit in values.items()}
        self.body_seconds = float(os.environ.get('EVLET_HTTP_BODY_SECONDS', '30'))
        if not 0 < self.body_seconds <= 3600:
            raise ValueError('EVLET_HTTP_BODY_SECONDS must be greater than 0 and at most 3600')
        self.log_requests = os.environ.get('EVLET_HTTP_LOG_REQUESTS') == '1'

    def close(self):
        """Used by isolated runtime tests; normal processes share the lanes until interpreter exit."""
        for lane in self.lanes.values():
            if lane.executor is not None:
                lane.executor.shutdown(wait=True)


_runtime = None
_runtime_lock = threading.Lock()


def runtime():
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = HTTPRuntime()
        return _runtime


class Work:
    """Track one application's real synchronous work across cancellations and event loops."""

    def __init__(self):
        self.lock = threading.Lock()
        self.futures = set()
        self.stopping = False
        self.generation = 0

    def start(self):
        with self.lock:
            if self.futures:
                raise RuntimeError('Cannot restart while HTTP work is draining')
            self.stopping = False
            self.generation += 1

    def stop(self):
        with self.lock:
            self.stopping = True

    def acquire(self, name):
        with self.lock:
            if self.stopping:
                raise Busy()
            lease = runtime().lanes[name].acquire()
            lease.generation = self.generation
            return lease

    async def run(self, name, function, *args, lease=None):
        own = lease is None
        lease = self.acquire(name) if own else lease
        try:
            context = copy_context()
            with self.lock:
                if self.stopping or lease.generation != self.generation:
                    raise Busy()
                lease.retain()
                try:
                    future = lease.lane.executor.submit(context.run, function, *args)
                except BaseException:
                    lease.release()
                    raise
                self.futures.add(future)
            def completed(future):
                lease.release()
                with self.lock:
                    self.futures.discard(future)
            future.add_done_callback(completed)
            # Shield the underlying work: releasing admission on waiter cancellation would admit
            # replacements while the old threads still hold locks and memory.
            wrapped = asyncio.wrap_future(future)
            wrapped.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            return await asyncio.shield(wrapped)
        finally:
            if own:
                lease.release()

    async def drain(self):
        self.stop()
        while True:
            with self.lock:
                if not self.futures:
                    return
            await asyncio.sleep(.01)
