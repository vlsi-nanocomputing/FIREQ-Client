"""Public plot functions.

Each function accepts an experiment directory, an ``ExperimentResult``, a DataFrame, a dict of DataFrames by
acquisition IP, or a list of those. With ``ip=None`` it plots every IP common to all inputs and returns a list
of handles; with an IP name it returns one handle. Keep the returned handles referenced while using the
widgets of a non-blocking figure (``show=False`` or Jupyter).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import pandas as pd

from .data import normalise
from .plotting.common import PlotHandles, plot_datasets, show_figures
from .plotting.plot_2d import _plot_2d
from .plotting.plot_3d_heatmap import _plot_3d_heatmap
from .plotting.plot_iq import CdfResult, _plot_iq_curve, _plot_rotated_cdf


def _plot(
    plotting_func: Callable[..., PlotHandles],
    data: object,
    ip: str | None,
    show: bool,
    block: bool | None,
    **kwargs: object,
) -> PlotHandles | list[PlotHandles]:
    """Plot every requested IP, then show all the figures together.

    :param plotting_func: the private plot function.
    :type plotting_func: Callable[..., PlotHandles]
    :param data: the input data.
    :type data: object
    :param ip: plot only this IP.
    :type ip: str | None
    :param show: show the figures.
    :type show: bool
    :param block: forwarded to ``plt.show``.
    :type block: bool | None
    :param kwargs: forwarded to the plot function.
    :type kwargs: object
    :return: one handle for an IP name, else the list of handles.
    :rtype: PlotHandles | list[PlotHandles]
    """
    plotted = plot_datasets(normalise(data), plotting_func, ip=ip, show=False, **kwargs)
    if show:
        show_figures(block)
    handles = list(plotted.values())
    return handles[0] if ip is not None else handles


def plot_2d(
    data: object,
    *,
    ip: str | None = None,
    quantity: str = "magnitude",
    x: str | None = None,
    selection: Mapping[str, float] | None = None,
    average_over: str | None = "shot",
    interactive: bool = True,
    show: bool = True,
    block: bool | None = None,
) -> PlotHandles | list[PlotHandles]:
    """Plot the data as curves, one per experiment.

    :param data: experiment directories, results or DataFrames.
    :type data: object
    :param ip: plot only this acquisition IP.
    :type ip: str | None
    :param quantity: ``magnitude``, ``phase``, ``phase_unwrapped``, ``real``, ``imag`` or ``real_imag``.
    :type quantity: str
    :param x: variable on the x axis, the innermost one by default.
    :type x: str | None
    :param selection: value of the other swept variables.
    :type selection: Mapping[str, float] | None
    :param average_over: index level averaged before plotting.
    :type average_over: str | None
    :param interactive: draw the selectors and sliders.
    :type interactive: bool
    :param show: show the figures.
    :type show: bool
    :param block: forwarded to ``plt.show``.
    :type block: bool | None
    :return: the handles.
    :rtype: PlotHandles | list[PlotHandles]
    """
    return _plot(
        _plot_2d,
        data,
        ip,
        show,
        block,
        quantity=quantity,
        x=x,
        selection=selection,
        average_over=average_over,
        interactive=interactive,
    )


def plot_3d_heatmap(
    data: object,
    *,
    ip: str | None = None,
    quantity: str = "magnitude",
    x: str | None = None,
    y: str | None = None,
    dataset: int = 0,
    selection: Mapping[str, float] | None = None,
    average_over: str | None = "shot",
    interactive: bool = True,
    show: bool = True,
    block: bool | None = None,
) -> PlotHandles | list[PlotHandles]:
    """Plot the data as a heatmap of two swept variables.

    :param data: experiment directories, results or DataFrames.
    :type data: object
    :param ip: plot only this acquisition IP.
    :type ip: str | None
    :param quantity: ``magnitude``, ``phase``, ``real`` or ``imag``.
    :type quantity: str
    :param x: variable on the x axis.
    :type x: str | None
    :param y: variable on the y axis.
    :type y: str | None
    :param dataset: index of the experiment displayed first.
    :type dataset: int
    :param selection: value of the other swept variables.
    :type selection: Mapping[str, float] | None
    :param average_over: index level averaged before plotting.
    :type average_over: str | None
    :param interactive: draw the selectors and sliders.
    :type interactive: bool
    :param show: show the figures.
    :type show: bool
    :param block: forwarded to ``plt.show``.
    :type block: bool | None
    :return: the handles.
    :rtype: PlotHandles | list[PlotHandles]
    """
    return _plot(
        _plot_3d_heatmap,
        data,
        ip,
        show,
        block,
        quantity=quantity,
        x=x,
        y=y,
        dataset=dataset,
        selection=selection,
        average_over=average_over,
        interactive=interactive,
    )


def plot_iq(
    data: object,
    *,
    ip: str | None = None,
    curve_parameter: str | None = None,
    selection: Mapping[str, float] | None = None,
    average_over: str | None = "shot",
    cdf_analysis: bool | None = None,
    interactive: bool = True,
    show: bool = True,
    block: bool | None = None,
) -> PlotHandles | list[PlotHandles]:
    """Plot the data on the IQ plane; with two single-shot inputs, also their readout fidelity.

    :param data: experiment directories, results or DataFrames.
    :type data: object
    :param ip: plot only this acquisition IP.
    :type ip: str | None
    :param curve_parameter: swept variable used as the curve parameter.
    :type curve_parameter: str | None
    :param selection: value of the other swept variables.
    :type selection: Mapping[str, float] | None
    :param average_over: index level averaged before plotting.
    :type average_over: str | None
    :param cdf_analysis: run the readout fidelity analysis (in ``handles.cdf``); None runs it when possible.
    :type cdf_analysis: bool | None
    :param interactive: draw the selector and sliders.
    :type interactive: bool
    :param show: show the figures.
    :type show: bool
    :param block: forwarded to ``plt.show``.
    :type block: bool | None
    :return: the handles.
    :rtype: PlotHandles | list[PlotHandles]
    """
    return _plot(
        _plot_iq_curve,
        data,
        ip,
        show,
        block,
        curve_parameter=curve_parameter,
        selection=selection,
        average_over=average_over,
        cdf_analysis=cdf_analysis,
        interactive=interactive,
    )


def rotated_cdf(
    data_0: object,
    data_1: object,
    *,
    ip: str | None = None,
    plot: bool = True,
    show: bool = True,
    block: bool | None = None,
) -> CdfResult:
    """Compute the single-shot readout fidelity of two experiments, states 0 and 1.

    :param data_0: shots prepared in state 0 (a directory, result or DataFrame).
    :type data_0: object
    :param data_1: shots prepared in state 1.
    :type data_1: object
    :param ip: acquisition IP, optional when the data has only one.
    :type ip: str | None
    :param plot: draw the rotated clouds and the cumulative distributions.
    :type plot: bool
    :param show: show the figure.
    :type show: bool
    :param block: forwarded to ``plt.show``.
    :type block: bool | None
    :raises ValueError: if the IP is ambiguous or missing.
    :return: the figure and the fidelity.
    :rtype: CdfResult
    """
    frames = []
    for data in (data_0, data_1):
        datasets = normalise(data)
        if len(datasets) != 1:
            raise ValueError("Each state needs exactly one experiment")
        frames.append(datasets[0][1])
    common = sorted(set(frames[0]) & set(frames[1]))
    if ip is None:
        if len(common) != 1:
            raise ValueError(f"Choose the ip among {common}")
        ip = common[0]
    elif ip not in common:
        raise ValueError(f"Unknown IP {ip!r}, the data has {common}")
    df_0: pd.DataFrame = frames[0][ip]
    df_1: pd.DataFrame = frames[1][ip]
    return _plot_rotated_cdf(df_0, df_1, ipname=ip, plot=plot, show=show, block=block)
