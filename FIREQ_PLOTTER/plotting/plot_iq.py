"""IQ plane comparison plot."""

from __future__ import annotations

from collections.abc import Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.figure import Figure

from .common import (
    LAYOUT_BASE_BOTTOM,
    LAYOUT_STRIDE,
    PLOT_STYLE,
    VALUE_COLUMN,
    PlotHandles,
    SelectorSpec,
    SliderPanel,
    add_selector_window,
    average_over_level,
    common_variable_axes,
    figure_is_open,
    slice_dataframe,
    union_index_values,
)

PARAMETER_RADIO_TITLE: str = "parameter"


def _plot_iq_curve(
    dataframes: Sequence[pd.DataFrame],
    ipname: str = "default",
    dataset_names: Sequence[str] | None = None,
    average_over: str | None = "shot",
    cdf_analysis: bool | None = None,
) -> PlotHandles:
    """Plot the data of one or more experiments on an IQ plane.

    The plane holds the imaginary part of the data against the real part, and
    a selector chooses which swept variable is used as the curve parameter.
    The remaining swept variables get a slider.

    :param dataframes: dataframes to plot, one scatter series each.
    :type dataframes: Sequence[pd.DataFrame]
    :param ipname: name of the acquisition IP, displayed as the title.
    :type ipname: str
    :param dataset_names: name of the experiment directory each dataframe
        comes from, used for the legend.
    :type dataset_names: Sequence[str] | None
    :param average_over: average the values of this index level, usually
        "shot". Ignored when the level is absent.
    :type average_over: str | None
    :param cdf_analysis: run the rotated CDF analysis once the IQ figure is
        closed. When None it runs if exactly two dataframes are given.
    :type cdf_analysis: bool | None
    :raises ValueError: if there is no dataframe, no swept variable, or the
        CDF analysis is asked for with a number of dataframes other than two.
    :return: the handles of the plot.
    :rtype: PlotHandles
    """
    if not dataframes:
        raise ValueError("Need at least one dataframe to plot")

    names = (
        list(dataset_names) if dataset_names is not None else [f"dataset {index}" for index in range(len(dataframes))]
    )
    plot_dfs = [average_over_level(df, average_over) for df in dataframes]

    var_axes = common_variable_axes(plot_dfs)
    if not var_axes:
        raise ValueError("Not enough axes in data to plot an IQ curve")

    run_cdf = len(plot_dfs) == 2 if cdf_analysis is None else cdf_analysis
    if run_cdf and len(plot_dfs) != 2:
        raise ValueError("CDF analysis is only supported for exactly two dataframes")

    var_values = union_index_values(plot_dfs, var_axes)
    axis_labels = list(var_axes)

    # the state every callback reads and writes
    state = {"curve_param": var_axes[-1]}

    plt.style.use(PLOT_STYLE)
    fig, ax = plt.subplots(figsize=(7.5, 7))
    ax.set_aspect("equal", adjustable="box")

    scatters = [ax.scatter([], [], s=5, alpha=0.5, label=name) for name in names]

    def update() -> None:
        """Redraw the IQ clouds for the selected values."""
        if not figure_is_open(fig):
            return

        selection = panel.selection()
        chunks = []
        for scatter, df in zip(scatters, plot_dfs, strict=True):
            filtered = slice_dataframe(df, selection)
            iq = filtered[VALUE_COLUMN].to_numpy()
            if iq.size:
                scatter.set_offsets(np.column_stack([iq.real, iq.imag]))
                chunks.append(iq)
            else:
                scatter.set_offsets(np.empty((0, 2)))

        # keep the plane square and steady while the sliders move
        limit = 1.05 * max((float(np.abs(iq).max()) for iq in chunks), default=1.0)
        ax.set_xlim(-limit, limit)
        ax.set_ylim(-limit, limit)
        ax.set_xlabel("I (Real)")
        ax.set_ylabel("Q (Imag)")
        ax.set_title(f"{ipname}\nParameter: {state['curve_param']}")
        fig.canvas.draw_idle()

    def layout(slider_count: int) -> None:
        """Reserve room for the selector box and the sliders.

        :param slider_count: number of sliders currently displayed.
        :type slider_count: int
        """
        fig.subplots_adjust(
            left=0.1,
            right=0.97,
            top=0.9,
            bottom=LAYOUT_BASE_BOTTOM + LAYOUT_STRIDE * slider_count,
        )

    def on_parameter(label: str) -> None:
        """Use another variable as the curve parameter.

        :param label: name of the selected variable.
        :type label: str
        """
        state["curve_param"] = label
        panel.rebuild([var for var in var_axes if var != label], var_values)

    panel = SliderPanel(fig, update, on_layout_change=layout)

    control_fig, radios = add_selector_window(
        [SelectorSpec(PARAMETER_RADIO_TITLE, axis_labels, axis_labels.index(state["curve_param"]), on_parameter)],
        fig,
        title=f"{ipname} controls",
    )

    panel.rebuild([var for var in var_axes if var != state["curve_param"]], var_values)
    if len(plot_dfs) > 1:
        ax.legend(loc="best")

    plt.show()

    if run_cdf:
        _plot_rotated_cdf(plot_dfs[0], plot_dfs[1], ipname=ipname)

    return PlotHandles(fig, ax, radios, panel, control_fig)


