import subprocess


class ansiseq:
    smcup = None
    rmcup = None
    clear = None
    cup00 = None
    reset = None
    cursor = None
    curpos = None
    bold = None
    sgr0 = None
    ready = False

    class decoded:
        pass

    @classmethod
    def initialize(cls):
        if cls.ready:
            return

        cls.smcup = subprocess.check_output(["tput", "smcup"])
        cls.rmcup = subprocess.check_output(["tput", "rmcup"])
        cls.sc = subprocess.check_output(["tput", "sc"])  # save cursor
        cls.rc = subprocess.check_output(["tput", "rc"])  # restore cursor
        cls.clear = subprocess.check_output(["tput", "clear"])
        cls.el = subprocess.check_output(
            ["tput", "el"]
        )  # erase line from cur to end of line
        cls.el1 = subprocess.check_output(
            ["tput", "el1"]
        )  # erase line from start to cur
        cls.ed = subprocess.check_output(
            ["tput", "ed"]
        )  # erase line from cur to end of screen
        cls.reset = subprocess.check_output(["tput", "reset"])
        cls.bold = subprocess.check_output(["tput", "bold"])  # bold
        cls.dim = subprocess.check_output(["tput", "dim"])  # dim
        cls.sitm = subprocess.check_output(["tput", "sitm"])  # italics
        cls.smul = subprocess.check_output(
            ["tput", "smul"]
        )  # underline or "underscore" (sic)
        cls.blink = subprocess.check_output(["tput", "blink"])  # blink
        cls.rev = subprocess.check_output(["tput", "rev"])  # reverse
        cls.smso = subprocess.check_output(["tput", "smso"])  # standout (reverse+bold)
        cls.invis = subprocess.check_output(["tput", "invis"])  # invisible
        cls.strike = cls.smul.replace(b"4m", b"9m")
        cls.sgr0 = subprocess.check_output(["tput", "sgr0"])  # reset all attributes

        cls.cursor = subprocess.check_output(["tput", "u7"])
        cls.curposXX = subprocess.check_output(["tput", "u6", "11110", "22221"])
        cls.curpos_suffix = cls.curposXX[-1:]
        cls.curpos_prefix = cls.curposXX[: -len(b"22222;11111" + cls.curpos_suffix)]
        cls.curpos_charset = b"0123456789;R"
        cls.curpos_seqfor = lambda x, y: (
            cls.curposXX.replace(b"11111", str(x).encode()).replace(
                b"22222", str(x).encode()
            )
        )

        cls.cup00 = subprocess.check_output(["tput", "cup", "0", "0"])
        cls.cupXX = subprocess.check_output(["tput", "cup", "123456788", "987654320"])
        cls.home = subprocess.check_output(["tput", "home"])  # eq cup00
        cls.cup = lambda x, y: (
            cls.cupXX.replace(b"123456789", str(x).encode()).replace(
                b"987654321", str(y).encode()
            )
        )

        cls.setaf0 = subprocess.check_output(["tput", "setaf", "0"])
        cls.setab0 = subprocess.check_output(["tput", "setab", "0"])
        cls.setaf = lambda c: cls.setaf0.replace(b"30", str(c).encode())
        cls.setab = lambda c: cls.setab0.replace(b"40", str(c).encode())

        cls.setaf_256 = lambda r, g, b: (
            cls.setaf0.replace(b"30m", f"38;2;{r};{g};{b}m".encode())
        )
        cls.setab_256 = lambda r, g, b: (
            cls.setab0.replace(b"40m", f"48;2;{r};{g};{b}m".encode())
        )

        for k, v in cls.__dict__.items():
            if isinstance(v, bytes):
                setattr(cls.decoded, k, v.decode())

        cls.decoded.cup = lambda x, y: cls.cup(x, y).decode()
        cls.decoded.setaf = lambda x: cls.setaf(x).decode()
        cls.decoded.setab = lambda x: cls.setab(x).decode()
        cls.decoded.setaf_256 = lambda r, g, b: cls.setaf_256(r, g, b).decode()
        cls.decoded.setab_256 = lambda r, g, b: cls.setab_256(r, g, b).decode()

        cls.ready = True


