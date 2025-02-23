import atexit
import fcntl
import os
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import termios
import threading
import time
import tty

import pyte

import ptyrc.common as common
import ptyrc.fake_pty as fake_pty
from ptyrc.common import verbose
from ptyrc.termcap import ansiseq, charspec


class client_handler(common.basic_handler):
    values_from_parent = [
        "terminal_size",
        "argv_cmd",
        "cursor_position",
        "has_smcup",
        "first_write",
    ]
    values_from_self = []

    def __init__(self, parent, remote, version=common.version):
        super().__init__(remote=remote, version=version)
        self.parent = parent

    def kill(self, code):
        os._exit(code)

    def get_value(self, value_name):
        if value_name in self.values_from_parent:
            value = getattr(self.parent, value_name)
            if value is not None:
                self.send(what=value_name, data=value)

        elif value_name in self.values_from_self:
            value = getattr(self, value_name)
            if value is not None:
                self.send(what=value_name, data=value)

        else:
            verbose(f"Unknown get_value: {value_name}")

    def command(self, command_name):

        def _handle_seq(name, value):
            nonlocal command_name
            nonlocal self

            if name != command_name:
                return

            if self.pyte_screen is None:
                self.pyte_buffer += value
            os.write(sys.stdin.fileno(), value)

        if command_name == "refresh_lines" and self.parent.terminal_size is not None:
            self.get_lines(list(range(self.parent.terminal_size[1])))
            return
        if command_name == "refresh_rawlines" and self.parent.terminal_size is not None:
            self.get_rawlines(list(range(self.parent.terminal_size[1])))
            return

        if command_name.startswith(("enable_", "disable_")):
            boolean_value = command_name.startswith("enable_")
            _, boolean_name = command_name.split("_", 1)

            try:
                what = getattr(self.parent, "_cfg_" + boolean_name)
                assert isinstance(what, bool)
                setattr(self.parent, "_cfg_" + boolean_name, boolean_value)
            except AttributeError as e:
                verbose(f"Unknown boolean: {boolean_name}")
            return

        if (
            False
            or _handle_seq("terminal_reset", ansiseq.reset)
            or _handle_seq("terminal_clear", ansiseq.clear)
            or _handle_seq("terminal_cup00", ansiseq.cup00)
            or _handle_seq("terminal_smcup", ansiseq.smcup)
            or _handle_seq("terminal_rmcup", ansiseq.rmcup)
        ):
            return

        verbose(f"Unknown command: {command_name}")
        return

    def get_lines(self, linelist):
        if self.parent.pyte_screen is None:
            return

        display = self.parent.pyte_screen.display

        linelist.sort()
        for lineno in linelist:
            if lineno >= len(display):
                continue

            self.send(what="set_line", data=dict(where=lineno, line=display[lineno]))

    def get_rawlines(self, linelist):
        if self.parent.pyte_screen is None:
            return

        buffer = self.parent.pyte_screen.buffer
        nbcols = self.parent.pyte_screen.columns

        linelist.sort()
        for lineno in linelist:
            if lineno >= len(buffer):
                continue

            packedline = b""
            for pytechar in [buffer[lineno][x] for x in range(nbcols)]:
                packedline += charspec(**pytechar._asdict()).pack()

            rawline = common.b64encode(packedline).decode()
            self.send(what="set_rawline", data=dict(where=lineno, rawline=rawline))

    def write_to_tty(self, input_bytes):
        if self.parent.child_fd is not None:
            os.write(self.parent.child_fd, input_bytes)

    def draw(self, where, char, attrs=None):
        if not ansiseq.ready:
            ansiseq.initialize()

        seq = ansiseq.sc + ansiseq.cup(*where)
        if attrs is None and isinstance(char, str):
            seq += char.encode()
        else:
            char = charspec(data=char, **attrs)
            seq += ansiseq.sgr0
            seq += char.seq
            seq += char.data
        seq += ansiseq.rc

        os.write(fake_pty.STDOUT_FILENO, seq)

