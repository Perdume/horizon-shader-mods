"""The C preprocessor subset OptiFine/Iris shader packs use: #define (object and function-like), #undef,
#if/#ifdef/#ifndef/#elif/#else/#endif, #include, and the markers the loader reads out of comments
(DRAWBUFFERS / RENDERTARGETS). Written from the public shader pack format documentation.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Callable

# What a pack can test with #if. A recent game on a modern GPU, no vendor or renderer macros.
PREDEFINED = {
    "MC_VERSION": "12100",
    "MC_GL_VERSION": "450",
    "MC_GLSL_VERSION": "450",
    "MC_OS_WINDOWS": "1",
    "MC_RENDER_QUALITY": "1.0",
    "MC_SHADOW_QUALITY": "1.0",
    "MC_HAND_DEPTH": "0.125",
}


class PreprocessError(Exception):
    pass


_IDENT = re.compile(r"[A-Za-z_]\w*")
_NUMBER = re.compile(
    r"0[xX][0-9a-fA-F]+[uU]?|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?[fFuUlL]?"
)
_DIRECTIVE = re.compile(r"^\s*#\s*(\w+)\s*(.*)$")
_MARKER = re.compile(r"/\*\s*(DRAWBUFFERS|RENDERTARGETS)\s*:\s*([0-9A-Za-z, ]+?)\s*\*/")


@dataclass
class Result:
    text: str = ""
    includes: list[str] = field(default_factory=list)
    # Buffers named by /* DRAWBUFFERS:01 */ or /* RENDERTARGETS: 0,1 */, as numbers.
    draw_buffers: list[int] = field(default_factory=list)
    version: int | None = None


def _splice(src: str) -> str:
    return src.replace("\r\n", "\n").replace("\r", "\n").replace("\\\n", "")


def _strip_comments(src: str) -> str:
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        if src.startswith("//", i):
            while i < n and src[i] != "\n":
                i += 1
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            end = n if j < 0 else j + 2
            out.append(" " + "\n" * src.count("\n", i, end))
            i = end
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


def _markers(raw: str) -> list[int]:
    found: list[int] = []
    for kind, value in _MARKER.findall(raw):
        if kind == "DRAWBUFFERS":
            found += [int(c) for c in value if c.isdigit()]
        else:
            found += [int(c) for c in re.findall(r"\d+", value)]
    return found


class Macros:
    def __init__(self, predefined: dict[str, str] | None = None):
        self.obj: dict[str, str] = dict(PREDEFINED if predefined is None else predefined)
        self.fun: dict[str, tuple[list[str], str]] = {}

    def defined(self, name: str) -> bool:
        return name in self.obj or name in self.fun

    def define(self, rest: str) -> None:
        m = re.match(r"([A-Za-z_]\w*)(\([^)]*\))?(.*)$", rest.strip("\n"), re.S)
        if not m:
            raise PreprocessError(f"bad #define: {rest!r}")
        name, params, body = m.group(1), m.group(2), m.group(3).strip()
        if params is not None:
            names = [p.strip() for p in params[1:-1].split(",") if p.strip()]
            self.fun[name] = (names, body)
            self.obj.pop(name, None)
        else:
            self.obj[name] = body
            self.fun.pop(name, None)

    def undef(self, name: str) -> None:
        self.obj.pop(name, None)
        self.fun.pop(name, None)

    def expand(self, text: str, active: frozenset[str] = frozenset(), depth: int = 0) -> str:
        if depth > 40:
            raise PreprocessError("macro expansion too deep")
        out: list[str] = []
        i, n = 0, len(text)
        while i < n:
            num = _NUMBER.match(text, i)
            if num and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")):
                out.append(num.group(0))
                i = num.end()
                continue
            m = _IDENT.match(text, i)
            if not m:
                out.append(text[i])
                i += 1
                continue
            name = m.group(0)
            i = m.end()
            if name in active:
                out.append(name)
            elif name in self.obj:
                out.append(self.expand(self.obj[name], active | {name}, depth + 1))
            elif name in self.fun:
                j = i
                while j < n and text[j] in " \t":
                    j += 1
                if j >= n or text[j] != "(":
                    out.append(name)
                    continue
                args, end = self._args(text, j)
                params, body = self.fun[name]
                if len(args) != len(params) and not (not params and args == [""]):
                    raise PreprocessError(f"macro {name} takes {len(params)} arguments, got {len(args)}")
                args = [self.expand(a, active, depth + 1) for a in args]
                mapping = dict(zip(params, args))
                body = re.sub(r"\b[A-Za-z_]\w*\b", lambda w: mapping.get(w.group(0), w.group(0)), body)
                out.append(self.expand(body, active | {name}, depth + 1))
                i = end
            else:
                out.append(name)
        return "".join(out)

    @staticmethod
    def _args(text: str, open_at: int) -> tuple[list[str], int]:
        depth, args, cur = 0, [], []
        i = open_at
        while i < len(text):
            c = text[i]
            if c == "(":
                depth += 1
                if depth > 1:
                    cur.append(c)
            elif c == ")":
                depth -= 1
                if depth == 0:
                    args.append("".join(cur).strip())
                    return args, i + 1
                cur.append(c)
            elif c == "," and depth == 1:
                args.append("".join(cur).strip())
                cur = []
            else:
                cur.append(c)
            i += 1
        raise PreprocessError("unterminated macro call")

    def condition(self, expr: str) -> bool:
        def defined(m: re.Match) -> str:
            return "1" if self.defined(m.group(1) or m.group(2)) else "0"

        expr = re.sub(r"defined\s*\(\s*(\w+)\s*\)|defined\s+(\w+)", defined, expr)
        return bool(_Eval(self.expand(expr)).run())


_TOKEN = re.compile(
    r"\s*(?:(?P<f>\d+\.\d*(?:[eE][+-]?\d+)?[fF]?|\.\d+(?:[eE][+-]?\d+)?[fF]?)"
    r"|(?P<i>0[xX][0-9a-fA-F]+|\d+)[uUlL]*"
    r"|(?P<id>[A-Za-z_]\w*)"
    r"|(?P<op><<|>>|<=|>=|==|!=|&&|\|\||[-+*/%()!~<>&|^?:]))"
)
_PRECEDENCE = {
    "||": 1, "&&": 2, "|": 3, "^": 4, "&": 5, "==": 6, "!=": 6,
    "<": 7, ">": 7, "<=": 7, ">=": 7, "<<": 8, ">>": 8, "+": 9, "-": 9, "*": 10, "/": 10, "%": 10,
}


class _Eval:
    """#if expressions: integers (and floats, leniently), identifiers left over are 0."""

    def __init__(self, text: str):
        self.tokens: list[tuple[str, object]] = []
        pos, text = 0, text.strip()
        while pos < len(text):
            m = _TOKEN.match(text, pos)
            if not m:
                raise PreprocessError(f"bad #if expression: {text!r}")
            pos = m.end()
            if m.group("f") is not None:
                self.tokens.append(("n", float(m.group("f").rstrip("fF"))))
            elif m.group("i") is not None:
                self.tokens.append(("n", int(m.group("i"), 0)))
            elif m.group("id") is not None:
                self.tokens.append(("n", 0))
            else:
                self.tokens.append(("op", m.group("op")))
        self.i = 0

    def run(self):
        if not self.tokens:
            raise PreprocessError("empty #if expression")
        value = self.ternary()
        if self.i != len(self.tokens):
            raise PreprocessError("trailing tokens in #if expression")
        return value

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else (None, None)

    def take(self):
        t = self.tokens[self.i]
        self.i += 1
        return t

    def ternary(self):
        cond = self.binary(1)
        if self.peek() == ("op", "?"):
            self.take()
            a = self.ternary()
            if self.take() != ("op", ":"):
                raise PreprocessError("expected ':' in #if expression")
            b = self.ternary()
            return a if cond else b
        return cond

    def binary(self, level: int):
        left = self.unary()
        while True:
            kind, op = self.peek()
            prec = _PRECEDENCE.get(op) if kind == "op" else None
            if prec is None or prec < level:
                return left
            self.take()
            right = self.binary(prec + 1)
            left = self.apply(op, left, right)

    def unary(self):
        kind, value = self.take()
        if kind == "n":
            return value
        if value == "(":
            inner = self.ternary()
            if self.take() != ("op", ")"):
                raise PreprocessError("expected ')' in #if expression")
            return inner
        if value == "!":
            return 0 if self.unary() else 1
        if value == "-":
            return -self.unary()
        if value == "+":
            return self.unary()
        if value == "~":
            return ~int(self.unary())
        raise PreprocessError(f"unexpected {value!r} in #if expression")

    @staticmethod
    def apply(op: str, a, b):
        if op == "||":
            return 1 if (a or b) else 0
        if op == "&&":
            return 1 if (a and b) else 0
        if op in ("==", "!=", "<", ">", "<=", ">="):
            return int({"==": a == b, "!=": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op])
        if op in ("/", "%") and b == 0:
            return 0
        if op == "/":
            return int(a / b) if isinstance(a, int) and isinstance(b, int) else a / b
        if op == "%":
            return int(a) - int(b) * int(int(a) / int(b))
        a, b = (int(a), int(b)) if op in ("<<", ">>", "&", "|", "^") else (a, b)
        return {"+": a + b, "-": a - b, "*": a * b, "<<": a << b if b >= 0 else 0,
                ">>": a >> b if b >= 0 else 0, "&": a & b, "|": a | b, "^": a ^ b}[op]


