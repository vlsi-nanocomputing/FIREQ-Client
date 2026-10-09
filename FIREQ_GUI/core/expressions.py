"""Parameter values and expressions.

A parameter value in the GUI is either a number or a string following the FIREQ YAML
prefix rules:

* ``"%name"``   preprocess macro, resolved client-side (whole value only);
* ``"#expr"``   sweep expression, evaluated server-side with Python ``eval`` where the
                sweep variable names are bare identifiers (``"#tau + 200"``).

This module parses user text, evaluates values for previews and builds *linear*
expressions so that absolute pulse start times can be turned into the relative
trigger delays required by the trigger generator.
"""

from __future__ import annotations

import ast
import math
import operator
import re
from dataclasses import dataclass, field

# a parameter is a number, a "%macro" (preprocess) or a "#expression" (sweep, evaluated by the server)
Param = int | float | str

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_identifier(name: str) -> bool:
    """Return True if name is a valid macro / variable / pulse name.

    :param name: candidate name.
    :type name: str
    :return: True if the name is a valid Python identifier.
    :rtype: bool
    """
    return bool(_NAME_RE.match(name or ""))


def parse_param(text: str | int | float | None) -> Param:
    """Parse text typed by the user into a number or a prefixed string.

    :param text: text typed by the user (or an already parsed number)
    :type text: str | int | float | None
    :return: an int/float, a ``%macro`` string or a ``#expression`` string (bare expressions get the ``#`` prefix)
    :rtype: Param
    """
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return text
    s = ("" if text is None else str(text)).strip()
    if s == "":
        return 0
    # numbers first: int, then float (keeps 100 as int and 1e3 as float)
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    # already prefixed: macro or sweep expression
    if s[0] in "%#":
        return s
    # bare expression containing variable names: make it a sweep expression
    return "#" + s


def format_param(value: Param) -> str:
    """Return the text shown in an editor for a value.

    :param value: parameter value.
    :type value: Param
    :return: the text to show in an editor.
    :rtype: str
    """
    if isinstance(value, float):
        # keep all digits of non-integral floats; show integral floats as "100.0"
        return repr(value) if value != int(value) or abs(value) >= 1e16 else f"{value:.1f}"
    return str(value)


def param_kind(value: Param) -> str:
    """Return 'number', 'macro' or 'sweep'.

    :param value: parameter value.
    :type value: Param
    :return: ``number``, ``macro`` or ``sweep``.
    :rtype: str
    """
    if isinstance(value, str):
        return "macro" if value.startswith("%") else "sweep"
    return "number"


def referenced_names(value: Param) -> tuple[set[str], set[str]]:
    """Return (macros, variables) referenced by a value.

    :param value: parameter value.
    :type value: Param
    :return: the macro names and the variable names used by the value.
    :rtype: tuple[set[str], set[str]]
    """
    if not isinstance(value, str):
        return set(), set()
    if value.startswith("%"):
        return {value[1:]}, set()
    if value.startswith("#"):
        try:
            tree = ast.parse(value[1:], mode="eval")
        except SyntaxError:
            return set(), set()
        # every bare name of the expression, except the allowed functions/constants
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        return set(), names - set(_SAFE_FUNCS)
    return set(), set()


# --------------------------------------------------------------------------- evaluation
# binary operators accepted by the preview evaluator
_BIN = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}
# functions and constants accepted in expressions (the server evaluates with Python eval)
_SAFE_FUNCS = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "int": int,
    "float": float,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "exp": math.exp,
    "pi": math.pi,
}


def _eval_node(node: ast.AST, names: dict[str, float]) -> float:
    """Evaluate a restricted Python AST (numbers, names, + - * / ** %, a few math functions).

    :param node: AST node to evaluate.
    :type node: ast.AST
    :param names: value of each name that may appear.
    :type names: dict[str, float]
    :return: the numeric value.
    :rtype: float
    :raises NameError: if a name is unknown.
    :raises ValueError: if the expression uses an unsupported construct.
    """
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, names)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in names:
            return names[node.id]
        # constants such as pi
        if node.id in _SAFE_FUNCS and not callable(_SAFE_FUNCS[node.id]):
            return _SAFE_FUNCS[node.id]
        raise NameError(node.id)
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        return _BIN[type(node.op)](_eval_node(node.left, names), _eval_node(node.right, names))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _eval_node(node.operand, names)
        return -v if isinstance(node.op, ast.USub) else v
    # calls of whitelisted functions only (no attributes, no keyword arguments)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and callable(_SAFE_FUNCS.get(node.func.id)):
        return _SAFE_FUNCS[node.func.id](*[_eval_node(a, names) for a in node.args])
    raise ValueError(f"unsupported expression element: {ast.dump(node)}")


