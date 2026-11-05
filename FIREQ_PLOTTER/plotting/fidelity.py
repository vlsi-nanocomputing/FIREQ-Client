"""Single-shot readout fidelity of two prepared states."""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
from numpy.typing import ArrayLike


class ReadoutFidelity(NamedTuple):
    """The result of a single-shot readout fidelity analysis.

    The shots are classified with a threshold on the rotated real axis: a shot
    at or below the threshold is read as state 0, a shot above it as state 1.
    """

    #: 1 - (eps_0 + eps_1) / 2, as a fraction (0.5 to 1).
    assignment_fidelity: float
    #: 1 - eps_0 - eps_1, as a fraction (0 to 1).
    separation: float
    #: P(read 1 | prepared 0).
    eps_0: float
    #: P(read 0 | prepared 1).
    eps_1: float
    #: threshold on the rotated real axis.
    threshold: float
    #: angle the clouds were rotated by, about the midpoint of their means, in radians.
    rotation_angle_rad: float
    #: number of shots of state 0.
    n_0: int
    #: number of shots of state 1.
    n_1: int


def rotate_to_real_axis(iq_0: ArrayLike, iq_1: ArrayLike) -> tuple[np.ndarray, np.ndarray, float]:
    """Rotate two IQ clouds so that the line joining their means is horizontal.

    The rotation is about the midpoint of the two means, and puts the mean of
    state 1 on the right of the mean of state 0. When the two means coincide
    the angle is 0 and the clouds are returned unchanged.

    :param iq_0: complex shots of state 0.
    :type iq_0: ArrayLike
    :param iq_1: complex shots of state 1.
    :type iq_1: ArrayLike
    :raises ValueError: if either dataset is empty.
    :return: the rotated shots of state 0, those of state 1, and the rotation angle in radians.
    :rtype: tuple[np.ndarray, np.ndarray, float]
    """
    values_0 = np.asarray(iq_0, dtype=complex).ravel()
    values_1 = np.asarray(iq_1, dtype=complex).ravel()
    if values_0.size == 0 or values_1.size == 0:
        raise ValueError("Both datasets need at least one shot")

    mean_0 = values_0.mean()
    mean_1 = values_1.mean()
    mid_point = (mean_0 + mean_1) / 2
    angle = float(np.angle(mean_1 - mean_0)) if mean_1 != mean_0 else 0.0

    rotation = np.exp(-1j * angle)
    return (values_0 - mid_point) * rotation + mid_point, (values_1 - mid_point) * rotation + mid_point, angle


def compute_readout_fidelity(iq_0: ArrayLike, iq_1: ArrayLike) -> ReadoutFidelity:
    """Compute the single-shot readout fidelity of two prepared states.

    The clouds are rotated with :func:`rotate_to_real_axis`. The threshold is
    the midpoint between consecutive distinct real parts of the pooled shots
    that maximises ``CDF_0 - CDF_1 = 1 - eps_0 - eps_1``. Among equal maxima,
    the threshold closest to the midpoint of the means is taken. When every
    shot has the same real part, that value is the threshold.

    :param iq_0: complex shots prepared in state 0.
    :type iq_0: ArrayLike
    :param iq_1: complex shots prepared in state 1.
    :type iq_1: ArrayLike
    :raises ValueError: if either dataset is empty.
    :return: both fidelities, the error rates, the threshold, the rotation angle and the shot counts.
    :rtype: ReadoutFidelity
    """
    rotated_0, rotated_1, angle = rotate_to_real_axis(iq_0, iq_1)
    x_0 = np.sort(rotated_0.real)
    x_1 = np.sort(rotated_1.real)
    n_0 = x_0.size
    n_1 = x_1.size

    pooled = np.unique(np.concatenate([x_0, x_1]))
    candidates = (pooled[:-1] + pooled[1:]) / 2 if pooled.size > 1 else pooled

    # shots read as 0 at each candidate, i.e. at or below it
    below_0 = np.searchsorted(x_0, candidates, side="right")
    below_1 = np.searchsorted(x_1, candidates, side="right")

    # n_0 * n_1 * (CDF_0 - CDF_1), kept in integers so the comparison is exact
    objective = below_0.astype(np.int64) * n_1 - below_1.astype(np.int64) * n_0
    best_candidates = np.flatnonzero(objective == objective.max())
    centre = (rotated_0.mean().real + rotated_1.mean().real) / 2
    best = best_candidates[np.argmin(np.abs(candidates[best_candidates] - centre))]

    eps_0 = float(n_0 - below_0[best]) / n_0
    eps_1 = float(below_1[best]) / n_1
    return ReadoutFidelity(
        assignment_fidelity=1.0 - (eps_0 + eps_1) / 2,
        separation=1.0 - eps_0 - eps_1,
        eps_0=eps_0,
        eps_1=eps_1,
        threshold=float(candidates[best]),
        rotation_angle_rad=angle,
        n_0=int(n_0),
        n_1=int(n_1),
    )
