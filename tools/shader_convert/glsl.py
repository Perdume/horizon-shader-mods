"""A GLSL (1.20 - 3.30) subset to WGSL translator for the fragment programs of OptiFine/Iris packs that
map onto Horizon's `post` stage. It is a small typed compiler: WGSL has no implicit conversions, no multi-
component swizzle assignment and no scalar/vector mixing in clamp/min/max, so the translator tracks every
expression's type. Anything outside the subset raises `Unsupported` with the reason; it never guesses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import NamedTuple


class Unsupported(Exception):
    """A construct the translator does not handle (the reason is the message)."""


class GlslSyntaxError(Exception):
    pass


# ---------------------------------------------------------------------------------------------- types

SCALARS = ("float", "int", "uint", "bool")
WGSL_SCALAR = {"float": "f32", "int": "i32", "uint": "u32", "bool": "bool"}
VECS: dict[str, tuple[str, int]] = {}
for _n in (2, 3, 4):
    VECS[f"vec{_n}"] = ("float", _n)
    VECS[f"ivec{_n}"] = ("int", _n)
    VECS[f"uvec{_n}"] = ("uint", _n)
    VECS[f"bvec{_n}"] = ("bool", _n)
MATS = {"mat2": 2, "mat3": 3, "mat4": 4}
OTHER_TYPES = ("void", "sampler1D", "sampler2D", "sampler3D", "samplerCube", "sampler2DShadow",
               "sampler2DArray", "isampler2D", "usampler2D")
TYPE_NAMES = set(SCALARS) | set(VECS) | set(MATS) | set(OTHER_TYPES)
VEC_PREFIX = {"float": "vec", "int": "ivec", "uint": "uvec", "bool": "bvec"}


def wgsl_type(t: str) -> str:
    if t in WGSL_SCALAR:
        return WGSL_SCALAR[t]
    if t in VECS:
        b, n = VECS[t]
        return f"vec{n}<{WGSL_SCALAR[b]}>"
    if t in MATS:
        return f"mat{MATS[t]}x{MATS[t]}<f32>"
    raise Unsupported(f"type {t}")


def base_of(t: str) -> str:
    if t in SCALARS:
        return t
    if t in VECS:
        return VECS[t][0]
    if t in MATS:
        return "float"
    raise Unsupported(f"type {t}")


def size_of(t: str) -> int:
    if t in SCALARS:
        return 1
    if t in VECS:
        return VECS[t][1]
    if t in MATS:
        return MATS[t]
    raise Unsupported(f"type {t}")


def vec_type(base: str, n: int) -> str:
    return base if n == 1 else f"{VEC_PREFIX[base]}{n}"


class V(NamedTuple):
    """An emitted expression: its WGSL code, GLSL type and, for a plain number, its value."""

    code: str
    type: str
    lit: float | int | None = None


def to_float(v: V) -> V:
    if v.type == "float":
        return v
    if v.type in ("int", "uint"):
        if isinstance(v.lit, int):
            return V(f"{v.lit}.0", "float", float(v.lit))
        return V(f"f32({v.code})", "float")
    if v.type in VECS and VECS[v.type][0] in ("int", "uint"):
        n = VECS[v.type][1]
        return V(f"vec{n}<f32>({v.code})", f"vec{n}")
    raise Unsupported(f"cannot convert {v.type} to float")


def convert(v: V, t: str) -> V:
    """Implicit conversion of `v` to type `t` (int to float only, as GLSL allows)."""
    if v.type == t:
        return v
    if t == "float" and v.type in ("int", "uint"):
        return to_float(v)
    if t in VECS and VECS[t][0] == "float" and v.type in VECS and VECS[v.type][1] == VECS[t][1]:
        return to_float(v)
    raise Unsupported(f"cannot convert {v.type} to {t}")


def broadcast(v: V, t: str) -> V:
    """A scalar widened to the vector type `t`, for functions WGSL wants uniformly typed."""
    if v.type == t:
        return v
    if v.type in SCALARS and t in VECS:
        v = convert(v, VECS[t][0]) if VECS[t][0] == "float" else v
        return V(f"{wgsl_type(t)}({v.code})", t)
    return v


# ---------------------------------------------------------------------------------------------- lexer

class Tok(NamedTuple):
    kind: str
    text: str
    line: int


_TOKEN = re.compile(
    r"""(?P<ws>[ \t\r\f\v]+|\n)
     |(?P<num>0[xX][0-9a-fA-F]+[uU]?|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?[fFuU]?)
     |(?P<id>[A-Za-z_]\w*)
     |(?P<op><<=|>>=|\+\+|--|\+=|-=|\*=|/=|%=|&=|\|=|\^=|<<|>>|<=|>=|==|!=|&&|\|\||\^\^|[-+*/%<>=!~&|^?:;,.(){}\[\]])""",
    re.X,
)


def lex(text: str) -> list[Tok]:
    out: list[Tok] = []
    pos, line = 0, 1
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise GlslSyntaxError(f"line {line}: unexpected character {text[pos]!r}")
        pos = m.end()
        if m.lastgroup == "ws":
            line += m.group(0).count("\n")
        else:
            out.append(Tok(m.lastgroup, m.group(0), line))
    out.append(Tok("eof", "", line))
    return out


# ---------------------------------------------------------------------------------------------- parser

QUALIFIERS = {"const", "uniform", "varying", "attribute", "in", "out", "inout", "flat", "smooth",
              "noperspective", "centroid", "highp", "mediump", "lowp", "invariant", "layout", "precise"}
BINARY = {"||": 1, "^^": 1, "&&": 2, "|": 3, "^": 4, "&": 5, "==": 6, "!=": 6, "<": 7, ">": 7, "<=": 7,
          ">=": 7, "<<": 8, ">>": 8, "+": 9, "-": 9, "*": 10, "/": 10, "%": 10}
ASSIGN = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}


@dataclass
class Decl:  # a global declaration
    quals: set[str]
    type: str
    name: str
    init: tuple | None
    location: int | None = None


@dataclass
class Func:
    ret: str
    name: str
    params: list[tuple[str, str]]  # (type, name)
    body: list | None


class Parser:
    def __init__(self, text: str):
        self.t = lex(text)
        self.i = 0

    # -- token helpers
    def peek(self, k: int = 0) -> Tok:
        return self.t[min(self.i + k, len(self.t) - 1)]

    def next(self) -> Tok:
        tok = self.t[self.i]
        self.i += 1
        return tok

    def at(self, text: str) -> bool:
        return self.peek().text == text

    def accept(self, text: str) -> bool:
        if self.at(text):
            self.i += 1
            return True
        return False

    def expect(self, text: str) -> Tok:
        tok = self.next()
        if tok.text != text:
            raise GlslSyntaxError(f"line {tok.line}: expected {text!r}, found {tok.text!r}")
        return tok

    def ident(self) -> str:
        tok = self.next()
        if tok.kind != "id":
            raise GlslSyntaxError(f"line {tok.line}: expected a name, found {tok.text!r}")
        return tok.text

    # -- declarations
    def unit(self) -> tuple[list[Decl], list[Func]]:
        decls: list[Decl] = []
        funcs: list[Func] = []
        while self.peek().kind != "eof":
            if self.accept(";"):
                continue
            if self.at("precision"):
                while not self.accept(";"):
                    self.next()
                continue
            quals, location = self.qualifiers()
            if self.at("struct"):
                raise Unsupported("struct")
            ty = self.type_name()
            name = self.ident()
            if self.at("("):
                f = self.function(ty, name)
                if f.body is not None:
                    funcs.append(f)
            else:
                decls += self.global_declarators(quals, location, ty, name)
        return decls, funcs

    def qualifiers(self) -> tuple[set[str], int | None]:
        quals: set[str] = set()
        location = None
        while self.peek().text in QUALIFIERS:
            word = self.next().text
            if word == "layout":
                self.expect("(")
                depth = 1
                while depth:
                    tok = self.next()
                    if tok.text == "(":
                        depth += 1
                    elif tok.text == ")":
                        depth -= 1
                    elif tok.text == "location" and self.peek().text == "=":
                        self.next()
                        location = int(self.next().text)
            else:
                quals.add(word)
        return quals, location

    def type_name(self) -> str:
        tok = self.next()
        if tok.text not in TYPE_NAMES:
            raise Unsupported(f"type {tok.text!r}" if tok.kind == "id" else f"line {tok.line}: expected a type")
        if self.at("["):
            raise Unsupported("arrays")
        return tok.text

    def global_declarators(self, quals, location, ty, name) -> list[Decl]:
        out = []
        while True:
            if self.at("["):
                raise Unsupported("arrays")
            init = self.assign_expr() if self.accept("=") else None
            out.append(Decl(quals, ty, name, init, location))
            if self.accept(","):
                name = self.ident()
                continue
            self.expect(";")
            return out

    def function(self, ret: str, name: str) -> Func:
        self.expect("(")
        params: list[tuple[str, str]] = []
        if not self.at(")"):
            if self.at("void") and self.peek(1).text == ")":
                self.next()
            else:
                while True:
                    quals, _ = self.qualifiers()
                    if "out" in quals or "inout" in quals:
                        raise Unsupported("out/inout parameters")
                    pty = self.type_name()
                    pname = self.ident() if self.peek().kind == "id" else f"p{len(params)}"
                    if self.at("["):
                        raise Unsupported("arrays")
                    params.append((pty, pname))
                    if not self.accept(","):
                        break
        self.expect(")")
        if self.accept(";"):
            return Func(ret, name, params, None)
        return Func(ret, name, params, self.block())

    # -- statements
    def block(self) -> list:
        self.expect("{")
        out = []
        while not self.at("}"):
            out.append(self.statement())
        self.expect("}")
        return out

    def is_decl_start(self) -> bool:
        t = self.peek()
        if t.text in ("const", "highp", "mediump", "lowp", "precise"):
            return True
        return t.text in TYPE_NAMES and self.peek(1).kind == "id"

    def statement(self):
        t = self.peek()
        if t.text == "{":
            return ("block", self.block())
        if t.text == ";":
            self.next()
            return ("block", [])
        if t.text == "if":
            self.next()
            self.expect("(")
            cond = self.expression()
            self.expect(")")
            then = self.statement()
            other = self.statement() if self.accept("else") else None
            return ("if", cond, then, other)
        if t.text == "for":
            self.next()
            self.expect("(")
            init = None
            if not self.at(";"):
                init = self.declaration() if self.is_decl_start() else self.expr_statement()
            else:
                self.next()
            cond = None if self.at(";") else self.expression()
            self.expect(";")
            step = None if self.at(")") else self.expression()
            self.expect(")")
            return ("for", init, cond, step, self.statement())
        if t.text == "while":
            self.next()
            self.expect("(")
            cond = self.expression()
            self.expect(")")
            return ("while", cond, self.statement())
        if t.text == "do":
            raise Unsupported("do-while loops")
        if t.text == "return":
            self.next()
            value = None if self.at(";") else self.expression()
            self.expect(";")
            return ("return", value)
        if t.text in ("break", "continue"):
            self.next()
            self.expect(";")
            return (t.text,)
        if t.text == "discard":
            raise Unsupported("discard")
        if t.text == "switch":
            raise Unsupported("switch")
        if self.is_decl_start():
            return self.declaration()
        return self.expr_statement()

    def expr_statement(self):
        e = self.expression()
        self.expect(";")
        return ("expr", e)

    def declaration(self):
        quals, _ = self.qualifiers()
        ty = self.type_name()
        items = []
        while True:
            name = self.ident()
            if self.at("["):
                raise Unsupported("arrays")
            init = self.assign_expr() if self.accept("=") else None
            items.append((name, init))
            if not self.accept(","):
                break
        self.expect(";")
        return ("decl", "const" in quals, ty, items)

    # -- expressions
    def expression(self):
        e = self.assign_expr()
        if self.at(","):
            raise Unsupported("the comma operator")
        return e

    def assign_expr(self):
        left = self.binary(1)
        if self.accept("?"):
            a = self.assign_expr()
            self.expect(":")
            b = self.assign_expr()
            return ("cond", left, a, b)
        if self.peek().text in ASSIGN:
            op = self.next().text
            return ("asg", op, left, self.assign_expr())
        return left

    def binary(self, level: int):
        left = self.unary()
        while self.peek().text in BINARY and BINARY[self.peek().text] >= level:
            op = self.next().text
            right = self.binary(BINARY[op] + 1)
            left = ("bin", op, left, right)
        return left

    def unary(self):
        t = self.peek()
        if t.text in ("-", "+", "!", "~", "++", "--"):
            self.next()
            return ("un", t.text, self.unary())
        return self.postfix()

    def postfix(self):
        e = self.primary()
        while True:
            if self.accept("."):
                e = ("field", e, self.ident())
            elif self.accept("["):
                idx = self.expression()
                self.expect("]")
                e = ("index", e, idx)
            elif self.peek().text in ("++", "--"):
                e = ("post", self.next().text, e)
            else:
                return e

    def primary(self):
        t = self.next()
        if t.kind == "num":
            return ("num", t.text)
        if t.text == "(":
            e = self.expression()
            self.expect(")")
            return e
        if t.kind == "id":
            if self.at("("):
                self.next()
                args = []
                if not self.at(")"):
                    while True:
                        args.append(self.assign_expr())
                        if not self.accept(","):
                            break
                self.expect(")")
                return ("call", t.text, args)
            if self.at("[") and t.text in TYPE_NAMES and self.peek(1).text == "]":
                raise Unsupported("array constructors")
            return ("id", t.text)
        raise GlslSyntaxError(f"line {t.line}: unexpected {t.text!r}")


# ---------------------------------------------------------------------------------------------- emitter

WGSL_RESERVED = set("""
alias break case const const_assert continue continuing default diagnostic discard else enable false fn for if let
loop override requires return struct switch true var while
NULL Self abstract active alignas alignof as asm asm_fragment async attribute auto await become binding_array cast
catch class co_await co_return co_yield coherent column_major common compile compile_fragment concept const_cast
consteval constexpr constinit crate debugger decltype delete demote demote_to_helper do dynamic_cast enum explicit
export extends extern external fallthrough filter final finally friend from fxgroup get goto groupshared highp impl
implements import inline instanceof interface layout lowp macro macro_rules match mediump meta mod module move mut
mutable namespace new nil noexcept noinline nointerpolation noperspective null nullptr of operator package packoffset
partition pass patch pixelfragment precise precision premerge priv protected pub public readonly ref regardless
register reinterpret_cast require resource restrict self set shared sizeof smooth snorm static static_assert
static_cast std subroutine super target template this thread_local throw trait try type typedef typeid typename
typeof union unless unorm unsafe unsized use using varying virtual volatile wgsl where with writeonly yield
abs acos acosh all any arrayLength asin asinh atan atan2 atanh ceil clamp cos cosh countLeadingZeros countOneBits
countTrailingZeros cross degrees determinant distance dot dpdx dpdy exp exp2 extractBits faceForward firstLeadingBit
firstTrailingBit floor fma fract frexp insertBits inverseSqrt ldexp length log log2 max min mix modf normalize pow
quantizeToF16 radians reflect refract reverseBits round saturate select sign sin sinh smoothstep sqrt step tan tanh
transpose trunc textureDimensions textureGather textureLoad textureSample textureSampleLevel textureStore
bool f32 f16 i32 u32 array ptr atomic sampler
frame previous scene_color scene_depth linear_sampler nearest_sampler depth_at view_position post input
Frame PostInput SkyInput MaterialInput Surface
""".split())


def rename(name: str) -> str:
    if name in WGSL_RESERVED or name.startswith("_") or name.startswith("horizon_") or name.startswith("hs_"):
        return "u_" + name.lstrip("_")
    if re.fullmatch(r"(vec|mat)[234](x[234])?|texture_\w+|sampler\w*", name):
        return "u_" + name
    return name


SWIZZLE = {"x": "x", "y": "y", "z": "z", "w": "w", "r": "x", "g": "y", "b": "z", "a": "w",
           "s": "x", "t": "y", "p": "z", "q": "w"}

# GLSL functions that keep their name and work per component (the result has the argument's type).
SAME = {"radians", "degrees", "sin", "cos", "tan", "asin", "acos", "exp", "log", "exp2", "log2", "sqrt",
        "abs", "sign", "floor", "ceil", "fract", "trunc", "normalize"}
RENAMED = {"inversesqrt": "inverseSqrt", "faceforward": "faceForward"}
UNSUPPORTED_FUNCS = {
    "inverse": "inverse() has no WGSL equivalent",
    "matrixCompMult": "matrixCompMult()",
    "dFdx": "derivatives", "dFdy": "derivatives", "fwidth": "derivatives",
    "ftransform": "ftransform()", "noise1": "noise()", "noise2": "noise()", "noise3": "noise()",
    "noise4": "noise()", "floatBitsToInt": "bit casts", "intBitsToFloat": "bit casts",
    "isnan": "isnan()", "isinf": "isinf()", "roundEven": "roundEven()", "outerProduct": "outerProduct()",
}
RELATIONAL = {"lessThan": "<", "lessThanEqual": "<=", "greaterThan": ">", "greaterThanEqual": ">=",
              "equal": "==", "notEqual": "!="}


@dataclass
class Env:
    """What a program may read: Horizon equivalents of the pack's uniforms, samplers and varyings."""

    uniforms: dict[str, tuple[str, str]]  # name -> (WGSL expression, GLSL type)
    samplers: dict[str, str]  # name -> "color" | "depth"
    varyings: dict[str, tuple[str, str]]  # name -> (WGSL expression, GLSL type)
    # Uniforms the program declared but Horizon has nothing for, filled while translating.
    missing: set[str] = field(default_factory=set)