def evaluate(value: Param, preprocess: dict[str, Param], variables: dict[str, float]) -> float | None:
    """Evaluate a value numerically for previews; return None if it cannot be resolved.

    :param value: number, ``%macro`` or ``#expression``.
    :type value: Param
    :param preprocess: preprocess macros (name -> value).
    :type preprocess: dict[str, Param]
    :param variables: value of each sweep variable at the evaluation point.
    :type variables: dict[str, float]
    :return: the numeric value, or None if the value cannot be resolved.
    :rtype: float | None
    """
    # any failure (unknown macro, bad syntax, division by zero...) just means "no preview"
    try:
        return _evaluate(value, preprocess, variables, depth=0)
    except Exception:  # noqa: BLE001 - preview only
        return None


def _evaluate(value: Param, preprocess: dict[str, Param], variables: dict[str, float], depth: int) -> float:
    """Evaluate a value, resolving macros recursively.

    :param value: number, ``%macro`` or ``#expression``.
    :type value: Param
    :param preprocess: preprocess macros (name -> value).
    :type preprocess: dict[str, Param]
    :param variables: value of each sweep variable at the evaluation point.
    :type variables: dict[str, float]
    :param depth: current macro nesting depth (guards against loops)
    :type depth: int
    :return: the numeric value.
    :rtype: float
    :raises RecursionError: if macros reference each other in a loop.
    :raises KeyError: if a macro is not defined.
    """
    # a macro may point to another macro; stop on loops
    if depth > 8:
        raise RecursionError("macro recursion")
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise TypeError(value)
    if value.startswith("%"):
        return _evaluate(preprocess[value[1:]], preprocess, variables, depth + 1)
    # sweep expression: variables are bare names (as in the server's eval)
    expr = value[1:] if value.startswith("#") else value
    return float(_eval_node(ast.parse(expr, mode="eval"), variables))


# --------------------------------------------------------------------------- linear expressions
# Start times are absolute in the GUI, while the trigger FIFO needs relative delays. Representing
# times as linear combinations of the sweep variables lets us subtract them symbolically and write
# compact expressions such as "#tau + 200" instead of "#(2*tau + 500) - (tau + 300)".
@dataclass
class Lin:
    """Linear combination ``const + sum(coef * var)`` of sweep variables.

    :param const: constant term.
    :type const: float
    :param terms: coefficient of each sweep variable.
    :type terms: dict[str, float]
    """

    const: float = 0.0
    terms: dict[str, float] = field(default_factory=dict)

    def __add__(self, other: Lin) -> Lin:
        """Add two linear expressions.

        :param other: expression to add.
        :type other: Lin
        :return: the sum.
        :rtype: Lin
        """
        terms = dict(self.terms)
        for k, v in other.terms.items():
            terms[k] = terms.get(k, 0.0) + v
        # drop the variables whose coefficient cancels out
        return Lin(self.const + other.const, {k: v for k, v in terms.items() if v != 0})

    def __neg__(self) -> Lin:
        """Negate the expression.

        :return: the opposite expression.
        :rtype: Lin
        """
        return Lin(-self.const, {k: -v for k, v in self.terms.items()})

    def __sub__(self, other: Lin) -> Lin:
        """Subtract two linear expressions.

        :param other: expression to subtract.
        :type other: Lin
        :return: the difference.
        :rtype: Lin
        """
        return self + (-other)

    def scale(self, k: float) -> Lin:
        """Multiply by a constant.

        :param k: constant factor.
        :type k: float
        :return: the scaled expression.
        :rtype: Lin
        """
        return Lin(self.const * k, {n: v * k for n, v in self.terms.items() if v * k != 0})

    @property
    def is_const(self) -> bool:
        """Return True if no sweep variable is involved.

        :return: True if no sweep variable is involved.
        :rtype: bool
        """
        return not self.terms

    def value(self, variables: dict[str, float]) -> float:
        """Evaluate numerically.

        :param variables: value of each sweep variable.
        :type variables: dict[str, float]
        :return: the numeric value.
        :rtype: float
        """
        return self.const + sum(c * variables.get(n, 0.0) for n, c in self.terms.items())

    def to_param(self) -> Param:
        """Render as a YAML value: a number, or a ``#`` sweep expression.

        :return: a number if constant, otherwise a ``#`` expression such as ``#tau + 200``.
        :rtype: Param
        """
        if self.is_const:
            return _clean_number(self.const)
        # one term per variable ("tau", "-tau", "2*tau"), then the constant
        parts: list[str] = []
        for name, coef in self.terms.items():
            c = _clean_number(coef)
            if c == 1:
                term = name
            elif c == -1:
                term = f"-{name}"
            else:
                term = f"{c}*{name}"
            parts.append(term)
        if self.const:
            parts.append(str(_clean_number(self.const)))
        # join with " + " / " - " (a leading "-" becomes a subtraction)
        text = parts[0]
        for p in parts[1:]:
            text += f" - {p[1:]}" if p.startswith("-") else f" + {p}"
        return "#" + text


def _clean_number(x: float) -> int | float:
    """Round to 9 decimals and return an int when the value is integral.

    :param x: number to clean.
    :type x: float
    :return: the cleaned number.
    :rtype: int | float
    """
    # remove float noise such as 199.99999999997 coming from tick arithmetic
    r = round(x, 9)
    return int(r) if float(r).is_integer() else r