def preprocess(
    path: str,
    read: Callable[[str], str],
    macros: Macros | None = None,
    result: Result | None = None,
    _depth: int = 0,
) -> Result:
    """Preprocesses `path` (a pack path such as `shaders/composite.fsh`). `read` returns a file's text."""
    if _depth > 24:
        raise PreprocessError("#include nested too deep")
    macros = macros or Macros()
    result = result or Result()
    raw = _splice(read(path))
    result.draw_buffers += _markers(raw)
    lines = _strip_comments(raw).split("\n")
    stack: list[list[bool]] = []  # [active, taken, parent_active]
    out: list[str] = []

    def active() -> bool:
        return all(s[0] for s in stack)

    for line in lines:
        m = _DIRECTIVE.match(line)
        if not m:
            out.append(macros.expand(line) if active() else "")
            continue
        word, rest = m.group(1), m.group(2).strip()
        out.append("")
        if word in ("if", "ifdef", "ifndef"):
            parent = active()
            if not parent:
                stack.append([False, True, False])
                continue
            if word == "if":
                cond = macros.condition(rest)
            elif word == "ifdef":
                cond = macros.defined(rest.split()[0])
            else:
                cond = not macros.defined(rest.split()[0])
            stack.append([cond, cond, True])
        elif word == "elif":
            if not stack:
                raise PreprocessError("#elif without #if")
            top = stack[-1]
            if not top[2] or top[1]:
                top[0] = False
            else:
                top[0] = macros.condition(rest)
                top[1] = top[0]
        elif word == "else":
            if not stack:
                raise PreprocessError("#else without #if")
            top = stack[-1]
            top[0] = top[2] and not top[1]
            top[1] = True
        elif word == "endif":
            if not stack:
                raise PreprocessError("#endif without #if")
            stack.pop()
        elif not active():
            continue
        elif word == "define":
            macros.define(rest)
        elif word == "undef":
            macros.undef(rest.split()[0])
        elif word == "include":
            target = rest.strip().strip('"<>')
            if target.startswith("/"):
                full = posixpath.normpath("shaders" + target)
            else:
                full = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
            result.includes.append(full)
            inner = preprocess(full, read, macros, result, _depth + 1)
            out[-1] = inner.text if inner is result else ""
        elif word == "version":
            digits = re.match(r"\d+", rest)
            if digits:
                result.version = int(digits.group(0))
        elif word in ("extension", "pragma", "line", "error", "warning"):
            pass
        else:
            raise PreprocessError(f"unknown directive #{word}")
    if stack:
        raise PreprocessError("#if without #endif")
    result.text = "\n".join(out)
    return result


def option_defines(raw: str) -> list[dict]:
    """The pack's own settings, as the loader finds them: `#define NAME value // [a b c]`, with the
    define commented out (`//#define NAME`) for an option that is off. For the report only."""
    found = []
    for line in _splice(raw).split("\n"):
        m = re.match(r"\s*(//)?\s*#define\s+([A-Za-z_]\w*)\s*([^/\n]*?)\s*(?://\s*(\[[^\]]*\]))?\s*$", line)
        if m and not m.group(3).startswith("("):
            found.append(
                {"name": m.group(2), "on": m.group(1) is None, "value": m.group(3), "choices": m.group(4)}
            )
    return found
