"""IQ plane comparison plot."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import NamedTuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.widgets import RadioButtons

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
    check_choice,
    common_variable_axes,
    figure_is_open,
    finish_plot,
    get_df_axes,
    show_figures,
    slice_dataframe,
    union_index_values,
)
from .fidelity import ReadoutFidelity, compute_readout_fidelity, rotate_to_real_axis

log = logging.getLogger(__name__)

PARAMETER_RADIO_TITLE: str = "parameter"
SHOT_LEVEL: str = "shot"
TIME_LEVEL: str = "time"


class CdfResult(NamedTuple):
    """The readout fidelity analysis of two single-shot datasets.

    Both fields are None when the datasets are too small to compare.
    """

    #: the rotated clouds and their cumulative distributions, None when not plotted
    figure: Figure | None
    fidelity: ReadoutFidelity | None


def _single_shot_issue(dataframes: Sequence[pd.DataFrame], names: Sequence[str]) -> str | None:
    """Tell why the given dataframes are not single shots of a single configuration.

    :param dataframes: un-averaged dataframes, one per prepared state.
    :type dataframes: Sequence[pd.DataFrame]
    :param names: name of each dataframe, used in the message.
    :type names: Sequence[str]
    :return: the reason, or None when every dataframe holds one sample per shot
        and sweeps nothing but the shots.
    :rtype: str | None
    """
    for name, df in zip(names, dataframes, strict=True):
        var_axes, _ = get_df_axes(df)
        if TIME_LEVEL in var_axes:
            samples = df.index.get_level_values(TIME_LEVEL).nunique()
            return (
                f"{name} holds {samples} samples per shot (index level {TIME_LEVEL!r}), "
                "it needs one sample per shot (accumulated mode)"
            )
        swept = [var for var in var_axes if var != SHOT_LEVEL]
        if swept:
            return f"{name} sweeps {swept}, it needs the shots of a single configuration"
    return None


def _plot_iq_curve(
    dataframes: Sequence[pd.DataFrame],
    ipname: str = "default",
    dataset_names: Sequence[str] | None = None,
    average_over: str | None = "shot",
    cdf_analysis: bool | None = None,
    *,
    curve_parameter: str | None = None,
    selection: Mapping[str, float] | None = None,
    interactive: bool = True,
    show: bool = True,
    block: bool | None = None,
) -> PlotHandles:
    """Plot the data of one or more experiments on an IQ plane.

    The plane holds the imaginary part of the data against the real part, and
    a selector chooses which swept variable is used as the curve parameter.
    The remaining swept variables get a slider. When nothing is swept once the
    data is averaged, as for a single run of shots, the raw shots of each
    dataframe are scattered instead, with no selector and no slider.

    The readout fidelity analysis always runs on the un-averaged shots, and
    only on single-shot data: no swept variable other than the shots, and one
    sample per shot.

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
    :param cdf_analysis: run the rotated CDF analysis, drawn in its own
        figure, with its result in ``cdf`` of the handles. When None it runs if
        exactly two dataframes are given and they hold single shots, and is
        skipped with a log message otherwise.
    :type cdf_analysis: bool | None
    :param curve_parameter: swept variable used as the curve parameter, the
        innermost one by default.
    :type curve_parameter: str | None
    :param selection: value of the other swept variables (the slider positions).
    :type selection: Mapping[str, float] | None
    :param interactive: draw the selector and the sliders; when False the
        figure is static.
    :type interactive: bool
    :param show: show the figures.
    :type show: bool
    :param block: forwarded to ``plt.show``.
    :type block: bool | None
    :raises ValueError: if there is no dataframe, or the CDF analysis is asked
        for with a number of dataframes other than two or on data that is not
        single-shot.
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
    # nothing left to sweep: scatter the raw shots, the state clouds
    shot_clouds = not var_axes
    if shot_clouds:
        plot_dfs = list(dataframes)

    run_cdf = len(plot_dfs) == 2 if cdf_analysis is None else cdf_analysis
    if run_cdf and len(plot_dfs) != 2:
        raise ValueError("CDF analysis is only supported for exactly two dataframes")

    cdf_skip_reason = _single_shot_issue(dataframes, names) if run_cdf else None
    if cdf_skip_reason is not None and cdf_analysis:
        raise ValueError(f"Readout fidelity analysis not possible: {cdf_skip_reason}")

    var_values = union_index_values(plot_dfs, var_axes)
    axis_labels = list(var_axes)
    check_choice("curve parameter", curve_parameter, var_axes)

    # the state every callback reads and writes
    state = {"curve_param": curve_parameter or (var_axes[-1] if var_axes else None)}

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
        subtitle = "Single shots" if shot_clouds else f"Parameter: {state['curve_param']}"
        ax.set_title(f"{ipname}\n{subtitle}")
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

    panel = SliderPanel(fig, update, on_layout_change=layout, initial=selection, interactive=interactive)

    control_fig: Figure | None = None
    radios: dict[str, RadioButtons] = {}
    if interactive and not shot_clouds:
        control_fig, radios = add_selector_window(
            [SelectorSpec(PARAMETER_RADIO_TITLE, axis_labels, axis_labels.index(state["curve_param"]), on_parameter)],
            fig,
            title=f"{ipname} controls",
        )

    panel.rebuild([var for var in var_axes if var != state["curve_param"]], var_values)
    if len(plot_dfs) > 1:
        ax.legend(loc="best")

    cdf = None
    if run_cdf:
        if cdf_skip_reason is not None:
            log.info(f"readout fidelity {ipname}: skipped, {cdf_skip_reason}")
        else:
            cdf = _plot_rotated_cdf(dataframes[0], dataframes[1], ipname=ipname, show=False)

    return finish_plot(PlotHandles(fig, ax, radios, panel, control_fig, cdf), show, block)


