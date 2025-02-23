import pyte

from ptyrc.termcap import charspec


class screen:

    def __init__(self, terminal_size):
        self.main_screen = pyte.Screen(*terminal_size)
        self.main_stream = pyte.ByteStream(self.main_screen)

        self.buffer = b""
        self.size = terminal_size  # (nbcols, nbrows)

    def feed(self, input_data):
        self.buffer += input_data

    def flush(self, callback=None, *, clear=False):
        if not self.is_dirty:
            return False

        buffer, self.buffer = self.buffer, b""
        self.main_stream.feed(buffer)

        if not callback:
            return self.is_dirty

        dirty = self.get_dirty_lines()
        display = self.main_screen.display

        # is this unnecessary?
        dirty = [lno for lno in dirty if lno < len(display)]

        if clear:
            self.clear_dirty_lines()

        if callback:
            callback(self, dirty, display)
        return self.is_dirty

    def resize(self, nbcols=None, nbrows=None, **kwargs):
        if nbrows or nbcols:
            assert len(kwargs) == 0
        if kwargs:
            nbrows = kwargs.get("lines", nbrows)
            nbcols = kwargs.get("columns", nbcols)
        assert nbrows or nbcols

        self.main_screen.resize(lines=nbrows, columns=nbcols)
        return (self.nbcols, self.nbrows)

    @property
    def display(self):
        return self.main_screen.display

    @property
    def nbcols(self):
        return self.main_screen.columns

    @property
    def nbrows(self):
        return self.main_screen.lines

    @property
    def is_dirty(self):
        return self.buffer or len(self.main_screen.dirty) > 0

    def get_dirty_lines(self):
        dirty = list(self.main_screen.dirty)
        dirty.sort()
        return dirty

    def clear_dirty_lines(self):
        self.main_screen.dirty.clear()
        return not self.is_dirty

    def get_raw_buffer(self):
        return self.main_screen.buffer

    def get_raw_lines(self, linelist):
        buffer = self.get_raw_buffer()
        nbcols = self.nbcols

        linelist.sort()
        linelist = [lno for lno in linelist if lno < len(buffer)]

        raw_lines = dict()
        for lineno in linelist:
            line_buffer = [buffer[lineno][x] for x in range(nbcols)]

            current_line = []
            for pyte_char in line_buffer:
                current_line.append(charspec.from_pyte_char(pyte_char))
            raw_lines[lineno] = current_line

        return raw_lines