def _plot_rotated_cdf(
    df_0: pd.DataFrame,
    df_1: pd.DataFrame,
    ipname: str = "default",
) -> Figure | None:
    """Rotate two IQ datasets and compare their cumulative distributions.

    The clouds are rotated so that the line joining their means is horizontal,
    then the cumulative distributions along that axis are compared to find the
    point of maximum separation, printed as a fidelity.

    :param df_0: first dataset.
    :type df_0: pd.DataFrame
    :param df_1: second dataset.
    :type df_1: pd.DataFrame
    :param ipname: name of the acquisition IP, displayed as the title.
    :type ipname: str
    :return: the figure, or None when the datasets are too small to compare.
    :rtype: Figure | None
    """
    iq_0 = df_0[VALUE_COLUMN].to_numpy()
    iq_1 = df_1[VALUE_COLUMN].to_numpy()

    if iq_0.size < 2 or iq_1.size < 2:
        print("CDF analysis needs at least two samples per dataset")
        return None

    mean_0 = iq_0.mean()
    mean_1 = iq_1.mean()

    mid_point = (mean_0 + mean_1) / 2
    angle = np.angle(mean_1 - mean_0)

    iq_0_rot = (iq_0 - mid_point) * np.exp(-1j * angle) + mid_point
    iq_1_rot = (iq_1 - mid_point) * np.exp(-1j * angle) + mid_point
    mean_0_rot = (mean_0 - mid_point) * np.exp(-1j * angle) + mid_point
    mean_1_rot = (mean_1 - mid_point) * np.exp(-1j * angle) + mid_point

    fig, (ax_scatter, ax_cdf) = plt.subplots(1, 2, figsize=(10, 5))

    ax_scatter.scatter(iq_0_rot.real, iq_0_rot.imag, s=5, alpha=0.5, label="Rotated 0")
    ax_scatter.scatter(iq_1_rot.real, iq_1_rot.imag, s=5, alpha=0.5, label="Rotated 1")
    ax_scatter.plot(
        [mean_0_rot.real, mean_1_rot.real],
        [mean_0_rot.imag, mean_1_rot.imag],
        color="red",
        marker="o",
        label="Means Line",
    )
    ax_scatter.set_title("Rotated IQ Plane")
    ax_scatter.set_xlabel("I (Real)")
    ax_scatter.set_ylabel("Q (Imag)")
    ax_scatter.legend()
    ax_scatter.grid(True)

    x_0 = iq_0_rot.real
    x_1 = iq_1_rot.real

    x_max = max(x_0.max(), x_1.max())
    x_min = min(x_0.min(), x_1.min())

    x_0_sorted = np.sort(x_0)
    x_1_sorted = np.sort(x_1)

    x_cdf = np.linspace(x_min, x_max, endpoint=True, num=(x_0.shape[0] + x_1.shape[0]))

    x_0_cdf = np.searchsorted(x_0_sorted, x_cdf, side="left") / x_0.shape[0]
    x_1_cdf = np.searchsorted(x_1_sorted, x_cdf, side="left") / x_1.shape[0]

    x_cdf_abs_diff = np.abs(x_0_cdf - x_1_cdf)
    threshold = np.argmax(x_cdf_abs_diff)
    fidelity = 100 * x_cdf_abs_diff[threshold]

    print(f"maximum fidelity with threshold: {x_cdf[threshold]:.4g} is equal to: {fidelity:.2f}%")

    ax_cdf.plot(x_cdf, x_0_cdf, label="CDF 0", linewidth=1.5)
    ax_cdf.plot(x_cdf, x_1_cdf, label="CDF 1", linewidth=1.5)
    ax_cdf.plot(
        [x_cdf[threshold]] * 2,
        [x_0_cdf[threshold], x_1_cdf[threshold]],
        color="red",
        marker="o",
        label=f"Max fidelity: {fidelity:.2f}%",
    )
    ax_cdf.set_title("Cumulative Distribution (X-axis)")
    ax_cdf.set_xlabel("Projected X value")
    ax_cdf.set_ylabel("Probability")
    ax_cdf.legend()
    ax_cdf.grid(True)

    fig.suptitle(ipname)
    plt.tight_layout()
    plt.show()

    return fig