@dataclass
class FuncInfo:
    wgsl: str
    params: list[str]
    ret: str


class Translator:
    def __init__(self, decls: list[Decl], funcs: list[Func], env: Env):
        self.decls, self.funcs, self.env = decls, funcs, env
        self.scopes: list[dict[str, tuple[str, str]]] = [{}]
        self.user_funcs: dict[str, list[FuncInfo]] = {}
        self.out_vars: dict[str, int] = {}  # GLSL 1.30+ `out vec4 name` -> its location
        self.declared_uniforms: dict[str, str] = {}
        self.declared_varyings: dict[str, str] = {}
        self.globals_wgsl: list[str] = []
        self.helpers: set[str] = set()
        self.in_main = False
        self.tmp = 0
        self.notes: list[str] = []

    # -- scope
    def lookup(self, name: str):
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    def bind(self, name: str, ty: str) -> str:
        w = rename(name)
        self.scopes[-1][name] = (w, ty)
        return w

    # -- driver
    def translate(self) -> dict:
        for d in self.decls:
            self.global_decl(d)
        for f in self.funcs:
            if f.name != "main":
                self.register(f)
        out = []
        for f in self.funcs:
            if f.name != "main":
                out.append(self.function(f))
        main = next((f for f in self.funcs if f.name == "main"), None)
        if main is None:
            raise Unsupported("no main()")
        out.append(self.main(main))
        return {"globals": self.globals_wgsl, "functions": out, "helpers": sorted(self.helpers),
                "notes": self.notes}

    def global_decl(self, d: Decl) -> None:
        q = d.quals
        if "uniform" in q:
            self.declared_uniforms[d.name] = d.type
            return
        if "varying" in q or ("in" in q and "const" not in q):
            self.declared_varyings[d.name] = d.type
            return
        if "out" in q:
            self.out_vars[d.name] = d.location or 0
            return
        if "attribute" in q:
            raise Unsupported("vertex attributes in a fragment program")
        w = rename(d.name)
        if d.init is None:
            if "const" in q:
                raise Unsupported("const without initializer")
            self.globals_wgsl.append(f"var<private> {w}: {wgsl_type(d.type)};")
            self.scopes[0][d.name] = (w, d.type)
            return
        v = convert(self.expr(d.init), d.type)
        keyword = "const" if "const" in q else "var<private>"
        self.globals_wgsl.append(f"{keyword} {w}: {wgsl_type(d.type)} = {v.code};")
        self.scopes[0][d.name] = (w, d.type)

    def register(self, f: Func) -> None:
        overloads = self.user_funcs.setdefault(f.name, [])
        suffix = f"_{len(overloads) + 1}" if overloads or any(
            g is not f and g.name == f.name for g in self.funcs
        ) else ""
        overloads.append(FuncInfo(rename(f.name) + suffix, [t for t, _ in f.params], f.ret))

    def function(self, f: Func) -> str:
        info = next(i for i in self.user_funcs[f.name] if i.params == [t for t, _ in f.params])
        self.scopes.append({})
        params, copies = [], []
        assigned = assigned_names(f.body or [])
        for ty, name in f.params:
            w = rename(name)
            if name in assigned:
                params.append(f"{w}_in: {wgsl_type(ty)}")
                copies.append(f"    var {w}: {wgsl_type(ty)} = {w}_in;")
            else:
                params.append(f"{w}: {wgsl_type(ty)}")
            self.scopes[-1][name] = (w, ty)
        self.current_ret = f.ret
        body = self.statements(f.body or [], 1)
        self.scopes.pop()
        ret = "" if f.ret == "void" else f" -> {wgsl_type(f.ret)}"
        return f"fn {info.wgsl}({', '.join(params)}){ret} {{\n" + "\n".join(copies + body) + "\n}"

    def main(self, f: Func) -> str:
        self.in_main = True
        self.current_ret = "void"
        self.scopes.append({})
        body = self.statements(f.body or [], 1)
        self.scopes.pop()
        self.in_main = False
        head = ["    var hs_out: vec4<f32> = input.color;", "    var hs_unused: vec4<f32> = vec4<f32>(0.0);"]
        return ("fn post(input: PostInput) -> vec4<f32> {\n" + "\n".join(head + body)
                + "\n    return hs_out;\n}")

    # -- statements
    def statements(self, stmts: list, depth: int) -> list[str]:
        out: list[str] = []
        for s in stmts:
            out += self.statement(s, depth)
        return out

    def scoped(self, stmt, depth: int) -> list[str]:
        self.scopes.append({})
        lines = self.statements(stmt[1], depth) if stmt[0] == "block" else self.statement(stmt, depth)
        self.scopes.pop()
        return lines

    def statement(self, s, depth: int) -> list[str]:
        pad = "    " * depth
        kind = s[0]
        if kind == "block":
            return [pad + "{"] + self.scoped(s, depth + 1) + [pad + "}"]
        if kind == "decl":
            _, is_const, ty, items = s
            lines = []
            for name, init in items:
                value = convert(self.expr(init), ty) if init is not None else None
                w = self.bind(name, ty)
                if is_const and value is not None:
                    lines.append(f"{pad}let {w}: {wgsl_type(ty)} = {value.code};")
                elif value is not None:
                    lines.append(f"{pad}var {w}: {wgsl_type(ty)} = {value.code};")
                else:
                    lines.append(f"{pad}var {w}: {wgsl_type(ty)};")
            return lines
        if kind == "expr":
            return [pad + line for line in self.expr_statement(s[1])]
        if kind == "if":
            _, cond, then, other = s
            c = self.expr(cond)
            if c.type != "bool":
                raise Unsupported("if condition is not a bool")
            lines = [f"{pad}if {c.code} {{"] + self.scoped(then if then[0] == "block" else ("block", [then]), depth + 1)
            if other is not None:
                if other[0] == "if":
                    nested = self.statement(other, depth)
                    nested[0] = f"{pad}}} else " + nested[0].lstrip()
                    return lines + nested
                lines += [f"{pad}}} else {{"] + self.scoped(other if other[0] == "block" else ("block", [other]), depth + 1)
            return lines + [pad + "}"]
        if kind == "for":
            _, init, cond, step, body = s
            self.scopes.append({})
            init_s = ""
            if init is not None:
                init_lines = self.statement(init, 0)
                if len(init_lines) != 1:
                    raise Unsupported("a for loop initializer with several variables")
                init_s = init_lines[0].rstrip(";")
            cond_s = ""
            if cond is not None:
                c = self.expr(cond)
                if c.type != "bool":
                    raise Unsupported("for condition is not a bool")
                cond_s = c.code
            step_s = ""
            if step is not None:
                step_lines = self.expr_statement(step)
                if len(step_lines) != 1:
                    raise Unsupported("a complex for loop update")
                step_s = step_lines[0].rstrip(";")
            inner = self.scoped(body if body[0] == "block" else ("block", [body]), depth + 1)
            self.scopes.pop()
            return [f"{pad}for ({init_s}; {cond_s}; {step_s}) {{"] + inner + [pad + "}"]
        if kind == "while":
            c = self.expr(s[1])
            return [f"{pad}while {c.code} {{"] + self.scoped(s[2] if s[2][0] == "block" else ("block", [s[2]]), depth + 1) + [pad + "}"]
        if kind == "return":
            if self.in_main:
                return [pad + "return hs_out;"]
            if s[1] is None:
                return [pad + "return;"]
            return [pad + f"return {convert(self.expr(s[1]), self.current_ret).code};"]
        if kind in ("break", "continue"):
            return [pad + kind + ";"]
        raise Unsupported(f"statement {kind}")

    def expr_statement(self, e) -> list[str]:
        if e[0] == "asg":
            return self.assignment(e[1], e[2], e[3])
        if e[0] in ("post", "un") and e[1] in ("++", "--"):
            target = e[2]
            t = self.lvalue(target)
            one = "1.0" if base_of(t.type) == "float" else "1"
            return [f"{t.code} {e[1][0]}= {one};"]
        if e[0] == "call" and e[1] in self.user_funcs:
            return [self.expr(e).code + ";"]
        raise Unsupported("an expression statement that is not an assignment or a call")

    def lvalue(self, e) -> V:
        """The WGSL place an assignment writes, for plain variables, components and fragment outputs."""
        if e[0] == "id":
            if e[1] in self.out_vars:
                return V("hs_out" if self.out_vars[e[1]] == 0 else "hs_unused", "vec4")
            if e[1] == "gl_FragColor":
                return V("hs_out", "vec4")
            found = self.lookup(e[1])
            if found is None:
                raise Unsupported(f"assignment to {e[1]!r}")
            return V(found[0], found[1])
        if e[0] == "index" and e[1] == ("id", "gl_FragData"):
            k = self.expr(e[2])
            if k.lit is None:
                raise Unsupported("gl_FragData with a computed index")
            return V("hs_out" if k.lit == 0 else "hs_unused", "vec4")
        if e[0] == "index":
            base = self.lvalue(e[1])
            i = self.expr(e[2])
            return V(f"{base.code}[{i.code}]", self.index_type(base.type))
        if e[0] == "field":
            base = self.lvalue(e[1])
            sw, ty = self.swizzle(base.type, e[2])
            return V(f"{base.code}.{sw}", ty)
        raise Unsupported("assignment to this expression")

    def assignment(self, op: str, lhs, rhs) -> list[str]:
        target = self.lvalue(lhs)
        value = self.expr(rhs)
        # Several components at once (`c.rgb = x`): WGSL only assigns one component per statement.
        m = re.search(r"\.([xyzw]{2,4})$", target.code)
        if m:
            comps = m.group(1)
            prefix = target.code[: m.start()]
            value = convert(value, target.type)
            tmp = f"hs_t{self.tmp}"
            self.tmp += 1
            lines = ["{", f"    let {tmp} = {value.code};"]
            for k, c in enumerate(comps):
                lines.append(f"    {prefix}.{c} {op} {tmp}.{'xyzw'[k]};")
            return lines + ["}"]
        if op != "=":
            if base_of(target.type) == "float":
                value = to_float(value) if value.type in ("int", "uint") else value
            return [f"{target.code} {op} {value.code};"]
        return [f"{target.code} = {convert(value, target.type).code};"]

    # -- expressions
    def expr(self, e) -> V:
        kind = e[0]
        if kind == "num":
            return self.number(e[1])
        if kind == "id":
            return self.identifier(e[1])
        if kind == "call":
            return self.call(e[1], e[2])
        if kind == "field":
            base = self.expr(e[1])
            sw, ty = self.swizzle(base.type, e[2])
            return V(f"{base.code}.{sw}", ty)
        if kind == "index":
            if e[1] == ("id", "gl_TexCoord"):
                if self.expr(e[2]).lit != 0:
                    raise Unsupported("gl_TexCoord other than [0]")
                return V("vec4<f32>(input.uv, 0.0, 1.0)", "vec4")
            base = self.expr(e[1])
            i = self.expr(e[2])
            return V(f"{base.code}[{i.code}]", self.index_type(base.type))
        if kind == "un":
            return self.unary(e[1], self.expr(e[2]), e[2])
        if kind == "bin":
            return self.binary(e[1], self.expr(e[2]), self.expr(e[3]))
        if kind == "cond":
            c, a, b = self.expr(e[1]), self.expr(e[2]), self.expr(e[3])
            if c.type != "bool":
                raise Unsupported("?: condition is not a bool")
            a, b = self.unify(a, b)
            return V(f"select({b.code}, {a.code}, {c.code})", a.type)
        if kind in ("asg", "post"):
            raise Unsupported("an assignment or increment inside an expression")
        raise Unsupported(f"expression {kind}")

    def number(self, text: str) -> V:
        t = text.rstrip("fFuU") if not text.lower().startswith("0x") else text
        is_float = (("." in text or "e" in text.lower()) and not text.lower().startswith("0x")) or (
            text[-1] in "fF" and not text.lower().startswith("0x"))
        if is_float:
            return V(t if re.search(r"[.eE]", t) else t + ".0", "float", float(t))
        if text[-1] in "uU":
            return V(f"{int(t, 0)}u", "uint", int(t, 0))
        return V(str(int(t, 0)), "int", int(t, 0))

    def identifier(self, name: str) -> V:
        found = self.lookup(name)
        if found is not None:
            return V(found[0], found[1])
        if name in self.declared_uniforms:
            ty = self.declared_uniforms[name]
            if ty.startswith("sampler"):
                if name not in self.env.samplers:
                    self.env.missing.add(name)
                    raise Unsupported(f"reads the buffer {name}")
                return V(name, "sampler2D")
            if name not in self.env.uniforms:
                self.env.missing.add(name)
                raise Unsupported(f"uniform {name} has no Horizon equivalent")
            code, mapped_type = self.env.uniforms[name]
            return V(code, mapped_type)
        if name in self.declared_varyings:
            if name not in self.env.varyings:
                self.env.missing.add(name)
                raise Unsupported(f"varying {name} is computed by the vertex shader")
            code, ty = self.env.varyings[name]
            return V(code, ty)
        if name in self.out_vars or name == "gl_FragColor":
            return V("hs_out", "vec4")
        if name == "gl_FragData":
            raise Unsupported("gl_FragData read as a whole")
        if name == "gl_FragCoord":
            self.helpers.add("fragcoord")
            return V("hs_fragcoord(input)", "vec4")
        if name == "gl_TexCoord":
            raise Unsupported("gl_TexCoord outside gl_TexCoord[0]")
        raise Unsupported(f"unknown name {name!r}")

    def swizzle(self, ty: str, sw: str) -> tuple[str, str]:
        if ty not in VECS:
            raise Unsupported(f"field access .{sw} on {ty}")
        b, n = VECS[ty]
        if not all(c in SWIZZLE for c in sw) or len(sw) > 4:
            raise Unsupported(f"swizzle .{sw}")
        comps = "".join(SWIZZLE[c] for c in sw)
        if any("xyzw".index(c) >= n for c in comps):
            raise Unsupported(f"swizzle .{sw} out of range for {ty}")
        return comps, vec_type(b, len(comps))

    @staticmethod
    def index_type(ty: str) -> str:
        if ty in VECS:
            return VECS[ty][0]
        if ty in MATS:
            return f"vec{MATS[ty]}"
        raise Unsupported(f"indexing {ty}")

    def unify(self, a: V, b: V) -> tuple[V, V]:
        if a.type == b.type:
            return a, b
        if base_of(a.type) == "float" and b.type in ("int", "uint"):
            return a, to_float(b)
        if base_of(b.type) == "float" and a.type in ("int", "uint"):
            return to_float(a), b
        return a, b

    def unary(self, op: str, v: V, node) -> V:
        if op in ("++", "--"):
            raise Unsupported("an increment inside an expression")
        if op == "-":
            return V(f"(-{v.code})", v.type, -v.lit if v.lit is not None else None)
        if op == "+":
            return v
        if op == "!":
            return V(f"(!{v.code})", v.type)
        return V(f"(~{v.code})", v.type)

    def binary(self, op: str, a: V, b: V) -> V:
        if op in ("&&", "||"):
            return V(f"({a.code} {op} {b.code})", "bool")
        if op == "^^":
            return V(f"({a.code} != {b.code})", "bool")
        a, b = self.unify(a, b)
        if op in ("==", "!="):
            code = f"({a.code} {op} {b.code})"
            if a.type in VECS or a.type in MATS:
                code = f"{'all' if op == '==' else 'any'}{code}"
            return V(code, "bool")
        if op in ("<", ">", "<=", ">="):
            return V(f"({a.code} {op} {b.code})", "bool")
        if op in ("<<", ">>", "&", "|", "^", "%"):
            if base_of(a.type) == "float":
                raise Unsupported(f"operator {op} on floats")
            return V(f"({a.code} {op} {b.code})", a.type)
        ta, tb = a.type, b.type
        if ta == tb:
            rt = ta
        elif ta in SCALARS and (tb in VECS or tb in MATS):
            rt = tb
        elif tb in SCALARS and (ta in VECS or ta in MATS):
            rt = ta
        elif op == "*" and ta in MATS and tb in VECS:
            rt = tb
        elif op == "*" and ta in VECS and tb in MATS:
            rt = ta
        else:
            raise Unsupported(f"operator {op} on {ta} and {tb}")
        return V(f"({a.code} {op} {b.code})", rt)

    # -- calls
    def call(self, name: str, arg_nodes: list) -> V:
        if name in SCALARS or name in VECS or name in MATS:
            return self.construct(name, [self.expr(a) for a in arg_nodes])
        if name in UNSUPPORTED_FUNCS:
            raise Unsupported(UNSUPPORTED_FUNCS[name])
        if name in ("texture2D", "texture", "texture2DLod", "textureLod", "texelFetch", "textureSize",
                    "texture2DProj", "textureProj", "texture2DGrad"):
            return self.texture(name, arg_nodes)
        args = [self.expr(a) for a in arg_nodes]
        if name in self.user_funcs:
            return self.user_call(name, args)
        return self.builtin(name, args)

    def user_call(self, name: str, args: list[V]) -> V:
        for info in self.user_funcs[name]:
            if len(info.params) != len(args):
                continue
            try:
                conv = [convert(a, p) for a, p in zip(args, info.params)]
            except Unsupported:
                continue
            return V(f"{info.wgsl}({', '.join(c.code for c in conv)})", info.ret)
        raise Unsupported(f"no overload of {name} fits its arguments")

    def construct(self, ty: str, args: list[V]) -> V:
        if not args:
            raise Unsupported(f"{ty}() without arguments")
        if ty in SCALARS:
            if len(args) != 1 or args[0].type not in SCALARS:
                raise Unsupported(f"{ty}() of this argument")
            a = args[0]
            if ty == "float" and isinstance(a.lit, (int, float)):
                return V(f"{float(a.lit)}", "float", float(a.lit))
            return V(f"{WGSL_SCALAR[ty]}({a.code})", ty)
        if ty in VECS:
            b, n = VECS[ty]
            parts, count = [], 0
            for a in args:
                if b == "float" and base_of(a.type) in ("int", "uint"):
                    a = to_float(a)
                elif base_of(a.type) != b and a.type in SCALARS:
                    a = V(f"{WGSL_SCALAR[b]}({a.code})", b)
                elif base_of(a.type) != b:
                    a = V(f"{wgsl_type(vec_type(b, size_of(a.type)))}({a.code})", vec_type(b, size_of(a.type)))
                parts.append(a.code)
                count += size_of(a.type) if a.type not in MATS else 0
            if len(args) == 1 and args[0].type in SCALARS:
                return V(f"{wgsl_type(ty)}({parts[0]})", ty)
            if count != n:
                raise Unsupported(f"{ty}() with {count} components")
            return V(f"{wgsl_type(ty)}({', '.join(parts)})", ty)
        n = MATS[ty]
        if len(args) == 1 and args[0].type in SCALARS:
            d = to_float(args[0]).code
            cells = [d if r == c else "0.0" for c in range(n) for r in range(n)]
            return V(f"{wgsl_type(ty)}({', '.join(cells)})", ty)
        if all(a.type in SCALARS for a in args) and len(args) == n * n:
            return V(f"{wgsl_type(ty)}({', '.join(to_float(a).code for a in args)})", ty)
        if all(a.type == f"vec{n}" for a in args) and len(args) == n:
            return V(f"{wgsl_type(ty)}({', '.join(a.code for a in args)})", ty)
        raise Unsupported(f"{ty}() of these arguments")

    def sampler_of(self, node) -> str:
        if node[0] != "id":
            raise Unsupported("a texture read through a computed sampler")
        v = self.identifier(node[1])
        if v.type != "sampler2D":
            raise Unsupported("not a sampler")
        return self.env.samplers[node[1]]

    def texture(self, name: str, arg_nodes: list) -> V:
        kind = self.sampler_of(arg_nodes[0])
        rest = [self.expr(a) for a in arg_nodes[1:]]
        if name == "textureSize":
            return V("vec2<i32>(textureDimensions(previous))" if kind == "color"
                     else "vec2<i32>(textureDimensions(scene_depth))", "ivec2")
        if name == "texelFetch":
            p = rest[0]
            if p.type != "ivec2":
                raise Unsupported("texelFetch position is not an ivec2")
            if kind == "color":
                return V(f"textureLoad(previous, {p.code}, 0)", "vec4")
            self.helpers.add("depth_tex")
            return V(f"vec4<f32>(1.0 - textureLoad(scene_depth, {p.code}, 0), 0.0, 0.0, 1.0)", "vec4")
        uv = rest[0]
        if name in ("texture2DProj", "textureProj"):
            if uv.type == "vec4":
                uv = V(f"({uv.code}.xy / {uv.code}.w)", "vec2")
            elif uv.type == "vec3":
                uv = V(f"({uv.code}.xy / {uv.code}.z)", "vec2")
        if uv.type != "vec2":
            raise Unsupported("a texture read with a non-2D coordinate")
        if kind == "color":
            return V(f"textureSampleLevel(previous, linear_sampler, {uv.code}, 0.0)", "vec4")
        self.helpers.add("depth_tex")
        return V(f"hs_depth_tex({uv.code})", "vec4")

    def builtin(self, name: str, args: list[V]) -> V:
        if name in SAME and len(args) == 1:
            a = to_float(args[0]) if args[0].type in ("int", "uint") and name not in ("abs", "sign") else args[0]
            return V(f"{name}({a.code})", a.type)
        if name in RENAMED:
            a = args[0]
            return V(f"{RENAMED[name]}({', '.join(x.code for x in args)})", a.type)
        if name == "atan":
            if len(args) == 1:
                return V(f"atan({to_float(args[0]).code})", to_float(args[0]).type)
            a, b = self.unify(to_float(args[0]), to_float(args[1]))
            return V(f"atan2({a.code}, {b.code})", a.type)
        if name == "mod":
            a, b = self.unify(args[0], args[1])
            if base_of(a.type) != "float":
                raise Unsupported("mod() on integers")
            t = a.type if a.type in VECS else b.type
            return V(f"({a.code} - {b.code} * floor({a.code} / {b.code}))", t)
        if name in ("min", "max", "pow", "step"):
            a, b = self.unify(args[0], args[1])
            target = a.type if a.type in VECS else b.type
            a, b = broadcast(a, target), broadcast(b, target)
            result = b.type if name == "step" else a.type
            return V(f"{name}({a.code}, {b.code})", result)
        if name == "clamp":
            x, lo, hi = args
            x, lo = self.unify(x, lo)
            x, hi = self.unify(x, hi)
            lo, hi = self.unify(lo, hi)
            target = x.type
            return V(f"clamp({x.code}, {broadcast(lo, target).code}, {broadcast(hi, target).code})", target)
        if name == "smoothstep":
            lo, hi, x = args
            lo, x = self.unify(lo, x)
            hi, x = self.unify(hi, x)
            return V(f"smoothstep({broadcast(lo, x.type).code}, {broadcast(hi, x.type).code}, {x.code})", x.type)
        if name == "mix":
            a, b, t = args
            a, b = self.unify(a, b)
            if t.type == "bool" or t.type in VECS and VECS[t.type][0] == "bool":
                raise Unsupported("mix() with a bool selector")
            t = to_float(t) if t.type in ("int", "uint") else t
            return V(f"mix({a.code}, {b.code}, {t.code})", a.type)
        if name == "saturate":
            raise Unsupported("saturate")
        if name in ("length", "distance", "dot"):
            if len(args) == 2:
                a, b = self.unify(args[0], args[1])
                return V(f"{name}({a.code}, {b.code})", "float")
            return V(f"{name}({to_float(args[0]).code})", "float")
        if name == "cross":
            return V(f"cross({args[0].code}, {args[1].code})", "vec3")
        if name in ("reflect", "refract"):
            return V(f"{name}({', '.join(a.code for a in args)})", args[0].type)
        if name in ("transpose", "determinant"):
            return V(f"{name}({args[0].code})", args[0].type if name == "transpose" else "float")
        if name in RELATIONAL:
            a, b = self.unify(args[0], args[1])
            n = size_of(a.type)
            return V(f"({a.code} {RELATIONAL[name]} {b.code})", vec_type("bool", n))
        if name in ("any", "all"):
            return V(f"{name}({args[0].code})", "bool")
        if name == "not":
            return V(f"(!{args[0].code})", args[0].type)
        raise Unsupported(f"function {name}()")