class pty_driver:

    def __init__(
        self,
        argv_cmd,
        *,
        initial_latency=1,
        start_port=common.start_port,
        port_range=common.port_range,
        maxfails=10,
        version=common.version,
    ):
        ansiseq.initialize()

        self.initial_latency = initial_latency
        self.start_port = start_port
        self.port_range = port_range
        self.maxfails = maxfails
        self.version = version

        self.jobs = []
        self.child_fd = -1

        self.handler = None
        self.active_client = None

        self.pyte_screen = None
        self.pyte_buffer = b""

        self.cursor_moved = False
        self.first_write = None
        self.early_buffer = b""
        self.has_smcup = False

        self.argv_cmd = argv_cmd
        self.cursor_position = None
        self.terminal_size = None

        self._cfg_stream_lines = True
        self._cfg_stream_rawlines = False
        self._cfg_stream_stdout = False
        self._cfg_stream_stdin = False

        self.finished = False

    def handle_client(self, client, addr, maxfails=None):
        self.handler = client_handler(self, client, version=self.version)
        common.handle_remote(client, self.handler, maxfails=maxfails or self.maxfails)

    def server_loop(
        self,
        *,
        start_port,
        port_range,
        callback=lambda this: this.finished,
        exit_func=lambda this: os._exit(1),
        scan_delay=0.1,
        reco_delay=1,
    ):

        # while not callback:
        #   - try binding portno starting with start_port
        #   - on fail, try next portno in port_range until exhausted
        #   - accept up to 1 client, update active_client global
        #   - pass control to handle_client
        #
        while not callback(self):

            # if we exhausted port_range, suicide pty
            if port_range < 0:
                verbose("\n\rUnable to bind any port in range :(")
                exit_func()

            # try binding
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                server.bind(("localhost", start_port))
                server.listen(1)
            except OSError:
                verbose(f"Unable to bind port {start_port}")
                start_port += 1
                verbose(f"-> binding {start_port}")
                port_range -= 1
                time.sleep(scan_delay)
                continue

            # accept client & give control handle_client for babysitting
            try:
                remote, addr = server.accept()
                self.active_client = remote
                self.handle_client(remote, addr)
            except (BrokenPipeError, ConnectionResetError) as e:
                verbose("\n\r -> client disconnected :/")
                verbose(f"    - reason: {type(e)} {e}")
                time.sleep(reco_delay)

            # if unhandled exception that would kill server, suicide pty too
            # except BaseException as e:
            #    verbose(f'\n\r Failure: {type(e)} {e}')
            #    exit_func()

            # if handle_client returned or raised, end connection / cleanup
            finally:
                self.active_client = None
                self.handler = None

                try:
                    remote.shutdown(socket.SHUT_RDWR)
                    remote.close()
                except BaseException:
                    pass
                time.sleep(reco_delay)

    #
    # threads & other parts
    #

    def send_to_client(self, what, data):
        """helper that sends data to client iff it exists (+hide exceptions)"""

        try:
            common.send_to_remote(self.active_client, what, data)

        except BrokenPipeError:

            # (try again to shutdown, just in case)
            if self.active_client is not None:
                try:
                    self.active_client.shutdown(socket.SHUT_RDWR)
                    self.active_client.close()
                except BaseException:
                    pass

            self.active_client = None
            self.handler = None
            time.sleep(1)

    def poll_termsize(self, wait_for_child=None):
        """(thread) poll terminal size and update child_fd TIOCSWINSZ

        Note: only thread for initial poll, after is called via SIGWINCH signal
        """

        # if wait_for_child, wait for child_fd to appear before executing
        if wait_for_child:
            while self.child_fd < 1:
                time.sleep(0.01)

        # if child_fd is here, read terminal size, then set its window size
        if self.child_fd > 0:
            new_size = tuple(shutil.get_terminal_size())

            if new_size != self.terminal_size:
                self.terminal_size = new_size
                self.send_to_client(what="terminal_size", data=new_size)

                nbcols, nbrows = new_size
                s = struct.pack("HHHH", nbrows, nbcols, 0, 0)
                fcntl.ioctl(self.child_fd, termios.TIOCSWINSZ, s)

                if self.pyte_screen is not None:
                    self.pyte_screen.resize(lines=nbrows, columns=nbcols)

        # sometime we race before pty has size, need to try again reading it
        if self.terminal_size is None:
            time.sleep(0.1)
            self.poll_termsize()
            return

        # when terminal size is first known, create pyte screen of the right size
        if self.pyte_screen is None and self.terminal_size is not None:
            self.pyte_screen = pyte.Screen(*self.terminal_size)

    def cursor_poller(self, poll=0.01):
        """(thread) watch for terminal cursor change, notify client if some"""

        last_ping = time.time()
        last_position = self.cursor_position
        while not self.finished:

            # poll waiting for child_fd to appear
            time.sleep(poll)
            if self.child_fd < 1:
                continue

            # if cursor cloud have moved, send sequence to stdin
            if self.cursor_moved:
                os.write(sys.stdin.fileno(), ansiseq.cursor)
                self.cursor_moved = False

            # if cursor has moved since last poll, send to client
            if self.cursor_position != last_position:
                last_position = self.cursor_position
                self.send_to_client(what="cursor_position", data=self.cursor_position)

                # (remove b/c it confuses pyte more than anything else)
                # if self.pyte_screen is not None:
                #    self.pyte_screen.cursor_position(*cursor_position)

            # also use this handler to send pings
            if abs(last_ping - time.time()) > 1:
                last_ping = time.time()
                self.send_to_client(what="ping", data=time.time())

    def screen_watcher(self, poll=0.1):
        """(pyte) feed updates to virtual pyte terminal, sends line updates to client"""

        while self.pyte_screen is None:
            time.sleep(poll)

        stream = pyte.ByteStream(self.pyte_screen)
        while not self.finished:
            if not self.pyte_buffer:
                time.sleep(poll)
                continue

            # every time there is something new in stdout, update pyte screen
            buffer, self.pyte_buffer = self.pyte_buffer, b""
            stream.feed(buffer)

            # if there are dirty pyte screen lines, send them to client
            if len(self.pyte_screen.dirty) > 0:
                display = self.pyte_screen.display
                dirty = list(self.pyte_screen.dirty)
                dirty.sort()  # (!! sorting for efficiency, smaller lines first !!)
                self.pyte_screen.dirty.clear()
                for lineno in dirty:
                    if lineno >= len(display):
                        continue

                    if self._cfg_stream_lines and self.handler:
                        self.handler.get_lines([lineno])
                    if self._cfg_stream_rawlines and self.handler:
                        self.handler.get_rawlines([lineno])

    #
    # fake_pty.spawn handlers
    #

    def master_read(self, stdout):
        """(pty) monitor child_fd output, send to client, print to screen"""

        if stdout is not None:
            out = os.read(stdout, common.global_buffer_size)
        else:
            out = b""

        if self._cfg_stream_stdout:
            self.send_to_client(what="stdout", data=out)

        # first second of stdout is buffered in early_buffer
        self.first_write = self.first_write or time.time()
        if abs(self.first_write - time.time()) < self.initial_latency:
            self.early_buffer += out
            return fake_pty.SKIP_STDOUT

        # if program has not smcup-ed, do it to get good aligment
        if not self.has_smcup:
            self.has_smcup = True
            if ansiseq.smcup not in self.early_buffer:
                def _rmcup_delayed():
                    print(ansiseq.decoded.smcup)
                    print(ansiseq.decoded.clear)
                    print(flush=True)
                    print(ansiseq.decoded.rmcup, end='', flush=True)
                atexit.register(_rmcup_delayed)
                print(ansiseq.decoded.smcup, end="")
                print(ansiseq.decoded.clear, end="")
                print(ansiseq.decoded.cup00, end="", flush=True)

        # if leftovers in early_buffer, prepend it to out
        if self.early_buffer:
            out = self.early_buffer + out
            self.early_buffer = b""

        if out:
            self.cursor_moved = True

        self.pyte_buffer += out

        if stdout is None:
            os.write(fake_pty.STDOUT_FILENO, out)
        return out

    def stdin_read(self, stdin):
        """(pty) monitor stdin, send to client, forward to child_fd input"""

        # read stdin, close it if EOF / empty
        indata = os.read(stdin, common.global_buffer_size)
        if not indata:
            self.send_to_client(what="process", data="stdin_eof")
            return indata

        # ask update on cursor position if its not already a stdin sequence
        if ansiseq.curpos_prefix not in indata:
            self.cursor_moved = True

        # find & remove cursor position sequence
        if ansiseq.curpos_prefix in indata:
            start = indata.index(ansiseq.curpos_prefix)
            end = start + len(ansiseq.curpos_prefix)
            while end < len(indata) and indata[end] in ansiseq.curpos_charset:
                end += 1

            # if one seq found, slice it, and update cursor_position
            if indata[end - 1] == ansiseq.curpos_suffix[0]:
                seq = indata[start:end]

                seq = seq.lstrip(ansiseq.curpos_prefix).rstrip(ansiseq.curpos_suffix)
                if b";" in seq:
                    lineno, colno = seq.split(b";")
                    lineno = int(lineno.decode())
                    colno = int(colno.decode())
                    self.cursor_position = (colno, lineno)

                indata = indata[:start] + indata[end:]

        # if stdin was only sequence, skip (without closing it)
        if not indata:
            return fake_pty.SKIP_STDIN

        # transmit data to client
        if self._cfg_stream_stdin:
            self.send_to_client(what="stdin", data=indata)

        # forward data to process
        return indata

    def setup_sigwinch(self):
        signal.signal(signal.SIGWINCH, lambda *x, **y: self.poll_termsize())

    def setup_jobs(self):
        self.jobs = []
        jobs = []

        # +start ephemeral thread for first terminal size update
        jobs.append(
            threading.Thread(
                target=lambda: self.poll_termsize(wait_for_child=True),
                daemon=True,
            )
        )

        # +start polling thread handling cursor position detection
        jobs.append(
            threading.Thread(
                target=lambda: self.cursor_poller(),
                daemon=True,
            )
        )

        # +start polling thread updating internal virtual pyte (& sending updates)
        jobs.append(
            threading.Thread(
                target=lambda: self.screen_watcher(),
                daemon=True,
            )
        )

        # +call master_read in 1.1s to empty early_buffer
        def _delayed():
            time.sleep(self.initial_latency * 1.10)
            self.master_read(None)

        jobs.append(
            threading.Thread(
                target=_delayed,
                daemon=True,
            )
        )

        # and finally, start thread handling networking / client connections
        jobs.append(
            threading.Thread(
                target=lambda: self.server_loop(
                    start_port=self.start_port, port_range=self.port_range
                ),
                daemon=True,
            )
        )

        self.jobs = jobs

    def spawn(self):
        exit_code = 1

        if len(self.jobs) == 0:
            raise RuntimeError("Call pty_driver.setup_jobs() before spawn()!")
        for job in self.jobs:
            job.start()

        try:
            parent = self  # to set parent.child_fd
            exit_code = fake_pty.spawn(
                parent,
                self.argv_cmd,
                master_read=lambda x: self.master_read(x),
                stdin_read=lambda x: self.stdin_read(x),
            )
        finally:
            self.send_to_client(what="exit", data=exit_code)

    def start(self):
        self.setup_sigwinch()
        self.setup_jobs()
        return self.spawn()


#
# main
#


def argv2cmd(
    argv, *, default_to_editor=True, alt_defaults=["vim", "nano", "bash", "sh"]
):

    default = None
    if len(argv) == 1:
        if default_to_editor:
            default = os.environ.get("EDITOR")
        while default is None:
            default = shutil.which(alt_defaults.pop(0))

        argv += [default]

    cmd, *args = argv[1:]
    if not (os.path.isabs(cmd) and os.path.isfile(cmd)):
        cmd = shutil.which(cmd) or cmd
    return [cmd] + args


def main():
    argv_cmd = argv2cmd(sys.argv)
    driver = pty_driver(argv_cmd)
    exit_code = driver.start()

    sys.exit(exit_code)
