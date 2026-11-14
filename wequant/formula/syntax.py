"""TDX-style formula text read into statements: tokens with their place in the text, then a tree of each line (S4.1).

A formula is statements separated by semicolons: NAME := expr (a variable), NAME : expr (an output line, drawing
styles after a comma ignored) or a bare expr. {braces} are comments; case does not matter; full-width punctuation
reads as its ASCII twin.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

WIDE = str.maketrans("（）；，：＝＜＞＋－＊／｛｝＇＂", "();,:=<>+-*/{}'\"")
TOKEN = re.compile(
    r"(?P<space>\s+)|(?P<comment>\{[^}]*\})|(?P<open_comment>\{)"
    r"|(?P<number>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)"
    r"|(?P<name>[A-Za-z_一-鿿][A-Za-z0-9_一-鿿]*)"
    r"|(?P<string>'[^'\n]*'|\"[^\"\n]*\")"
    r"|(?P<op>:=|>=|<=|<>|!=|&&|\|\||[-+*/(),;:=<>])"
)
WORDS = {"AND": "AND", "OR": "OR", "NOT": "NOT", "&&": "AND", "||": "OR", "!=": "<>"}
BINARY = {"OR": 1, "AND": 2, "=": 3, "<>": 3, ">": 3, "<": 3, ">=": 3, "<=": 3, "+": 4, "-": 4, "*": 5, "/": 5}
PREFIX = 6  # NOT and the minus sign bind tighter than any binary operator
STYLE = re.compile(
    r"(COLOR\w*|LINETHICK\d*|NODRAW|NOTEXT|NOFRAME|NOTITLE|DOTLINE|CIRCLEDOT|CROSSDOT|POINTDOT|STICK|VOLSTICK|"
    r"COLORSTICK|LINESTICK|DRAWABOVE|NOTMIXED|NOKLINE|LAYER\d*|ALIGN\d*|VALIGN\d*|MOVE\d*|DRAWNULL)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Span:
    """Where text sits in the formula: 1-based lines and columns, the end column just after its last character."""

    line: int
    col: int
    end_line: int
    end_col: int

    def to(self, other: Span) -> Span:
        return Span(self.line, self.col, other.end_line, other.end_col)


@dataclass(frozen=True)
class Token:
    kind: str  # number, name, string, op, end
    text: str  # names upper-cased, operators in their canonical spelling
    span: Span


class FormulaError(ValueError):
    """Text a formula cannot be: a stable code, an English message, where it is and the token at fault."""

    def __init__(self, code: str, message: str, span: Span | None = None, token: str | None = None) -> None:
        super().__init__(message)
        self.code, self.span, self.token = code, span, token

    @property
    def position(self) -> dict | None:
        """line, col, end_line, end_col (just after the last character) and token; None without a place."""
        return None if self.span is None else {**vars(self.span), "token": self.token}


@dataclass(frozen=True)
class Num:
    value: float
    span: Span


@dataclass(frozen=True)
class Name:
    name: str
    span: Span


@dataclass(frozen=True)
class Text:
    value: str
    span: Span


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple
    span: Span  # the name and its parentheses
    name_span: Span


@dataclass(frozen=True)
class Unary:
    op: str  # NOT or -
    operand: object
    span: Span


@dataclass(frozen=True)
class Binary:
    op: str
    left: object
    right: object
    span: Span  # the operator
    whole: Span


@dataclass(frozen=True)
class Statement:
    """One line of the formula: kind is "variable" (:=), "output" (:) or "bare"; name is None for a bare line."""

    kind: str
    name: str | None
    expr: object
    span: Span  # the name, or the expression of a bare line


def tokens(text: str) -> list[Token]:
    """The formula's tokens, then an end token; FormulaError formula_syntax at a character no token starts with."""
    text, found, at, line, start = text.translate(WIDE), [], 0, 1, 0
    while at < len(text):
        match = TOKEN.match(text, at)
        if match is None or match.lastgroup == "open_comment":
            span = _span(text, at, at + 1, line, start)
            if match is not None:
                raise FormulaError("formula_syntax", "a comment opened with { is never closed by }", span, "{")
            raise FormulaError("formula_syntax", f"unexpected character {text[at]!r}", span, text[at])
        kind, end = match.lastgroup, match.end()
        if kind not in ("space", "comment"):
            raw = match.group()
            word = raw.upper() if kind == "name" else raw
            found.append(Token(kind, WORDS.get(word, word), _span(text, at, end, line, start)))
        for newline in re.finditer("\n", text[at:end]):
            line, start = line + 1, at + newline.end()
        at = end
    found.append(Token("end", "", _span(text, at, at, line, start)))
    return found


