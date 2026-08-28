"""Shared helpers for the plotting functions."""

from __future__ import annotations
from collections.abc import Callable
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.widgets import Slider

# sampletime for raw and decimated samples.
# TODO: it's not an issue now because all overlays share the same fixed clocking but it
# breaks the layer isolation between client and server and should be fixed in the next release.
TIME_MULTIPLIER = {
    "raw": 0.4283168859,
    "decimated": 1.7132675438,
}


def load_exported_dataframe(exp_dir: Path) -> pd.DataFrame | None:
    """Load the data.pkl of an experiment exported from the client.

    The plotter only works with exported experiments: each exported experiment
    directory contains a single data.pkl file with the full DataFrame, plus
    config.json.

    :param exp_dir: experiment directory.
    :type exp_dir: Path
    :return: the exported DataFrame, or None if data.pkl is missing.
    :rtype: pd.DataFrame | None
    """
    data_path = Path(exp_dir) / "data.pkl"
    if not data_path.exists():
        print(
            f"No data.pkl in {exp_dir}: the plotter only works with experiments "
            "exported from the client (use the client's export command)."
        )
        return None
    return pd.read_pickle(data_path)


def get_var_names(dataframe: pd.DataFrame) -> list[str]:
    """Extract the swept variable names from the DataFrame index.

    The exported DataFrame is indexed by the sweep variables and time
    (shot and time for variable-less experiments).

    :param dataframe: exported experiment DataFrame.
    :type dataframe: pd.DataFrame
    :return: names of the index levels that are not time or shot.
    :rtype: list[str]
    """
    return [name for name in dataframe.index.names if name not in (None, "time", "shot")]


def make_formatter(var: str, var_values: dict[str, np.ndarray]) -> Callable[[float], str]:
    """Return a formatter showing the variable value for a slider index.

    :param var: name of the variable to format.
    :type var: str
    :param var_values: values of each swept variable.
    :type var_values: dict[str, np.ndarray]
    :return: the formatting function.
    :rtype: Callable[[float], str]
    """
    return lambda x: f"{var_values[var][int(x)]:.3f}"


def add_sliders(
    slider_vars: list[str],
    var_values: dict[str, np.ndarray],
    update: Callable[[object], None],
    bottom: float = 0.08,
) -> dict[str, Slider]:
    """Create one slider per variable and bind them to the update callback.

    :param slider_vars: names of the variables that get a slider.
    :type slider_vars: list[str]
    :param var_values: values of each swept variable.
    :type var_values: dict[str, np.ndarray]
    :param update: callback invoked when a slider moves.
    :type update: Callable[[object], None]
    :param bottom: vertical offset of the first slider.
    :type bottom: float
    :return: dict mapping each variable name to its slider.
    :rtype: dict[str, Slider]
    """
    sliders = {}
    for i, var in enumerate(slider_vars):
        sax = plt.axes([0.15, bottom + i * 0.05, 0.7, 0.03])
        sliders[var] = Slider(
            sax,
            var,
            0,
            len(var_values[var]) - 1,
            valinit=0,
            valstep=1,
            valfmt=make_formatter(var, var_values),
        )
        sliders[var].on_changed(update)
    return sliders
