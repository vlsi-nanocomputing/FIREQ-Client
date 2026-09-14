"""Interactive 2D line plot of the experiment data."""

from __future__ import annotations

from collections.abc import Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .common import (
    LAYOUT_BASE_BOTTOM,
    LAYOUT_STRIDE,
    PLOT_STYLE,
    QUANTITY_SPECS,
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

QUANTITY_RADIO_TITLE: str = "quantity"
X_AXIS_RADIO_TITLE: str = "x-axis"
NO_LEGEND_LABEL: str = "_nolegend_"

#: quantities offered by the 2D plot, mapped to the components they draw as
#: (quantity key, linestyle) pairs.
PLOT_2D_SERIES: dict[str, tuple[tuple[str, str], ...]] = {
    "magnitude": (("magnitude", ":"),),
    "phase": (("phase", "-"),),
    "phase_unwrapped": (("phase_unwrapped", "-"),),
    "real": (("real", "-"),),
    "imag": (("imag", "--"),),
    "real_imag": (("real", "-"), ("imag", "--")),
}

#: the largest number of components a quantity draws.
MAX_COMPONENTS: int = max(len(series) for series in PLOT_2D_SERIES.values())


def _series_label(dataset_name: str, component: str, dataset_count: int, component_count: int) -> str:
    """Return the legend label of one curve.

    :param dataset_name: name of the experiment directory the curve comes from.
    :type dataset_name: str
    :param component: label of the plotted quantity.
    :type component: str
    :param dataset_count: number of experiment directories plotted together.
    :type dataset_count: int
    :param component_count: number of components drawn for the quantity.
    :type component_count: int
    :return: the label to display.
    :rtype: str
    """
    if dataset_count == 1 and component_count == 1:
        return component
    if component_count == 1:
        return dataset_name
    return f"{dataset_name} · {component}"


def _plot_2d(
    dataframes: Sequence[pd.DataFrame],
    ipname: str = "default",
    dataset_names: Sequence[str] | None = None,
    average_over: str | None = "shot",
) -> PlotHandles:
    """Interactively plot the experiment data as curves.

    The figure holds a quantity selector, an x-axis selector and one slider
    per remaining swept variable. Each dataframe is drawn as its own series.

    :param dataframes: dataframes to plot, one series each.
    :type dataframes: Sequence[pd.DataFrame]
    :param ipname: name of the acquisition IP, displayed as the title.
    :type ipname: str
    :param dataset_names: name of the experiment directory each dataframe
        comes from, used for the legend.
    :type dataset_names: Sequence[str] | None
    :param average_over: average the values of this index level, usually
        "shot". Ignored when the level is absent.
    :type average_over: str | None
    :raises ValueError: if there is no dataframe or no swept variable.
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
        raise ValueError("Not enough axes in data to plot in 2D")

    var_values = union_index_values(plot_dfs, var_axes)
    quantity_keys = list(PLOT_2D_SERIES)
    label_to_key = {QUANTITY_SPECS[key].label: key for key in quantity_keys}

    # the state every callback reads and writes
    state = {"quantity": quantity_keys[0], "x_axis": var_axes[-1]}

    plt.style.use(PLOT_STYLE)
    fig, ax = plt.subplots(figsize=(9, 5.5))

    # one line per dataset and component slot, reused for every quantity
    lines = {
        (dataset_index, slot): ax.plot([], [], lw=2, label=NO_LEGEND_LABEL)[0]
        for dataset_index in range(len(plot_dfs))
        for slot in range(MAX_COMPONENTS)
    }

    def update() -> None:
        """Redraw the curves for the selected quantity, x axis and values."""
        if not figure_is_open(fig):
            return

        components = PLOT_2D_SERIES[state["quantity"]]
        x_axis = state["x_axis"]
        selection = panel.selection()

        for dataset_index, df in enumerate(plot_dfs):
            filtered = slice_dataframe(df, selection)
            if filtered.empty:
                for slot in range(MAX_COMPONENTS):
                    line = lines[(dataset_index, slot)]
                    line.set_data([], [])
                    line.set_visible(False)
                    line.set_label(NO_LEGEND_LABEL)
                continue

            x = filtered.index.get_level_values(x_axis).to_numpy()
            y = filtered[VALUE_COLUMN].to_numpy()

            # keep points ordered along x, filtering can leave them shuffled
            order = np.argsort(x)
            x = x[order]
            y = y[order]

            for slot in range(MAX_COMPONENTS):
                line = lines[(dataset_index, slot)]
                if slot >= len(components):
                    line.set_data([], [])
                    line.set_visible(False)
                    line.set_label(NO_LEGEND_LABEL)
                    continue
                component_key, linestyle = components[slot]
                spec = QUANTITY_SPECS[component_key]
                line.set_data(x, spec.transform(y))
                line.set_linestyle(linestyle)
                line.set_visible(True)
                line.set_label(_series_label(names[dataset_index], spec.label, len(plot_dfs), len(components)))

        ax.set_xlabel(x_axis)
        ax.set_ylabel(VALUE_COLUMN)
        ax.set_title(ipname)
        ax.relim()
        ax.autoscale_view()
        fig.canvas.draw_idle()

    def refresh_legend() -> None:
        """Rebuild the legend from the visible series."""
        legend = ax.get_legend()
        if legend is not None:
            legend.remove()

        handles = [line for line in lines.values() if line.get_visible() and line.get_label() != NO_LEGEND_LABEL]
        if handles:
            ax.legend(handles=handles, loc="best")

    def layout(slider_count: int) -> None:
        """Reserve room for the selector boxes and the sliders.

        :param slider_count: number of sliders currently displayed.
        :type slider_count: int
        """
        fig.subplots_adjust(
            left=0.09,
            right=0.97,
            top=0.92,
            bottom=LAYOUT_BASE_BOTTOM + LAYOUT_STRIDE * slider_count,
        )

    def on_quantity(label: str) -> None:
        """Switch the plotted quantity.

        :param label: label of the selected quantity.
        :type label: str
        """
        state["quantity"] = label_to_key[label]
        update()
        refresh_legend()

    def on_x_axis(label: str) -> None:
        """Move a variable to the x axis and give the others a slider.

        :param label: name of the selected x-axis variable.
        :type label: str
        """
        state["x_axis"] = label
        panel.rebuild([var for var in var_axes if var != label], var_values)

    panel = SliderPanel(fig, update, on_layout_change=layout)

    control_fig, radios = add_selector_window(
        [
            SelectorSpec(
                QUANTITY_RADIO_TITLE,
                [QUANTITY_SPECS[key].label for key in quantity_keys],
                quantity_keys.index(state["quantity"]),
                on_quantity,
            ),
            SelectorSpec(X_AXIS_RADIO_TITLE, list(var_axes), var_axes.index(state["x_axis"]), on_x_axis),
        ],
        fig,
        title=f"{ipname} controls",
    )

    panel.rebuild([var for var in var_axes if var != state["x_axis"]], var_values)
    refresh_legend()

    plt.show()
    return PlotHandles(fig, ax, radios, panel, control_fig)
