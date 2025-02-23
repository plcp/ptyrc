import code
import os
import pty
import select
import shutil
import socket
import subprocess
import sys
import threading
import time
import tty

import ptyrc.common as common
from ptyrc.common import verbose
from ptyrc.termcap import ansiseq, charspec, linespec


class server_handler(common.basic_handler):

    def __init__(self, backend, remote, version=common.version):
        super().__init__(remote, version=version)

        # we just connected, ask server for terminal size & cursor position
        self.send(what="get_value", data="argv_cmd")
        self.send(what="get_value", data="terminal_size")
        self.send(what="get_value", data="cursor_position")
        self.send(what="command", data="enable_stream_lines")
        self.display = []
        self.raw_display = dict()

        self.backend = backend
        self.backend.active_handler = self

    def terminal_size(self, new_size):

        # first time we get terminal_size, ask server for all lines
        if self.values.get("terminal_size") is None:
            self.send("command", data="refresh_lines")

        super().terminal_size(new_size)

    def set_line(self, where, line):
        if where >= len(self.display):
            current = len(self.display)
            missing = list(range(current, where))
            if len(missing) > 0:
                self.send("get_lines", missing)

            self.display += ["" for _ in range(where - current + 1)]

        self.display[where] = line

        maxsz = max(self.values["terminal_size"][1], where + 1)
        self.display = self.display[:maxsz]

    def set_rawline(self, where, rawline):
        chars = []

        buffer = common.b64decode(rawline)
        for start in range(0, len(buffer), charspec.packed_size):
            packed = buffer[start : start + charspec.packed_size]
            chars.append(charspec.unpack(packed))

        self.raw_display[where] = linespec(chars)


class pilot_backend:

    def __init__(
        self,
        *,
        timeout=3,
        start_port=common.start_port,
        port_range=common.port_range,
        maxfails=10,
        version=common.version,
    ):

        self.finished = False

        self.timeout = timeout
        self.start_port = start_port
        self.port_range = port_range
        self.maxfails = maxfails
        self.version = common.version

        self.active_handler = None
        self.active_server = None

    def handle_server(self, server, portno, maxfails=10):
        self.active_handler = server_handler(self, server, version=self.version)

        try:
            common.handle_remote(server, self.active_handler, maxfails=maxfails)
        finally:
            self.active_handler.last_ping = 0
            self.active_handler.finished = True

    def find_server(self, start_port, port_range):
        verbose("searching for server...")

        while not self.finished:
            for portno in range(start_port, start_port + port_range):
                verbose(f" - trying {portno}")

                try:
                    remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    remote.settimeout(1)
                    remote.connect(("localhost", portno))
                    remote.settimeout(3)

                    self.active_server = remote
                    self.handle_server(remote, portno)
                except (ConnectionRefusedError, TimeoutError):
                    time.sleep(0.1)
                #            except BaseException as e:
                except (ConnectionResetError, BrokenPipeError) as e:
                    verbose("\n\r -> Connection closed :/")
                    verbose(f"    - reason: {type(e)} {e}")
                    time.sleep(1)
                    verbose("    ...reconnecting")
                finally:
                    self.active_server = None

                    try:
                        remote.shutdown(socket.SHUT_RDWR)
                        remote.close()
                    except BaseException:
                        pass
                    time.sleep(0.1)

    def setup_jobs(self):
        self.jobs = []
        jobs = []

        jobs.append(
            threading.Thread(
                target=lambda: self.find_server(
                    start_port=self.start_port, port_range=self.port_range
                ),
                daemon=True,
            )
        )

        self.jobs = jobs

    def start(
        self,
        *,
        callback=(lambda pilot: pilot.drop_shell()),
    ):
        pilot = pilot_frontend(backend=self, timeout=self.timeout)

        self.setup_jobs()
        for job in self.jobs:
            job.start()

        return callback(pilot)

    def quit(self, exit_func=lambda: os._exit(0)):
        self.finished = True
        exit_func()


