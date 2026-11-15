"""Bounded, process-wide HTTP work with a short waiting queue per lane; no executor per tenant.

A request waits up to the queue time for a slot in its lane, then gets 503. Bodies are read
before admission, so a slow client holds no slot. A cancelled ASGI waiter does not cancel its
synchronous work: that work retains its slot until its actual completion. See
docs/production-operations.md.
"""

import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import os
import threading


class Busy(Exception):
    """No slot within the queue time, or the application is draining."""


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


def _grant(future):
    if not future.done():
        future.set_result(None)


class _Lane:
    def __init__(self, name, limit, workers=None):
        self.limit = limit
        self.workers = 0 if name == 'stream' else workers or limit
        self._active = 0
        # Waiters in arrival order: (event loop, future). A released slot passes straight to the
        # first one, so a newcomer cannot overtake a queued request.
        self._waiters = deque()
        self._lock = threading.Lock()
        # Threads are created only as work arrives, and shared across every application/tenant.
        self.executor = None if name == 'stream' else ThreadPoolExecutor(self.workers, thread_name_prefix=f'evlet-http-{name}')

    async def acquire(self, timeout):
        """Take a slot, waiting at most timeout seconds for one, or raise Busy."""
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._active < self.limit and not self._waiters:
                self._active += 1
                return _Lease(self)
            entry = (loop, loop.create_future())
            self._waiters.append(entry)
        try:
            async with asyncio.timeout(timeout):
                await entry[1]
        except BaseException as exc:
            with self._lock:
                queued = entry in self._waiters
                if queued:
                    self._waiters.remove(entry)
            if not queued:
                # The slot was handed over just as the wait ended: pass it on.
                self.release()
            if isinstance(exc, TimeoutError):
                raise Busy() from None
            raise
        return _Lease(self)

    def release(self):
        with self._lock:
            while self._waiters:
                loop, future = self._waiters.popleft()
                try:
                    loop.call_soon_threadsafe(_grant, future)
                    return
                except RuntimeError:
                    continue  # Its event loop has closed; nobody is waiting there any more.
            self._active -= 1

    def active(self):
        with self._lock:
            return self._active

    def waiting(self):
        with self._lock:
            return len(self._waiters)


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
        self.queue_seconds = float(os.environ.get('EVLET_HTTP_QUEUE_SECONDS', '5'))
        if not 0 < self.queue_seconds <= 60:
            raise ValueError('EVLET_HTTP_QUEUE_SECONDS must be greater than 0 and at most 60')
        self.body_idle_seconds = float(os.environ.get('EVLET_HTTP_BODY_IDLE_SECONDS', '30'))
        if not 0 < self.body_idle_seconds <= 3600:
            raise ValueError('EVLET_HTTP_BODY_IDLE_SECONDS must be greater than 0 and at most 3600')
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

    def ticket(self):
        """The generation a request starts in; raises Busy while draining."""
        with self.lock:
            if self.stopping:
                raise Busy()
            return self.generation

    async def acquire(self, name, ticket=None):
        """Wait for a slot in lane name for a request that started in generation ticket."""
        ticket = self.ticket() if ticket is None else ticket
        with self.lock:
            if self.stopping or ticket != self.generation:
                raise Busy()
        current = runtime()
        lease = await current.lanes[name].acquire(current.queue_seconds)
        with self.lock:
            # A stop or restart while it waited refuses work from the previous generation.
            if self.stopping or ticket != self.generation:
                lease.release()
                raise Busy()
            lease.generation = ticket
            return lease

    async def run(self, name, function, *args, lease=None):
        own = lease is None
        lease = await self.acquire(name) if own else lease
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
