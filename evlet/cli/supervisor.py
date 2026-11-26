"""The endpoint's supervisor: each instance of the project as a child process (docs/project-endpoint.md#supervisor).

A child is `evlet dev NAME --no-link` in development (it reloads app source) and `evlet serve NAME --jobs` in
production, on 127.0.0.1 and the instance's own port. A child that exits without being asked is started again after
a pause that grows with each crash in a row (1, 2, 5, 10 then 30 seconds) and resets once it has run for a minute.
stop() and start() need no sudo: the endpoint owns the processes. Each child's last lines of output are kept in
memory for instance_logs; they can hold values from tracebacks.
"""

from collections import deque
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

BACKOFF = (1, 2, 5, 10, 30)
STEADY = 60.0
LINES = 2000
STOP_SECONDS = 15.0


class Child:
    def __init__(self, name, port):
        self.name, self.port = name, port
        self.process = None
        self.wanted = False
        self.crashes = 0
        self.restarts = 0
        self.started = None
        self.next_start = None
        self.exit_code = None
        self.lines = deque(maxlen=LINES)

    @property
    def state(self):
        if self.process is not None and self.process.poll() is None:
            return 'running'
        if self.wanted:
            return 'restarting' if self.next_start is not None else 'starting'
        return 'stopped'


class Supervisor:
    """Run, watch and restart the instance children. command(name, port) gives a child's argument list."""

    def __init__(self, root, ports, *, development, python=None, backoff=BACKOFF, steady=STEADY, poll=0.5):
        self.root, self.development = Path(root), development
        self.python = python or sys.executable
        self.backoff, self.steady, self.poll = tuple(backoff), steady, poll
        self.children = {name: Child(name, port) for name, port in ports.items()}
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.watcher = None

    def command(self, child):
        base = [self.python, '-P', '-m', 'evlet', '--project', str(self.root)]
        if self.development:
            return [*base, 'dev', child.name, '--no-link', '--port', str(child.port)]
        return [*base, 'serve', child.name, '--jobs', '--port', str(child.port)]

    # ---------- lifecycle of the supervisor ----------

    def start_all(self, names=None):
        for name in sorted(self.children if names is None else names):
            self.start(name)
        if self.watcher is None:
            self.watcher = threading.Thread(target=self.watch, name='evlet-supervisor', daemon=True)
            self.watcher.start()

    def stop_all(self):
        self.stopping.set()
        for name in sorted(self.children):
            self.stop(name)
        if self.watcher is not None:
            self.watcher.join(timeout=5)

    def add(self, name, port):
        """Start supervising an instance created after the endpoint started."""
        with self.lock:
            if name not in self.children:
                self.children[name] = Child(name, port)
        return self.children[name]

    # ---------- one child ----------

    def child(self, name):
        found = self.children.get(name)
        if found is None:
            raise ValueError(f'The endpoint runs no instance named {name}')
        return found

    def start(self, name):
        child = self.child(name)
        with self.lock:
            child.wanted, child.next_start = True, None
            if child.state != 'running':
                self._spawn(child)
        return child

    def stop(self, name):
        child = self.child(name)
        with self.lock:
            child.wanted, child.next_start = False, None
            process = child.process
        if process is not None and process.poll() is None:
            self._signal(process, signal.SIGTERM)
            try:
                process.wait(STOP_SECONDS)
            except subprocess.TimeoutExpired:
                self._signal(process, signal.SIGKILL)
                process.wait()
        if process is not None:
            child.exit_code = process.returncode
        return child

    def restart(self, name):
        self.stop(name)
        return self.start(name)

    @contextmanager
    def paused(self, names):
        """Stop the running children among names for an operation that needs the instance's exclusive lock, and start
        them again afterwards, whether the operation succeeded or not."""
        stopped = [name for name in names if name in self.children and self.children[name].wanted]
        for name in stopped:
            self.stop(name)
        try:
            yield stopped
        finally:
            if not self.stopping.is_set():
                for name in stopped:
                    self.start(name)

    def logs(self, name, count=200):
        return list(self.child(name).lines)[-count:]

    def _spawn(self, child):
        environment = {**os.environ, 'PYTHONUNBUFFERED': '1'}
        environment.pop('EVLET_DELEGATED_PROJECT', None)
        child.lines.append(f'[endpoint] starting: {" ".join(self.command(child)[3:])}')
        process = subprocess.Popen(self.command(child), cwd=self.root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, env=environment, start_new_session=True)
        child.process, child.started, child.exit_code = process, time.monotonic(), None
        threading.Thread(target=self._read, args=(child, process), name=f'evlet-{child.name}-output', daemon=True).start()

    @staticmethod
    def _read(child, process):
        for raw in process.stdout:
            child.lines.append(raw.decode('utf-8', 'replace').rstrip('\n'))
        process.stdout.close()

    @staticmethod
    def _signal(process, number):
        try:
            os.killpg(process.pid, number)  # the child and the reloader's worker
        except (ProcessLookupError, PermissionError):
            pass

    def watch(self):
        while not self.stopping.wait(self.poll):
            for child in list(self.children.values()):
                with self.lock:
                    self._check(child)

    def _check(self, child):
        process = child.process
        if not child.wanted:
            return
        if process is not None and process.poll() is None:
            if child.crashes and time.monotonic() - child.started >= self.steady:
                child.crashes = 0
            return
        now = time.monotonic()
        if child.next_start is None:
            child.exit_code = process.returncode if process is not None else None
            if process is not None:
                self._signal(process, signal.SIGKILL)  # what the child left behind, such as the reloader's worker
            pause = self.backoff[min(child.crashes, len(self.backoff) - 1)]
            child.crashes += 1
            child.next_start = now + pause
            child.lines.append(f'[endpoint] {child.name} exited with code {child.exit_code}; starting it again in {pause} s')
        elif now >= child.next_start:
            child.next_start = None
            child.restarts += 1
            self._spawn(child)
