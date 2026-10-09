"""Pulse envelope shapes.

Every shape returns ``n`` complex samples normalised to ``max|I|,|Q| <= 1`` over the
normalised time axis ``t in [0, 1]``. The pulse ``$gain`` scales the envelope on the
hardware, so envelopes are always full-scale here.
"""

from __future__ import annotations

import ast
import math

import numpy as np

# key used in the model/YAML -> label shown in the GUI
SHAPES: dict[str, str] = {
    "rect": "Rectangular",
    "gaussian": "Gaussian",
    "drag": "DRAG",
    "cosine": "Raised cosine",
    "flattop": "Flat-top",
    "sine": "Half sine",
    "triangle": "Triangle",
    "custom": "Custom expression",
}

# parameters of each shape (the inspector shows only these fields)
SHAPE_PARAMS: dict[str, tuple[str, ...]] = {
    "rect": (),
    "gaussian": ("sigma",),
    "drag": ("sigma", "beta"),
    "cosine": (),
    "flattop": ("sigma", "rise"),
    "sine": (),
    "triangle": (),
    "custom": ("expr",),
}

# parity of (I, Q) in time, used for _is_symmetric envelopes (_i_even, _q_even); DRAG has an odd Q
SYMMETRY: dict[str, tuple[bool, bool] | None] = {
    "gaussian": (True, True),
    "cosine": (True, True),
    "flattop": (True, True),
    "sine": (True, True),
    "triangle": (True, True),
    "drag": (True, False),
}


def _gauss(t: np.ndarray, center: float, sigma: float) -> np.ndarray:
    """Return a unit-height Gaussian.

    :param t: normalised time axis.
    :type t: np.ndarray
    :param center: centre of the Gaussian on the same axis.
    :type center: float
    :param sigma: standard deviation on the same axis.
    :type sigma: float
    :return: the Gaussian evaluated on ``t``.
    :rtype: np.ndarray
    """
    return np.exp(-0.5 * ((t - center) / max(sigma, 1e-6)) ** 2)


def envelope(
    shape: str,
    n: int = 64,
    sigma: float = 0.2,
    beta: float = 0.0,
    rise: float = 0.2,
    expr: str = "",
) -> np.ndarray:
    """Compute complex envelope samples.

    :param shape: one of :data:`SHAPES`.
    :type shape: str
    :param n: number of samples.
    :type n: int
    :param sigma: gaussian width as a fraction of the pulse duration.
    :type sigma: float
    :param beta: DRAG coefficient (Q = -beta * dI/dt, t normalised)
    :type beta: float
    :param rise: rise/fall length as a fraction of the duration (flat-top)
    :type rise: float
    :param expr: numpy expression of ``t`` (0..1) for custom shapes; may be complex.
    :type expr: str
    :return: complex samples normalised so that max(|I|, |Q|) = 1.
    :rtype: np.ndarray
    :raises ValueError: if the shape is unknown.
    """
    n = max(int(n), 2)
    # sample centres on [0, 1]: the first and last samples are not exactly 0 and 1
    t = (np.arange(n) + 0.5) / n
    if shape == "rect":
        env = np.ones(n, dtype=complex)
    elif shape == "gaussian":
        g = _gauss(t, 0.5, sigma)
        # remove the pedestal so that the pulse starts and ends at zero
        env = (g - g.min()) / max(1e-12, (1 - g.min())) + 0j
    elif shape == "drag":
        g = _gauss(t, 0.5, sigma)
        # analytic derivative of the Gaussian; Q = -beta * dI/dt (per sample)
        dg = -(t - 0.5) / max(sigma, 1e-6) ** 2 * g
        env = g + 1j * (-beta * dg / n)
    elif shape == "cosine":
        env = 0.5 * (1 - np.cos(2 * np.pi * t)) + 0j
    elif shape == "flattop":
        # flat top with Gaussian edges of length ``rise`` on each side
        r = min(max(rise, 1e-3), 0.5)
        env = np.ones(n)
        left = t < r
        right = t > 1 - r
        env[left] = _gauss(t[left], r, sigma * r)
        env[right] = _gauss(t[right], 1 - r, sigma * r)
        env = env + 0j
    elif shape == "sine":
        env = np.sin(np.pi * t) + 0j
    elif shape == "triangle":
        env = 1 - np.abs(2 * t - 1) + 0j
    elif shape == "custom":
        env = custom_envelope(expr, t)
    else:
        raise ValueError(f"unknown shape {shape!r}")
    # full-scale normalisation: the amplitude is set by the pulse $gain on the hardware
    peak = max(np.max(np.abs(env.real)), np.max(np.abs(env.imag)), 1e-12)
    return env / peak


