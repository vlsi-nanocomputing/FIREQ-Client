"""Interactive 3D heatmap of the experiment data."""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from .common import TIME_MULTIPLIER, add_sliders, get_var_names, load_exported_dataframe


def _plot_3d_heatmap(
    exp_dir: str,
    plot_magnitude: bool = True,
    plot_phase: bool = False,
    plot_real: bool = False,
    plot_imag: bool = False,
    save: bool = False,
) -> None:
    """Interactive heatmap.

    accumulated:
        x = variable 0
        y = variable 1

    raw/decimated:
        x = time
        y = variable 0

    Remaining variables become sliders.

    :param exp_dir: experiment directory.
    :type exp_dir: str
    :param plot_magnitude: plot the magnitude.
    :type plot_magnitude: bool
    :param plot_phase: plot the phase.
    :type plot_phase: bool
    :param plot_real: plot the real part.
    :type plot_real: bool
    :param plot_imag: plot the imaginary part.
    :type plot_imag: bool
    :param save: also save the figure as a_figure.png.
    :type save: bool
    """
    exp_path = Path(exp_dir)

    dataframe = load_exported_dataframe(exp_path)
    if dataframe is None:
        return

    with open(exp_path / "config.json") as f:
        config = json.load(f)

    output_type = config["sys_config"]["/axisAcquisitionIP_0"]["$output_type"]
    time_mult = TIME_MULTIPLIER.get(output_type, 1)

    var_names = get_var_names(dataframe)

    if output_type in ("raw", "decimated"):
        if not var_names:
            print(f"No sweep variables in experiment {exp_path.name}; nothing to plot.")
            return
        x_name = "time"
        y_name = var_names[0]
        slider_vars = var_names[1:]
    else:
        if len(var_names) < 2:
            print(f"Accumulated experiment {exp_path.name} needs at least two sweep variables.")
            return
        x_name = var_names[0]
        y_name = var_names[1]
        slider_vars = var_names[2:]

    var_values = {
        name: np.linspace(
            config["variables"][name]["start"],
            config["variables"][name]["stop"],
            config["variables"][name]["num"],
        )
        for name in var_names
    }

    mode = (
        "magnitude"
        if plot_magnitude
        else "phase"
        if plot_phase
        else "real"
        if plot_real
        else "imag"
    )

    plt.style.use("seaborn-v0_8-darkgrid")

    fig, ax = plt.subplots(figsize=(8, 6))
    plt.subplots_adjust(bottom=0.08 * max(1, len(slider_vars) + 1))

    img = ax.imshow(
        np.zeros((2, 2)),
        origin="lower",
        aspect="auto",
        interpolation="nearest",
    )

    cbar = plt.colorbar(img, ax=ax)
    cbar.set_label(mode)

    def update(_: object = None) -> None:
        """Redraw the heatmap with the current slider selections.

        :param _: slider value (unused).
        :type _: object
        """
        df = dataframe

        for var in slider_vars:
            idx = int(sliders[var].val)
            df = df[df.index.get_level_values(var) == idx]

        if df.empty:
            return

        if output_type in ("raw", "decimated"):
            heat = (
                df["value"]
                .unstack("time")
                .reindex(range(len(var_values[y_name])))
            )

            x = heat.columns.to_numpy() * time_mult
            y = var_values[y_name]

        else:
            heat = (
                df["value"]
                .droplevel("time")
                .unstack(y_name)
                .reindex(range(len(var_values[x_name])))
            )

            heat = heat.T

            x = var_values[x_name]
            y = var_values[y_name]

        data = heat.to_numpy()

        if mode == "magnitude":
            data = np.abs(data)
            cmap = "viridis"

        elif mode == "phase":
            data = np.angle(data)
            cmap = "twilight"

        elif mode == "real":
            data = np.real(data)
            cmap = "RdBu_r"

        else:
            data = np.imag(data)
            cmap = "RdBu_r"

        img.set_data(data)
        img.set_cmap(cmap)
        img.set_extent([x[0], x[-1], y[0], y[-1]])

        img.set_clim(np.nanmin(data), np.nanmax(data))

        ax.set_xlabel(x_name)
        ax.set_ylabel(y_name)

        fig.canvas.draw_idle()

    sliders = add_sliders(slider_vars, var_values, update)

    update()
    if save:
        plt.savefig(exp_path / "a_figure.png")
    plt.show()