def _cdf_steps(sorted_values: np.ndarray, low: float, high: float) -> tuple[np.ndarray, np.ndarray]:
    """Return the vertices of an empirical CDF, to draw with ``step(..., where="post")``.

    :param sorted_values: the values, sorted in ascending order.
    :type sorted_values: np.ndarray
    :param low: left end of the drawn range, at or below the smallest value.
    :type low: float
    :param high: right end of the drawn range, at or above the largest value.
    :type high: float
    :return: the x and y coordinates of the steps.
    :rtype: tuple[np.ndarray, np.ndarray]
    """
    count = sorted_values.size
    x = np.concatenate([[low], sorted_values, [high]])
    y = np.concatenate([[0.0], np.arange(1, count + 1) / count, [1.0]])
    return x, y


def _plot_rotated_cdf(
    df_0: pd.DataFrame,
    df_1: pd.DataFrame,
    ipname: str = "default",
    *,
    plot: bool = True,
    show: bool = True,
    block: bool | None = None,
) -> CdfResult:
    """Rotate two single-shot IQ datasets and compute their readout fidelity.

    The clouds are rotated so that the line joining their means is horizontal,
    with state 1 on the right, and the threshold on that axis is chosen to
    maximise the separation of the cumulative distributions, as computed by
    :func:`compute_readout_fidelity`. The assignment fidelity, the separation,
    the error rates and the threshold are logged, and the rotated clouds are
    drawn next to the cumulative distributions.

    :param df_0: shots prepared in state 0.
    :type df_0: pd.DataFrame
    :param df_1: shots prepared in state 1.
    :type df_1: pd.DataFrame
    :param ipname: name of the acquisition IP, displayed as the title.
    :type ipname: str
    :param plot: draw the figure.
    :type plot: bool
    :param show: show the figure.
    :type show: bool
    :param block: forwarded to ``plt.show``.
    :type block: bool | None
    :return: the figure and the fidelity, both None when the datasets are too
        small to compare.
    :rtype: CdfResult
    """
    iq_0 = df_0[VALUE_COLUMN].to_numpy()
    iq_1 = df_1[VALUE_COLUMN].to_numpy()

    if iq_0.size < 2 or iq_1.size < 2:
        log.warning("CDF analysis needs at least two samples per dataset")
        return CdfResult(None, None)

    if iq_0.mean() == iq_1.mean():
        log.warning(f"readout fidelity {ipname}: warning, the two datasets have the same mean, rotation angle set to 0")

    fidelity = compute_readout_fidelity(iq_0, iq_1)
    iq_0_rot, iq_1_rot, _ = rotate_to_real_axis(iq_0, iq_1)
    mean_0_rot = iq_0_rot.mean()
    mean_1_rot = iq_1_rot.mean()

    assignment = 100 * fidelity.assignment_fidelity
    separation = 100 * fidelity.separation
    log.info(
        f"readout fidelity {ipname}: assignment 1-(ε0+ε1)/2 = {assignment:.2f}% | "
        f"separation 1-ε0-ε1 = {separation:.2f}% | "
        f"ε0=P(1|0)={100 * fidelity.eps_0:.2f}% ε1=P(0|1)={100 * fidelity.eps_1:.2f}% | "
        f"threshold {fidelity.threshold:.3e} (rotated axis) | shots {fidelity.n_0}/{fidelity.n_1}"
    )

    if not plot:
        return CdfResult(None, fidelity)

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
    ax_scatter.axvline(fidelity.threshold, color="red", linestyle="--", linewidth=1, label="Threshold")
    ax_scatter.set_title("Rotated IQ Plane")
    ax_scatter.set_xlabel("I (Real)")
    ax_scatter.set_ylabel("Q (Imag)")
    ax_scatter.legend()
    ax_scatter.grid(True)

    x_0_sorted = np.sort(iq_0_rot.real)
    x_1_sorted = np.sort(iq_1_rot.real)
    x_min = min(x_0_sorted[0], x_1_sorted[0])
    x_max = max(x_0_sorted[-1], x_1_sorted[-1])

    ax_cdf.step(*_cdf_steps(x_0_sorted, x_min, x_max), where="post", label="CDF 0", linewidth=1.5)
    ax_cdf.step(*_cdf_steps(x_1_sorted, x_min, x_max), where="post", label="CDF 1", linewidth=1.5)
    ax_cdf.plot(
        [fidelity.threshold] * 2,
        [1 - fidelity.eps_0, fidelity.eps_1],
        color="red",
        marker="o",
        label=f"assignment {assignment:.2f}% / separation {separation:.2f}%",
    )
    ax_cdf.set_title("Cumulative Distribution (X-axis)")
    ax_cdf.set_xlabel("Projected X value")
    ax_cdf.set_ylabel("Probability")
    ax_cdf.legend()
    ax_cdf.grid(True)

    fig.suptitle(ipname)
    fig.tight_layout()
    if show:
        show_figures(block)

    return CdfResult(fig, fidelity)