def custom_envelope(expr: str, t: np.ndarray) -> np.ndarray:
    """Evaluate a user expression of ``t`` with numpy functions only.

    :param expr: expression of ``t`` using numpy functions (``np.*``, ``sin``, ``exp``, ``pi``, ``j``...)
    :type expr: str
    :param t: normalised time axis.
    :type t: np.ndarray
    :return: the complex envelope.
    :rtype: np.ndarray
    :raises ValueError: if the expression uses a name or attribute that is not allowed.
    """
    if not expr.strip():
        return np.ones_like(t) + 0j
    # names the expression may use; everything else is rejected before evaluation
    allowed = {
        "t": t,
        "np": np,
        "pi": math.pi,
        "exp": np.exp,
        "sin": np.sin,
        "cos": np.cos,
        "sqrt": np.sqrt,
        "abs": np.abs,
        "where": np.where,
        "j": 1j,
    }
    # validate the syntax tree: only allowed names and public np.<function> attributes
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in allowed:
            raise ValueError(f"name '{node.id}' not allowed in envelope expression")
        if isinstance(node, ast.Attribute):
            if not (isinstance(node.value, ast.Name) and node.value.id == "np") or node.attr.startswith("_"):
                raise ValueError("only np.<function> attributes are allowed")
    # evaluate without builtins; constants are broadcast to the length of t
    code = compile(tree, "<envelope>", "eval")
    val = eval(code, {"__builtins__": {}}, allowed)  # noqa: S307 - restricted namespace
    arr = np.broadcast_to(np.asarray(val, dtype=complex), t.shape).copy()
    return arr


def samples_to_yaml(samples: np.ndarray, fmt: str) -> list:
    """Serialise samples for the YAML ``$samples`` field.

    :param samples: complex envelope samples.
    :type samples: np.ndarray
    :param fmt: ``iq_pairs`` -> ``[[I, Q], ...]``, ``complex_str`` -> ``['I+Qj', ...]``, ``real`` -> ``[I, ...]``.
    :type fmt: str
    :return: the samples as a YAML-serialisable list.
    :rtype: list
    """
    out: list = []
    for s in samples:
        # 6 decimals are far below the 16-bit resolution of the envelope memory
        i, q = round(float(s.real), 6), round(float(s.imag), 6)
        if fmt == "complex_str":
            out.append(f"{i}{'+' if q >= 0 else '-'}{abs(q)}j")
        elif fmt == "real":
            out.append(i)
        else:
            out.append([i, q])
    return out


def samples_from_yaml(values: list) -> np.ndarray:
    """Parse ``$samples`` in any of the supported formats.

    :param values: ``$samples`` as read from a YAML file ([I, Q] pairs, complex strings, dicts or numbers)
    :type values: list
    :return: the complex samples.
    :rtype: np.ndarray
    """
    out = []
    for v in values or []:
        # accept every format written by samples_to_yaml, plus {real, imag} dicts and plain numbers
        if isinstance(v, (list, tuple)) and len(v) == 2:
            out.append(complex(float(v[0]), float(v[1])))
        elif isinstance(v, str):
            out.append(complex(v.replace(" ", "")))
        elif isinstance(v, dict):
            out.append(complex(float(v.get("real", 0)), float(v.get("imag", 0))))
        else:
            out.append(complex(v))
    return np.asarray(out, dtype=complex)
