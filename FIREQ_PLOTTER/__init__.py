"""Plotting of FIREQ experiment data: scriptable functions, with the interactive prompt in :mod:`FIREQ_PLOTTER.cli`."""

import logging

from .api import plot_2d, plot_3d_heatmap, plot_iq, rotated_cdf
from .data import load_experiment
from .plotting.common import PlotHandles
from .plotting.fidelity import ReadoutFidelity
from .plotting.plot_iq import CdfResult

logging.getLogger(__name__).addHandler(logging.NullHandler())


def start_interactive() -> None:
    """Run the interactive plotter prompt."""
    from .cli import main  # noqa: PLC0415 - prompt_toolkit is only needed by the prompt

    main()


__all__ = [
    "CdfResult",
    "PlotHandles",
    "ReadoutFidelity",
    "load_experiment",
    "plot_2d",
    "plot_3d_heatmap",
    "plot_iq",
    "rotated_cdf",
    "start_interactive",
]
