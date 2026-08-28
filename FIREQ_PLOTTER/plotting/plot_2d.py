"""Interactive 2D line plot of the experiment data."""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from .common import TIME_MULTIPLIER, add_sliders, get_var_names, load_exported_dataframe


def _plot_2d(
    exp_dir: str,
    plot_magnitude: bool = True,
    plot_imag: bool = False,
    plot_real: bool = False,
    save: bool = False,
) -> None:
    """Interactive 2D plot of the experiment data.

    :param exp_dir: experiment directory.
    :type exp_dir: str
    :param plot_magnitude: plot the magnitude.
    :type plot_magnitude: bool
    :param plot_imag: plot the imaginary part.
    :type plot_imag: bool
    :param plot_real: plot the real part.
    :type plot_real: bool
    :param save: also save the figure as a_figure.png.
    :type save: bool
    """
    exp_path = Path(exp_dir)

    dataframe = load_exported_dataframe(exp_path)
    if dataframe is None:
        return

    # Load experiment configuration
    with open(exp_path / "config.json") as f:
        config = json.load(f)

    output_type = config["sys_config"]["/axisAcquisitionIP_0"]["$output_type"]

    var_names = get_var_names(dataframe)

    if output_type in ("raw", "decimated"):
        xlabel = "Time"
        slider_vars = var_names
    else:
        if not var_names:
            print(f"No sweep variables in experiment {exp_path.name}; use plot_iq for accumulated data.")
            return
        xlabel = var_names[0]
        slider_vars = var_names[1:]

    var_values = {
        name: np.linspace(
            config["variables"][name]["start"],
            config["variables"][name]["stop"],
            config["variables"][name]["num"],
        )
        for name in var_names
    }

    plt.style.use("seaborn-v0_8-darkgrid")

    fig, ax = plt.subplots(figsize=(8, 5))
    plt.subplots_adjust(bottom=0.08 * max(2, len(slider_vars) + 1))

    lines = {}

    if plot_real:
        lines["real"], = ax.plot([], [], lw=2, label="Real")

    if plot_imag:
        lines["imag"], = ax.plot([], [], "--", lw=2, label="Imag")

    if plot_magnitude:
        lines["mag"], = ax.plot([], [], ":", lw=2, label="Magnitude")

    ax.legend()
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Amplitude")
    ax.set_title(exp_path.name)

    def update(_: object = None) -> None:
        """Redraw the plot with the current slider selections.

        :param _: slider value (unused).
        :type _: object
        """
        df = dataframe

        # Apply slider selections
        for var in slider_vars:
            idx = int(sliders[var].val)
            df = df[df.index.get_level_values(var) == idx]

        if df.empty:
            return

        if output_type in ("raw", "decimated"):
            x = (
                df.index.get_level_values("time").to_numpy()
                * TIME_MULTIPLIER[output_type]
            )
            y = df["value"].to_numpy()

        else:
            # REAL sweep values instead of integer indices
            x = var_values[var_names[0]]
            y = df["value"].to_numpy()

        if "real" in lines:
            lines["real"].set_data(x, np.real(y))

        if "imag" in lines:
            lines["imag"].set_data(x, np.imag(y))

        if "mag" in lines:
            lines["mag"].set_data(x, np.abs(y))

        ax.relim()
        ax.autoscale_view()
        fig.canvas.draw_idle()

    sliders = add_sliders(slider_vars, var_values, update)

    update()
    if save:
        plt.savefig(exp_path / "a_figure.png")
    plt.show()
