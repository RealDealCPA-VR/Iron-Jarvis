"""A small arithmetic evaluator (reference solution; see the task's docstring)."""

import re


class CalcError(ValueError):
    def __init__(self, message, pos):
        super().__init__(f"{message} (at {pos})")
        self.pos = pos


_NUMBER = re.compile(r"\d+\.\d*|\.\d+|\d+")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_OPS = ("**", "//", "+", "-", "*", "/", "%", "(", ")", ",")
_FUNCS = {"min": (1, None), "max": (1, None), "abs": (1, 1)}


def _tokens(text):
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in " \t":
            i += 1
            continue
        m = _NUMBER.match(text, i)
        if m:
            raw = m.group()
            out.append(("num", float(raw) if "." in raw else int(raw), i))
            i = m.end()
            continue
        m = _NAME.match(text, i)
        if m:
            out.append(("name", m.group(), i))
            i = m.end()
            continue
        for op in _OPS:
            if text.startswith(op, i):
                out.append(("op", op, i))
                i += len(op)
                break
        else:
            raise CalcError(f"unexpected character {ch!r}", i)
    out.append(("end", None, len(text)))
    return out


class _Parser:
    def __init__(self, text, variables):
        self.toks = _tokens(text)
        self.i = 0
        self.vars = variables or {}

    def peek(self):
        return self.toks[self.i]

    def take(self):
        tok = self.toks[self.i]
        self.i += 1
        return tok

    def is_op(self, *ops):
        kind, value, _ = self.peek()
        return kind == "op" and value in ops

    def expect(self, op):
        kind, value, pos = self.peek()
        if kind == "op" and value == op:
            return self.take()
        raise CalcError(f"expected {op!r}", pos)

    def parse(self):
        value = self.expr()
        kind, _, pos = self.peek()
        if kind != "end":
            raise CalcError("unexpected token", pos)
        return value

    def expr(self):
        value = self.term()
        while self.is_op("+", "-"):
            _, op, _ = self.take()
            rhs = self.term()
            value = value + rhs if op == "+" else value - rhs
        return value

    def term(self):
        value = self.unary()
        while self.is_op("*", "/", "//", "%"):
            _, op, pos = self.take()
            rhs = self.unary()
            if op != "*" and rhs == 0:
                raise CalcError("division by zero", pos)
            if op == "*":
                value = value * rhs
            elif op == "/":
                value = value / rhs
            elif op == "//":
                value = value // rhs
            else:
                value = value % rhs
        return value

    def unary(self):
        if self.is_op("+", "-"):
            _, op, _ = self.take()
            value = self.unary()
            return value if op == "+" else -value
        return self.power()

    def power(self):
        base = self.atom()
        if self.is_op("**"):
            _, _, pos = self.take()
            exponent = self.unary()
            try:
                result = base ** exponent
            except (ZeroDivisionError, OverflowError) as exc:
                raise CalcError("bad power", pos) from exc
            if isinstance(result, complex):
                raise CalcError("not a real number", pos)
            return result
        return base

    def atom(self):
        kind, value, pos = self.take()
        if kind == "num":
            return value
        if kind == "op" and value == "(":
            inner = self.expr()
            self.expect(")")
            return inner
        if kind == "name":
            if self.is_op("("):
                self.take()
                args = []
                if not self.is_op(")"):
                    args.append(self.expr())
                    while self.is_op(","):
                        self.take()
                        args.append(self.expr())
                self.expect(")")
                if value not in _FUNCS:
                    raise CalcError(f"unknown function {value}", pos)
                low, high = _FUNCS[value]
                if len(args) < low or (high is not None and len(args) > high):
                    raise CalcError(f"wrong number of arguments for {value}", pos)
                if value == "abs":
                    return abs(args[0])
                return min(args) if value == "min" else max(args)
            if value not in self.vars:
                raise CalcError(f"unknown variable {value}", pos)
            return self.vars[value]
        raise CalcError("unexpected token", pos)


def evaluate(expression, variables=None):
    return _Parser(expression, variables).parse()
