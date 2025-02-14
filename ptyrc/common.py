import json
import sys
import time
from base64 import b64decode, b64encode

import ptyrc.fake_pty as fake_pty

version = (1, 0, 0)
start_port = 34012
port_range = 10

global_buffer_size = fake_pty.BUFFER_SIZE
larger_buffer_size = global_buffer_size * 4 * 2

verbose_logs = False


def verbose(*kargs, **kwargs):
    if not verbose_logs:
        return
    print(*kargs, **kwargs, file=sys.stderr, end="\n\r")


class basic_handler:
    def __init__(self, remote, version=version):
        self.remote = remote
        self.version = version
        self.finished = False
        self.last_ping = 0
        self.exit_code = None

    def is_alive(self):
        return (
            (not self.finished)
            and (abs(self.last_ping - time.time()) < 2)
            and self.exit_code is None
        )

    def send(self, what, data):
        send_to_remote(self.remote, what, data)

    def close(self, reason):
        try:
            self.remote.shutdown()
            self.remote.close()
        except BaseException:
            pass

        self.finished = True
        raise BrokenPipeError(reason)

    #
    # commands
    #

    def exit(self, code):
        self.exit_code = code
        self.finished = True
        if not self.is_alive():
            self.close(f"remote exit with code {code}")

    #
    # ping-pong
    #

    def pong(self, pong_timestamp):
        self.last_ping = max(pong_timestamp, self.last_ping)

        # verbose(f'pong: {pong_timestamp}')
        if not self.is_alive():
            self.close("non-responsive remote")

    def ping(self, ping_timestamp):
        # verbose(f'ping: {ping_timestamp}')
        self.last_ping = max(ping_timestamp, self.last_ping)

        self.send(what="pong", data=time.time())
        if not self.is_alive():
            self.close("non-responsive remote")

    #
    # welcome message
    #

    def has_version(self, remote_version):
        # verbose(f'has_version {".".join(str(s) for s in remote_version)}')
        assert self.version == tuple(remote_version)

    def get_version(self, remote_version):
        # verbose(f'get_version {".".join(str(s) for s in remote_version)}')
        self.send(what="has_version", data=version)
        assert self.version == tuple(remote_version)

    #
    # default value handlers (to be overriden if needed)
    #

    values = dict()

    def cursor_position(self, new_position):
        # verbose(f'cursor_position {new_position}')
        self.values["cursor_position"] = new_position

    def terminal_size(self, new_size):
        # verbose(f'terminal_size {new_size}')
        self.values["terminal_size"] = new_size

    def argv_cmd(self, new_argv):
        # verbose(f'argv_cmd {new_argv}')
        self.values["argv_cmd"] = new_argv

    #
    # default stdin / stdout handlers (does nothing)
    #

    def stdin(self, data):
        pass

    def stdout(self, data):
        pass


def send_to_remote(remote, what, data):
    if remote is None:
        return

    if isinstance(data, bytes):
        data = dict(base64=b64encode(data).decode())
    rq = dict(what=what, data=data)

    raw = json.dumps(rq) + "\n"
    assert len(raw) < larger_buffer_size
    remote.sendall(raw.encode())


def recv_from_remote(remote, attempts=20):
    if remote is None:
        return

    data = remote.recv(larger_buffer_size)
    if b"\n" not in data:
        data += remote.recv(larger_buffer_size)
    if b"\n" not in data:
        return

    def _loader(blob):
        payloads = []
        for line in blob.split(b"\n"):
            if not line:
                continue
            payloads += [json.loads(line)]
        return payloads

    payloads = []
    for _ in range(attempts):
        try:
            payloads = _loader(data)
        except json.decoder.JSONDecodeError:
            data += remote.recv(larger_buffer_size)

    return payloads


def handle_remote(remote, handler, *, maxfails=10):
    send_to_remote(remote, what="get_version", data=version)

    fails = 0
    while True:
        if fails > maxfails:
            verbose("lost connection...")
            return

        payloads = recv_from_remote(remote)

        if payloads is None:
            send_to_remote(remote, what="ping", data=time.time())
            time.sleep(0.1)
            fails += 1
            continue

        for payload in payloads:
            if not isinstance(payload, dict):
                verbose(f"Unhandled raw data: {payload}")
                continue

            if "what" not in payload or "data" not in payload:
                verbose(f"Ill-formed incoming data: {payload}")
                continue

            method = getattr(handler, payload["what"], None)
            if method is None:
                verbose(f'Unknown {payload["what"]} here:\n\r {payload}')
                continue

            data = payload["data"]
            if isinstance(data, dict) and len(data) == 1 and "base64" in data:
                data = b64decode(data["base64"])

            if data is None:
                verbose(f'Null data (None) was send for {payload["what"]}')
                continue

            if isinstance(data, dict):
                method(**data)
            else:
                method(data)
