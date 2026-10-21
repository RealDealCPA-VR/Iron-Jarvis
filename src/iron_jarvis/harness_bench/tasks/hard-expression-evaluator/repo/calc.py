"""A small arithmetic evaluator.

    evaluate(expression, variables=None) -> int | float

GRAMMAR, lowest precedence first::

    expr   := term (("+" | "-") term)*
    term   := unary (("*" | "/" | "//" | "%") unary)*
    unary  := ("+" | "-") unary | power
    power  := atom ("**" unary)?
    atom   := NUMBER | NAME | NAME "(" [expr ("," expr)*] ")" | "(" expr ")"

So "**" is right-associative (2 ** 3 ** 2 == 512) and binds tighter than a
unary minus on its LEFT (-2 ** 2 == -4) but not on its right (2 ** -1 == 0.5).
"+" and "-" (binary), "*", "/", "//" and "%" are left-associative.

NUMBER is digits with an optional fraction: "12", "3.5", ".5" or "5.". A
number written with a "." is a float, otherwise an int ("007" is the int 7).
NAME is a letter or "_" followed by letters, digits or "_". Spaces and tabs
may appear between tokens.

VALUES follow Python's own int/float arithmetic: "/" always gives a float,
"//" floors, "%" takes the sign of the divisor, "**" is Python's power.

NAMES: a NAME followed by "(" is a FUNCTION call; any other NAME is a
VARIABLE, looked up in the ``variables`` dict (None means no variables). The
functions are ``min`` and ``max`` (one or more arguments) and ``abs`` (exactly
one argument). Functions are not variables, and a variable cannot be called.

ERRORS raise ``CalcError`` (a ValueError defined in this module) whose ``pos``
attribute is the index in ``expression`` where the problem was found:

* an unexpected character, or a token where it does not belong: the index
  where that character or token starts (e.g. "1 + * 2" -> 4, "1 2" -> 2,
  "2 $ 3" -> 2, ")" -> 0);
* the expression ends too early ("1 +", "(1", "max(1," and "" too):
  ``pos == len(expression)``;
* an unknown variable or function, a variable called like a function, or a
  function given the wrong number of arguments: the index where its NAME
  starts;
* division or modulo by zero ("/", "//" or "%" with a zero right-hand side):
  the index of that operator;
* a power Python cannot compute as a real number (0 ** -1, (-8) ** 0.5): the
  index of the "**".
"""


class CalcError(ValueError):
    def __init__(self, message, pos):
        super().__init__(f"{message} (at {pos})")
        self.pos = pos


def evaluate(expression, variables=None):
    raise NotImplementedError("evaluate is not written yet")