_cache_pyte2raw = dict()
_cache_raw2packed = dict()
_cache_packed2raw = dict()


class charspec:
    datamaxsz = 8
    packed_size = 8 + datamaxsz

    @staticmethod
    def from_pyte_char(pyte_char):
        raw = _cache_pyte2raw.get(pyte_char)
        if raw is not None:
            return raw

        raw = charspec(**pyte_char._asdict())
        _cache_pyte2raw[pyte_char] = raw
        return raw

    def __init__(
        self,
        data=" ",
        fg="default",
        bg="default",
        bold=False,
        italics=False,
        underscore=False,
        strikethrough=False,
        reverse=False,
        blink=False,
    ):

        if isinstance(data, str):
            data = data.encode()
        data = bytes(data)
        self.datasz = len(data)
        self.data = data

        # handle foreground color
        if isinstance(fg, tuple):
            fg = bytes(fg)
        if isinstance(fg, bytes):
            assert len(fg) == 3  # fg color may be (1, 2, 3) or b'\x01\x02\x03'
            fg = fg.hex().lower()

        self.fg_name = fg
        self.fg_code, self.fg_is256 = self.color_to_code(fg, foreground=True)
        self.fg_seq = self.colcode_to_seq(self.fg_code, self.fg_is256, foreground=True)

        # handle background color
        if isinstance(bg, tuple):
            bg = bytes(bg)
        if isinstance(bg, bytes):
            assert len(bg) == 3  # bg color may be (1, 2, 3) or b'\x01\x02\x03'
            bg = bg.hex().lower()

        self.bg_name = bg
        self.bg_code, self.bg_is256 = self.color_to_code(bg, background=True)
        self.bg_seq = self.colcode_to_seq(self.bg_code, self.bg_is256, background=True)

        self.flags = dict(
            bold=bold,
            italics=italics,
            underscore=underscore,
            strikethrough=strikethrough,
            reverse=reverse,
            blink=blink,
        )
        self.flags_seq = self.flags_to_seq(**self.flags)

        self.seq = (
            (self.fg_seq or b"")
            + (self.bg_seq or b"")
            + b"".join(v for v in self.flags_seq.values())
        )

    def __hash__(self):
        return hash((self.data, self.seq))

    def pack(self):
        if self in _cache_raw2packed:
            return _cache_raw2packed[self]

        bitflags = 0
        bitflags |= 0b00000001 if self.flags["bold"] else 0
        bitflags |= 0b00000010 if self.flags["italics"] else 0
        bitflags |= 0b00000100 if self.flags["underscore"] else 0
        bitflags |= 0b00001000 if self.flags["strikethrough"] else 0
        bitflags |= 0b00010000 if self.flags["reverse"] else 0
        bitflags |= 0b00100000 if self.flags["blink"] else 0
        bitflags |= 0b01000000 if self.fg_is256 else 0
        bitflags |= 0b10000000 if self.bg_is256 else 0

        fgcol = self.fg_code if self.fg_is256 else (self.fg_code, 0, 0)
        bgcol = self.bg_code if self.bg_is256 else (self.bg_code, 0, 0)

        assert self.datasz <= self.datamaxsz
        datapack = bytes(
            self.data[i] if i < self.datasz else 0 for i in range(self.datamaxsz)
        )

        retvalue = (
            bytes([bitflags] + list(fgcol) + list(bgcol) + [self.datasz]) + datapack
        )

        _cache_raw2packed[self] = retvalue
        return retvalue

    @classmethod
    def unpack(cls, packed_bytes):
        assert len(packed_bytes) == cls.packed_size

        if packed_bytes in _cache_packed2raw:
            return _cache_packed2raw[packed_bytes]

        bitflags = packed_bytes[0]
        bold = bool(bitflags & 0b00000001)
        italics = bool(bitflags & 0b00000010)
        underscore = bool(bitflags & 0b00000100)
        strikethrough = bool(bitflags & 0b00001000)
        reverse = bool(bitflags & 0b00010000)
        blink = bool(bitflags & 0b00100000)
        fg_is256 = bool(bitflags & 0b01000000)
        bg_is256 = bool(bitflags & 0b10000000)

        fg = ""
        bg = ""
        if fg_is256:
            fg = packed_bytes[1:4].hex().lower()
        if bg_is256:
            bg = packed_bytes[4:7].hex().lower()

        if not fg_is256:
            fg_code = packed_bytes[1]
            if fg_code // 90 > 0:
                fg = "bright"
            if fg_code % 10 == 0:
                fg += "black"
            if fg_code % 10 == 1:
                fg += "red"
            if fg_code % 10 == 2:
                fg += "green"
            if fg_code % 10 == 3:
                fg += "brown"
            if fg_code % 10 == 4:
                fg += "blue"
            if fg_code % 10 == 5:
                fg += "magenta"
            if fg_code % 10 == 6:
                fg += "cyan"
            if fg_code % 10 == 7:
                fg += "white"
            assert fg_code % 10 != 8
            if fg_code % 10 == 9:
                fg += "default"
            assert fg != "brightdefault"

        if not bg_is256:
            bg_code = packed_bytes[4]
            if bg_code // 100 > 0:
                bg = "bright"
            if bg_code % 10 == 0:
                bg += "black"
            if bg_code % 10 == 1:
                bg += "red"
            if bg_code % 10 == 2:
                bg += "green"
            if bg_code % 10 == 3:
                bg += "brown"
            if bg_code % 10 == 4:
                bg += "blue"
            if bg_code % 10 == 5:
                bg += "magenta"
            if bg_code % 10 == 6:
                bg += "cyan"
            if bg_code % 10 == 7:
                bg += "white"
            assert bg_code % 10 != 8
            if bg_code % 10 == 9:
                bg += "default"
            assert bg != "brightdefault"

        datasz = packed_bytes[7]
        datapacked = packed_bytes[8:]

        assert datasz <= cls.datamaxsz
        data = datapacked[:datasz].decode()
        retvalue = cls(
            data=data,
            fg=fg,
            bg=bg,
            bold=bold,
            italics=italics,
            underscore=underscore,
            strikethrough=strikethrough,
            reverse=reverse,
            blink=blink,
        )

        _cache_packed2raw[packed_bytes] = retvalue
        return retvalue

    def flags_to_seq(self, bold, italics, underscore, strikethrough, reverse, blink):
        seqs = dict()
        if bold:
            seqs["bold"] = ansiseq.bold
        if italics:
            seqs["italics"] = ansiseq.sitm
        if underscore:
            seqs["underscore"] = ansiseq.smul
        if strikethrough:
            seqs["strikethrough"] = ansiseq.strike
        if reverse:
            seqs["reverse"] = ansiseq.rev
        if blink:
            seqs["blink"] = ansiseq.blink
        return seqs

    def color_to_code(self, colorname, *, foreground=False, background=False):
        assert (foreground or background) and not (foreground and background)

        base = None
        if foreground:
            base = 30
        if background:
            base = 40

        asval = base
        if colorname.startswith("bright"):
            asval += 60

        if colorname.endswith("black"):
            return (asval, False)
        elif colorname.endswith("red") and colorname in ["brightred", "red"]:
            asval += 1
        elif colorname.endswith("green"):
            asval += 2
        elif colorname.endswith("brown") or colorname.endswith("yellow"):
            asval += 3
        elif colorname.endswith("blue"):
            asval += 4
        elif colorname.endswith("magenta"):
            asval += 5
        elif colorname.endswith("cyan"):
            asval += 6
        elif colorname.endswith("white"):
            asval += 7
        # elif colorname.endswith('mode256'): # (enable 256 colors mode)
        #   asval += 8
        elif colorname.endswith("default"):  # (special case)
            asval += 9

        if asval == base:
            asval = tuple(bytes.fromhex(colorname))
            assert len(asval) == 3  # assuming colorname xxxxxx 256-color code
            return (asval, True)
        return (asval, False)

    def colcode_to_seq(self, colorcode, is_256, *, foreground=False, background=False):
        assert (foreground or background) and not (foreground and background)
        if not ansiseq.ready:
            ansiseq.initialize()

        if not is_256:
            assert isinstance(colorcode, int)  # if not is_256 then color is int
        if is_256:
            assert isinstance(
                colorcode, tuple
            )  # if is_256 then color is (r, g, b) tuple

        if isinstance(colorcode, tuple):
            assert len(colorcode) == 3
            if foreground:
                return ansiseq.setaf_256(*colorcode)
            else:
                return ansiseq.setab_256(*colorcode)

        if foreground:
            return ansiseq.setaf(colorcode)
        else:
            return ansiseq.setab(colorcode)

    def render(self, previous_char=None, decode=True):

        data = b""
        if previous_char is not None and previous_char.seq != self.seq:
            if not ansiseq.ready:
                ansiseq.initialize()
            data += ansiseq.sgr0 + self.seq
        if previous_char is None:
            data += self.seq

        data += self.data
        return data.decode() if decode else data

    def __repr__(self):
        char = self.data
        attr = []
        if self.fg_name != "default":
            attr += [f"fg={self.fg_name}"]
        if self.bg_name != "default":
            attr += [f"bg={self.bg_name}"]
        for k, v in self.flags.items():
            if v:
                attr += [k]
        if attr:
            attr = "," + ",".join(attr)
        else:
            attr = ""

        return f"charspec({self.data}{attr})"