def _span(text: str, begin: int, end: int, line: int, start: int) -> Span:
    """The span of text[begin:end], begin on line whose first character is at start."""
    lines = text.count("\n", begin, end)
    last_start = text.rfind("\n", begin, end) + 1 if lines else start
    return Span(line, begin - start + 1, line + lines, end - last_start + 1)


class Parser:
    """A Pratt parser over the formula's tokens."""

    def __init__(self, text: str) -> None:
        self.tokens, self.at = tokens(text), 0

    def statements(self) -> list[Statement]:
        found = []
        while self.peek().kind != "end":
            found.append(self.statement())
            if self.peek().text == ";":
                self.next()
            elif self.peek().kind != "end":
                token = self.peek()
                raise FormulaError("formula_syntax", f"expected ; before {token.text!r}", token.span, token.text)
        return found

    def statement(self) -> Statement:
        first, second = self.peek(), self.peek(1)
        if first.kind == "name" and second.text in (":=", ":"):
            self.at += 2
            kind = "variable" if second.text == ":=" else "output"
            expr = self.expression(0)
            if kind == "output":
                self.styles()
            return Statement(kind, first.text, expr, first.span)
        expr = self.expression(0)
        self.styles()
        return Statement("bare", None, expr, _whole(expr))

    def styles(self) -> None:
        """Skip the drawing styles after an output: , COLORRED, LINETHICK2, NODRAW and the like."""
        while self.peek().text == ",":
            self.next()
            token = self.next()
            if token.kind != "name" or not STYLE.fullmatch(token.text):
                raise FormulaError("formula_syntax", f"{token.text!r} is not a drawing style", token.span, token.text)

    def expression(self, floor: int) -> object:
        left = self.prefix()
        while (token := self.peek()).kind == "op" or token.text in ("AND", "OR"):
            power = BINARY.get(token.text)
            if power is None or power <= floor:
                break
            self.next()
            right = self.expression(power)
            left = Binary(token.text, left, right, token.span, _whole(left).to(_whole(right)))
        return left

    def prefix(self) -> object:
        token = self.next()
        if token.text in ("NOT", "-", "+"):
            operand = self.expression(PREFIX)
            return operand if token.text == "+" else Unary(token.text, operand, token.span.to(_whole(operand)))
        if token.kind == "number":
            return Num(float(token.text), token.span)
        if token.kind == "string":
            return Text(token.text[1:-1], token.span)
        if token.kind == "name" and self.peek().text == "(":
            return self.call(token)
        if token.kind == "name":
            return Name(token.text, token.span)
        if token.text == "(":
            inner = self.expression(0)
            self.expect(")", token)
            return inner
        if token.kind == "end":
            raise FormulaError("formula_syntax", "the formula ends where an expression should follow", token.span)
        raise FormulaError("formula_syntax", f"unexpected {token.text!r}", token.span, token.text)

    def call(self, name: Token) -> Call:
        opening, args = self.next(), []
        if self.peek().text != ")":
            args.append(self.expression(0))
            while self.peek().text == ",":
                self.next()
                args.append(self.expression(0))
        closing = self.expect(")", opening)
        return Call(name.text, tuple(args), name.span.to(closing.span), name.span)

    def expect(self, text: str, opening: Token) -> Token:
        token = self.next()
        if token.text != text:
            message = (
                f"expected {text!r} to close the {opening.text!r} at line {opening.span.line}, col {opening.span.col}"
            )
            raise FormulaError("formula_syntax", message, token.span, token.text or None)
        return token

    def peek(self, ahead: int = 0) -> Token:
        return self.tokens[min(self.at + ahead, len(self.tokens) - 1)]

    def next(self) -> Token:
        token = self.peek()
        self.at = min(self.at + 1, len(self.tokens) - 1)
        return token


def parse(text: str) -> list[Statement]:
    """The formula's statements; FormulaError formula_syntax where the text breaks the grammar."""
    return Parser(text).statements()


def _whole(node: object) -> Span:
    """The span of a whole expression."""
    return node.whole if isinstance(node, Binary) else node.span