def assigned_names(stmts) -> set[str]:
    """Names a function body assigns to (WGSL parameters are read-only, so those get a local copy)."""
    found: set[str] = set()

    def target(e):
        while e[0] in ("field", "index"):
            e = e[1]
        if e[0] == "id":
            found.add(e[1])

    def walk(node):
        if isinstance(node, tuple):
            if node and node[0] == "asg":
                target(node[2])
            elif node and node[0] in ("post", "un") and len(node) > 2 and node[1] in ("++", "--"):
                target(node[2])
            for child in node:
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(stmts)
    return found


HELPERS = {
    "depth_tex": (
        "// OptiFine's depth: 0 near, 1 far and sky. Horizon's is reversed, so this is one minus it.\n"
        "fn hs_depth_tex(uv: vec2<f32>) -> vec4<f32> {\n"
        "    return vec4<f32>(1.0 - depth_at(uv), 0.0, 0.0, 1.0);\n}"
    ),
    "fragcoord": (
        "// gl_FragCoord: pixels from the bottom left, depth as OptiFine has it.\n"
        "fn hs_fragcoord(input: PostInput) -> vec4<f32> {\n"
        "    return vec4<f32>(input.pixel.x, frame.resolution.y - input.pixel.y, 1.0 - input.depth, 1.0);\n}"
    ),
}


def translate_fragment(text: str, env: Env) -> dict:
    """Translates a preprocessed fragment program to a `post` stage. Returns {"wgsl", "notes", "samplers",
    "uniforms"}; raises Unsupported (with the reason) or GlslSyntaxError."""
    decls, funcs = Parser(text).unit()
    tr = Translator(decls, funcs, env)
    # `gl_TexCoord[0]` is the vertex shader's texture coordinate.
    out = tr.translate()
    parts = ["// Converted by shaderpack_convert.py from a fragment program; see README.md for the limits."]
    for key in out["helpers"]:
        parts.append(HELPERS[key])
    parts += out["globals"]
    parts += out["functions"]
    return {"wgsl": "\n\n".join(parts) + "\n", "notes": out["notes"]}