class linespec:
    def __init__(self, charlist):
        self.charlist = charlist
        self.literal = (b"".join([c.data for c in charlist])).decode()

    def __getitem__(self, idx):
        return self.charlist[idx]

    def render(
        self,
        *,
        decode=True,
        start_clean=False,
        end_clean=False,
        maxlen=None,
        cursor_at=None,
    ):
        line = "" if decode else b""

        if not ansiseq.ready:
            ansiseq.initialize()

        if decode and start_clean:
            line += ansiseq.decoded.sgr0
        elif start_clean:
            line += ansiseq.sgr0

        last_char = None
        for i, next_char in enumerate(self.charlist):
            if maxlen is not None and i >= maxlen:
                continue

            if cursor_at is not None and i == cursor_at - 1:
                flags = dict(next_char.flags)
                flags["reverse"] = not flags.get("reverse", False)
                next_char = charspec(
                    data=next_char.data,
                    fg=next_char.fg_name,
                    bg=next_char.bg_name,
                    **flags,
                )

            char_render = next_char.render(previous_char=last_char, decode=decode)
            line += char_render
            last_char = next_char

        if decode and end_clean:
            line += ansiseq.decoded.sgr0
        elif end_clean:
            line += ansiseq.sgr0

        return line

    def __repr__(self):
        line = ""
        last_spec = None
        for cspec in self.charlist:
            cstr = cspec.__repr__()
            cstr = cstr[len("charspec(b'") :][: -len(")")]
            if len(cstr) == 2:
                if last_spec is not None:
                    line += "<default>"
                    last_spec = None
                line += cstr[0]
            elif cstr[1] == "'" and cstr[2] == ",":
                new_spec = cstr[3:]
                if new_spec != last_spec:
                    line += f"<{new_spec}>"
                    last_spec = new_spec
                line += cstr[0]
            else:
                line += f"<{cspec.__repr__()}>"
        return f"linespec('{line}')"