def _lin_node(node: ast.AST, consts: dict[str, float], var_names: set[str]) -> Lin:
    """Convert an AST into a linear expression of the sweep variables.

    :param node: AST node.
    :type node: ast.AST
    :param consts: numeric macros that may appear as bare names.
    :type consts: dict[str, float]
    :param var_names: names of the sweep variables.
    :type var_names: set[str]
    :return: the linear expression.
    :rtype: Lin
    :raises ValueError: if the expression is not linear.
    :raises NameError: if a name is neither a variable nor a macro.
    """
    if isinstance(node, ast.Expression):
        return _lin_node(node.body, consts, var_names)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return Lin(float(node.value))
    if isinstance(node, ast.Name):
        if node.id in var_names:
            return Lin(0.0, {node.id: 1.0})
        # numeric macros may appear by name inside expressions: inline their value
        if node.id in consts:
            return Lin(float(consts[node.id]))
        raise NameError(node.id)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _lin_node(node.operand, consts, var_names)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        a = _lin_node(node.left, consts, var_names)
        b = _lin_node(node.right, consts, var_names)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        # products and divisions stay linear only when one side is a constant
        if isinstance(node.op, ast.Mult):
            if a.is_const:
                return b.scale(a.const)
            if b.is_const:
                return a.scale(b.const)
        if isinstance(node.op, ast.Div) and b.is_const and b.const != 0:
            return a.scale(1.0 / b.const)
    raise ValueError("non-linear expression")


def to_lin(value: Param, preprocess: dict[str, Param], var_names: set[str], depth: int = 0) -> Lin | None:
    """Convert a value into a linear expression of sweep variables (None if non-linear).

    :param value: number, ``%macro`` or ``#expression``.
    :type value: Param
    :param preprocess: preprocess macros (name -> value).
    :type preprocess: dict[str, Param]
    :param var_names: names of the sweep variables.
    :type var_names: set[str]
    :param depth: current macro nesting depth.
    :type depth: int
    :return: the linear expression, or None if the value is not linear.
    :rtype: Lin | None
    """
    try:
        if isinstance(value, bool):
            return Lin(float(value))
        if isinstance(value, (int, float)):
            return Lin(float(value))
        if isinstance(value, str) and value.startswith("%"):
            if depth > 8:
                return None
            return to_lin(preprocess[value[1:]], preprocess, var_names, depth + 1)
        if isinstance(value, str):
            expr = value[1:] if value.startswith("#") else value
            # only numeric macros can be inlined in a linear expression
            numeric_macros = {}
            for k, v in preprocess.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    numeric_macros[k] = float(v)
            return _lin_node(ast.parse(expr, mode="eval"), numeric_macros, var_names)
    except Exception:  # noqa: BLE001
        # non-linear, unknown name or syntax error: the caller falls back to opaque arithmetic
        return None
    return None


def inline_expr(value: Param, preprocess: dict[str, Param]) -> str:
    """Return a Python expression (without '#') with macros inlined, for opaque arithmetic.

    :param value: number, ``%macro`` or ``#expression``.
    :type value: Param
    :param preprocess: preprocess macros (name -> value).
    :type preprocess: dict[str, Param]
    :return: a Python expression without the leading ``#``.
    :rtype: str
    """
    if isinstance(value, (int, float)):
        return repr(_clean_number(float(value)))
    if value.startswith("%"):
        return inline_expr(preprocess.get(value[1:], 0), preprocess)
    return value[1:] if value.startswith("#") else value


def subtract(a: Param, b: Param, preprocess: dict[str, Param], var_names: set[str]) -> Param:
    """Return ``a - b`` as a YAML value, simplified when both are linear.

    :param a: minuend.
    :type a: Param
    :param b: subtrahend.
    :type b: Param
    :param preprocess: preprocess macros (name -> value).
    :type preprocess: dict[str, Param]
    :param var_names: names of the sweep variables.
    :type var_names: set[str]
    :return: ``a - b`` as a number or a ``#`` expression.
    :rtype: Param
    """
    la, lb = to_lin(a, preprocess, var_names), to_lin(b, preprocess, var_names)
    if la is not None and lb is not None:
        return (la - lb).to_param()
    # non-linear operands: let the server compute the difference (it evaluates with Python eval)
    return f"#({inline_expr(a, preprocess)}) - ({inline_expr(b, preprocess)})"


def add(a: Param, b: Param, preprocess: dict[str, Param], var_names: set[str]) -> Param:
    """Return ``a + b`` as a YAML value, simplified when both are linear.

    :param a: first term.
    :type a: Param
    :param b: second term.
    :type b: Param
    :param preprocess: preprocess macros (name -> value).
    :type preprocess: dict[str, Param]
    :param var_names: names of the sweep variables.
    :type var_names: set[str]
    :return: ``a + b`` as a number or a ``#`` expression.
    :rtype: Param
    """
    la, lb = to_lin(a, preprocess, var_names), to_lin(b, preprocess, var_names)
    if la is not None and lb is not None:
        return (la + lb).to_param()
    # non-linear operands: let the server compute the sum
    return f"#({inline_expr(a, preprocess)}) + ({inline_expr(b, preprocess)})"