class pilot_frontend:
    class key:
        ESC = "\x1b"
        ARROW_UP = "\x1bOA"
        ARROW_DOWN = "\x1bOB"
        ARROW_RIGHT = "\x1bOC"
        ARROW_LEFT = "\x1bOD"
        PAGE_UP = "\x1b[5~"
        PAGE_DOWN = "\x1b[6~"
        BACKSPACE = "\x08"
        ENTER = "\r"
        RETURN = "\r"
        CTRL_C = "\x03"
        CTRL_D = "\x03"
        CTRL_X = "\x18"

    def __init__(self, backend, timeout=3):
        self.backend = backend
        self.timeout = timeout

        self.finished = False

    @property
    def connected(self):
        if self.backend.active_handler is None:
            return False
        return self.backend.active_handler.is_alive()

    @property
    def handler(self):

        latency = 0
        while (
            self.backend.active_handler is None
            or not self.backend.active_handler.is_alive()
        ):
            time.sleep(0.1)
            latency += 0.1
            if latency > self.timeout:
                raise TimeoutError("no remote to be found")
        return self.backend.active_handler

    @property
    def argv(self):
        return self.handler.values.get("argv_cmd")

    @property
    def cursor(self):
        return self.handler.values.get("cursor_position")

    @property
    def cursor_row(self):
        pos = self.handler.values.get("cursor_position")
        if pos is None:
            return None
        return pos[1]

    @property
    def cursor_column(self):
        pos = self.handler.values.get("cursor_position")
        if pos is None:
            return None
        return pos[0]

    @property
    def size(self):
        return self.handler.values.get("terminal_size")

    @property
    def size_rows_count(self):
        sz = self.handler.values.get("terminal_size")
        if sz is None:
            return None
        return sz[1]

    @property
    def size_columns_count(self):
        sz = self.handler.values.get("terminal_size")
        if sz is None:
            return None
        return sz[0]

    def wait_for_driver(self, animated=True):
        while not self.connected:
            if animated:
                cnt = int(time.time() * 10) % 4
                print(
                    " [{}] connecting...".format("|/—\\"[cnt]),
                    end="\r",
                    file=sys.stderr,
                )
            time.sleep(0.1)

        if animated:
            print("                   ", end="\r", file=sys.stderr)

    def drop_shell(
        self, extra_locals=None, banner=None, exitmsg=None, confirm_exit=True
    ):
        if confirm_exit:
            exitmsg = (
                exitmsg or "\n\rUse pilot.quit() to quit or press again ^D quickly!\n\r"
            )

        if banner is None and self.handler.values.get("argv_cmd") is None:
            time.sleep(0.5)

        argv = self.handler.values.get("argv_cmd")
        if banner is None and argv is not None:
            banner = f'Connected to "{" ".join(argv)}"'
        if banner is None:
            banner = "Connected!"

        last_exit = 0
        while not self.finished:
            try:
                local = dict(pilot=self)
                local.update(extra_locals or dict())
                code.interact(banner=banner, exitmsg=exitmsg, local=local)
            except (KeyboardInterrupt, SystemExit):
                pass

            # if blocked, will exit at some point
            if abs(last_exit - time.time()) < 2 or not confirm_exit:
                self.quit()

            last_exit = time.time()

    def drop_task(self, task, freq=1, try_restart=True, **task_kwargs):

        def _pilot_task(self, task, freq, task_kwargs):
            while not self.finished:
                task(self, **task_kwargs)
                time.sleep(1 / freq)

        def _pilot_restart_task(self, task, freq, task_kwargs):
            while not self.finished:
                try:
                    task(self, **task_kwargs)
                except (TimeoutError, BrokenPipeError, ConnectionResetError):
                    pass
                time.sleep(1 / freq)

        which_job = _pilot_restart_task if try_restart else _pilot_task
        new_task = threading.Thread(
            target=which_job,
            kwargs=dict(self=self, task=task, freq=freq, task_kwargs=task_kwargs),
            daemon=True,
        )

        self.backend.jobs.append(new_task)
        new_task.start()
        return new_task

    def quit(self, exit_func=lambda: os._exit(0)):
        self.finished = True
        if self.backend.active_handler is not None:
            try:
                self.backend.active_handler.close("closed by user")
            except BrokenPipeError:
                pass

        self.backend.finished = True
        self.backend.quit(exit_func=exit_func)

    def text_at(self, row_number, rstrip=" ", first_row_is_one=True):
        if first_row_is_one:
            row_number -= 1

        if row_number > len(self.handler.display):
            return None

        row = self.handler.display[row_number]
        if rstrip:
            row = row.rstrip(rstrip)
        return row

    def show(self, *, colors=False, cursor=False, cropped=True, **kwargs):
        kwargs["display_only"] = kwargs.get("display_only", True)
        kwargs["show_colors"] = kwargs.get("show_colors", colors)
        kwargs["show_cursor"] = kwargs.get("show_cursor", cursor)
        kwargs["cropped"] = kwargs.get("cropped", cropped)
        self.interact(**kwargs)

    def interact(
        self,
        *,
        verbose=False,
        cropped=True,
        margin=2,
        framerate=10,
        display_only=False,
        exit_hint=True,
        show_cursor=True,
        show_colors=True,
        argv=True,
        size=True,
        cursor=True,
        top_line=True,
        bottom_line=True,
    ):
        last_size = None
        stdin_mode = None

        quiet = not verbose
        hook_stdin = not display_only

        ansiseq.initialize()

        def _echo(*args, **kwargs):
            kwargs["end"] = kwargs.get("end", "\r\n")
            print(*args, **kwargs)

        if quiet:
            argv = False
            size = False
            cursor = False
            top_line = False
            bottom_line = False

        if len(self.handler.display) == 0:
            self.handler.send("get_value", data="terminal_size")
            self.handler.send("command", data="refresh_lines")
            self.handler.send("command", data="enable_stream_lines")
            time.sleep(1)
        if len(self.handler.display) == 0:
            raise TimeoutError("remote send nothing to display :(")

        if show_colors:
            self.handler.send("command", data="refresh_rawlines")
            self.handler.send("command", data="enable_stream_rawlines")

        try:
            _echo(ansiseq.decoded.smcup, end="")

            if hook_stdin:
                try:
                    stdin_mode = tty.tcgetattr(pty.STDIN_FILENO)
                    tty.setraw(pty.STDIN_FILENO)
                except tty.error:
                    stdin_mode = None

            while not self.handler.finished:

                if hook_stdin:
                    r, _, _ = select.select([pty.STDIN_FILENO], [], [], 0)
                    if pty.STDIN_FILENO in r:
                        inbuf = os.read(pty.STDIN_FILENO, 1024)
                        if self.key.CTRL_X.encode() in inbuf:
                            return

                        if self.key.CTRL_C.encode() in inbuf and exit_hint:
                            exit_hint = False
                            _echo(ansiseq.decoded.clear, end="")
                            _echo(ansiseq.decoded.cup00, end="", flush=True)
                            _echo("                                                ")
                            _echo("Press ^X to exit pilot.show(hook_stdin=True)    ")
                            _echo("                                                ")
                            time.sleep(1)
                            _echo(ansiseq.decoded.cup00, end="", flush=True)
                            _echo("                                                ")
                            _echo("                                                ")
                            _echo("                                                ")
                            _echo(ansiseq.decoded.clear, end="")

                        self.input(data=inbuf, interactive=False, raw=True)

                new_size = shutil.get_terminal_size()
                if new_size != last_size:
                    _echo(ansiseq.decoded.clear, end="")
                    last_size = new_size
                nbcols, nbrows = last_size

                colno, lineno = self.cursor
                maxlen = 9999 if not cropped else nbcols - 6

                disp = list(self.handler.display)
                colored = 0

                for _ in range(10):
                    if not show_colors:
                        break

                    for i, d in enumerate(disp):
                        raw = self.handler.raw_display.get(i)
                        if raw is not None and disp[i] == raw.literal:
                            colored += 1
                            if show_cursor and i == lineno - 1:
                                disp[i] = raw.render(maxlen=maxlen, cursor_at=colno)
                            else:
                                disp[i] = raw.render(maxlen=maxlen)

                    if colored > max(nbrows - 6, 0):
                        break
                    time.sleep(0.05)

                curcnt = int(time.time() * framerate) % 4
                if show_cursor and curcnt > 1 and not show_colors:
                    if lineno > 0 and (lineno - 1) < len(disp):
                        line = disp[lineno - 1]
                        if colno > 0 and (colno - 1) < len(line):
                            line = line[: colno - 1] + "_" + line[colno:]
                            disp[lineno - 1] = line

                rqrows = len(disp) + margin
                rqrows += [argv, size, cursor].count(True)
                rqrows += 2 * [top_line, bottom_line].count(True)

                if cropped and rqrows > nbrows:
                    extra = rqrows - nbrows + 1
                    half = len(disp) // 2 - (extra + 1) // 2
                    midmsg = f"(... {len(disp) - half * 2 - extra % 2} truncated ...)"
                    disp = disp[: half + extra % 2] + [midmsg] + disp[-half:]

                if cropped and not show_colors:
                    new_disp = []
                    for d in disp:
                        if len(d) >= nbcols - margin:
                            new_disp += [d[: nbcols - 6 - margin] + " ... >"]
                        else:
                            new_disp += [d]
                    disp = new_disp

                if len(disp) < 1:
                    return

                time.sleep(1 / framerate)
                _echo(ansiseq.decoded.cup00, end="", flush=True)
                if argv:
                    _echo(f"argv: {self.argv}    ")
                if size:
                    _echo(f"size: {self.size}     ")
                if cursor:
                    _echo(f"cursor: {self.cursor}     ")
                if top_line:
                    _echo(disp[0])
                    _echo("-----")

                _echo("\n\r".join(disp))

                if bottom_line:
                    _echo("-----")
                    _echo(disp[-1])

        finally:
            _echo(ansiseq.decoded.rmcup, end="", flush=True)

            if stdin_mode is not None:
                tty.tcsetattr(pty.STDIN_FILENO, tty.TCSAFLUSH, stdin_mode)

    def intercept(self, callback=None, decode=False, verbose_hex=False):
        original_method = self.handler.stdin
        is_finished = False

        self.handler.send("command", data="enable_stream_stdin")
        try:

            def stdin_interceptor(data):
                nonlocal callback
                nonlocal decode
                nonlocal verbose_hex
                nonlocal is_finished

                if verbose_hex:
                    print(dict(stdin=data.hex()), end="\n\r", file=sys.stderr)
                if decode:
                    data = data.decode()
                elif callback is None and isinstance(data, bytes):
                    data = data.__repr__()[2:-1]

                if callback is None and not verbose_hex:
                    print(data, end="", flush=True)
                    return

                retval = callback(data)
                if not retval:
                    is_finished = True

            self.handler.stdin = stdin_interceptor
            while not is_finished:
                time.sleep(0.1)
        finally:
            self.handler.stdin = original_method

    def input(self, interactive=True, *, data=None, raw=False):
        data = data or interactive
        if isinstance(data, bytes) and not raw:
            data = data.decode()
        if not isinstance(data, (bytes, str)) and interactive:
            if not interactive:
                raise RuntimeError(
                    "If input data is None (or not str) interactive must be True"
                )

            data = input()

        if not raw:
            data = data.encode()

        self.handler.send(what="write_to_tty", data=data)

    # TODO: overlay should be handled on driver side
    def draw(
        self,
        y_rows,
        x_cols,
        char,
        *,
        overlay=True,
        first_rowcol_is_one=True,
        **charspec_attrs,
    ):

        if not first_rowcol_is_one:
            x_cols += 1
            y_rows += 1

        assert isinstance(char, str)
        assert len(char.encode()) <= charspec.datamaxsz  # <= 8

        if not overlay and y_rows >= 1 and x_cols >= 1:
            newchar = ""
            for xoffset, newc in enumerate(char):
                if y_rows - 1 >= len(self.handler.display):
                    break

                row = self.handler.display[y_rows - 1]
                if x_cols - 1 + xoffset >= len(row):
                    break

                oldc = row[x_cols + xoffset - 1]
                if oldc == " ":
                    newchar += newc
                else:
                    break
            char = newchar

        req = dict(
            where=[y_rows, x_cols],
            char=char,
            attrs=charspec_attrs if charspec_attrs else None,
        )

        self.handler.send(what="draw", data=req)

    # TODO: overlay should be handled on driver side
    def draw2d(self, y_rows, x_cols, char_matrix, **draw_kwargs):

        for yoffset, char in enumerate(char_matrix):
            self.draw(y_rows + yoffset, x_cols, char=char, **draw_kwargs)

    # TODO: animations should be handled on driver side
    # TODO: cleanup should be handled on driver side
    def draw_anim(
        self,
        y_rows,
        x_cols,
        char_sequence,
        *,
        stepsize=0.1,
        clear_after=True,
        **draw_kwargs,
    ):

        maxsz = 0
        for char in char_sequence:
            maxsz = max(len(char), maxsz)
            self.draw(y_rows, x_cols, char=char, **draw_kwargs)
            time.sleep(stepsize)

        if clear_after:
            self.draw(y_rows, x_cols, char=" " * maxsz, **draw_kwargs)

    # TODO: animations should be handled on driver side
    # TODO: cleanup should be handled on driver side
    def draw2d_anim(
        self,
        y_rows,
        x_cols,
        matrix_sequence,
        *,
        stepsize=0.1,
        clear_after=True,
        **draw_kwargs,
    ):

        y_maxsz = 0
        x_maxsz = 0
        for matrix in matrix_sequence:
            y_maxsz = max(len(matrix), y_maxsz)
            x_maxsz = max(max(len(c) for c in matrix), x_maxsz)

            self.draw2d(y_rows, x_cols, char_matrix=matrix, **draw_kwargs)
            time.sleep(stepsize)

        if clear_after:
            cleanup = [" " * x_maxsz for _ in range(y_maxsz)]
            self.draw2d(y_rows, x_cols, char_matrix=cleanup, **draw_kwargs)


def main():
    backend = pilot_backend()

    if len(sys.argv) < 2:

        def interactive_shell(pilot):
            pilot.wait_for_driver()
            pilot.drop_shell()

        backend.start(callback=interactive_shell)
        sys.exit(0)

    if len(sys.argv) == 2:
        import importlib.util

        spec = importlib.util.spec_from_file_location("userscript", sys.argv[1])

        userscript = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = userscript
        spec.loader.exec_module(userscript)

        backend.start(callback=lambda pilot: userscript.main(pilot))
        sys.exit(0)

    if len(sys.argv) > 2:
        print(f"Usage: {sys.argv[0]} [userscript.py]", file=sys.stderr)
        sys.exit(1)
