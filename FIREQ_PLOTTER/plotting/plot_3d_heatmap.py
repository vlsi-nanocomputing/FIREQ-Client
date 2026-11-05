"""Interactive 3D heatmap of the experiment data."""

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
    drop_constant_levels,
    figure_is_open,
    slice_dataframe,
    union_index_values,
)

QUANTITY_RADIO_TITLE: str = "quantity"
X_AXIS_RADIO_TITLE: str = "x-axis"
Y_AXIS_RADIO_TITLE: str = "y-axis"
DATASET_RADIO_TITLE: str = "dataset"

#: quantities the heatmap can display, each with its own colormap.
HEATMAP_QUANTITY_KEYS: tuple[str, ...] = ("magnitude", "phase", "real", "imag")

# layout constants, in figure fractions, for the colorbar axes
COLORBAR_LEFT: float = 0.935
COLORBAR_WIDTH: float = 0.02
IMAGE_TOP: float = 0.92


def _plot_3d_heatmap(
    dataframes: Sequence[pd.DataFrame],
    ipname: str = "default",
    dataset_names: Sequence[str] | None = None,
    average_over: str | None = "shot",
) -> PlotHandles:
    """Interactively plot the experiment data as a heatmap.

    The figure holds a quantity selector, an x-axis selector, a y-axis
    selector, one slider per remaining swept variable, and - when several
    dataframes are given - a selector for the displayed dataframe.

    :param dataframes: dataframes to plot, one is displayed at a time.
    :type dataframes: Sequence[pd.DataFrame]
    :param ipname: name of the acquisition IP, displayed as the title.
    :type ipname: str
    :param dataset_names: name of the experiment directory each dataframe
        comes from, used by the dataframe selector.
    :type dataset_names: Sequence[str] | None
    :param average_over: average the values of this index level, usually
        "shot". Ignored when the level is absent.
    :type average_over: str | None
    :raises ValueError: if there is no dataframe or fewer than two swept
        variables.
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
    if len(var_axes) < 2:
        raise ValueError("Not enough axes in data to plot a heatmap")

    var_values = union_index_values(plot_dfs, var_axes)
    quantity_keys = list(HEATMAP_QUANTITY_KEYS)
    label_to_key = {QUANTITY_SPECS[key].label: key for key in quantity_keys}
    axis_labels = list(var_axes)

    # the state every callback reads and writes
    state = {
        "quantity": quantity_keys[0],
        "x_axis": var_axes[-1],
        "y_axis": var_axes[-2],
        "dataset": 0,
        "syncing": False,
    }

    plt.style.use(PLOT_STYLE)
    fig, ax = plt.subplots(figsize=(9, 6))

    # the colorbar gets its own axes, so that adjusting the subplot never
    # overlaps it with the image
    cax = fig.add_axes((COLORBAR_LEFT, 0.1, COLORBAR_WIDTH, 0.5))
    img = ax.imshow(
        np.zeros((2, 2)),
        origin="lower",
        aspect="auto",
        interpolation="nearest",
        cmap=QUANTITY_SPECS[state["quantity"]].cmap,
    )
    cbar = fig.colorbar(img, cax=cax)
    cbar.set_label(QUANTITY_SPECS[state["quantity"]].label)

    def update() -> None:
        """Redraw the heatmap for the selected quantity, axes and values."""
        if not figure_is_open(fig):
            return

        spec = QUANTITY_SPECS[state["quantity"]]
        x_axis = state["x_axis"]
        y_axis = state["y_axis"]

        filtered = slice_dataframe(plot_dfs[state["dataset"]], panel.selection())
        if filtered.empty:
            return
        heat_frame = drop_constant_levels(filtered, (y_axis, x_axis))

        # the remaining index is exactly [y_axis, x_axis], unstack x into columns
        heat = heat_frame[VALUE_COLUMN].unstack(x_axis).sort_index(axis=0).sort_index(axis=1)
        x = heat.columns.to_numpy()
        y = heat.index.to_numpy()
        data = spec.transform(heat.to_numpy())

        img.set_data(data)
        img.set_extent([x[0], x[-1], y[0], y[-1]])

        finite = data[np.isfinite(data)]
        if finite.size:
            low, high = float(finite.min()), float(finite.max())
            if high <= low:
                # a constant slice has no colour range of its own
                high = low + 1.0
            img.set_clim(low, high)

        ax.set_xlim(x[0], x[-1])
        ax.set_ylim(y[0], y[-1])
        ax.set_xlabel(x_axis)
        ax.set_ylabel(y_axis)
        ax.set_title(ipname if len(plot_dfs) == 1 else f"{ipname} — {names[state['dataset']]}")
        fig.canvas.draw_idle()

    def layout(slider_count: int) -> None:
        """Reserve room for the sliders and keep the colorbar beside the image.

        :param slider_count: number of sliders currently displayed.
        :type slider_count: int
        """
        bottom = LAYOUT_BASE_BOTTOM + LAYOUT_STRIDE * slider_count
        fig.subplots_adjust(left=0.08, right=0.92, top=IMAGE_TOP, bottom=bottom)
        cax.set_position((COLORBAR_LEFT, bottom, COLORBAR_WIDTH, IMAGE_TOP - bottom))

    def select_axis(which: str, label: str) -> None:
        """Send a variable to one axis, swapping it with the other axis.

        :param which: the axis to change, "x_axis" or "y_axis".
        :type which: str
        :param label: name of the selected variable.
        :type label: str
        """
        if state["syncing"]:
            return

        other = "y_axis" if which == "x_axis" else "x_axis"
        previous = state[which]
        state[which] = label
        if label == state[other]:
            # the same variable was picked twice, the two axes swap
            state["syncing"] = True
            state[other] = previous
            other_title = Y_AXIS_RADIO_TITLE if other == "y_axis" else X_AXIS_RADIO_TITLE
            radios[other_title].set_active(axis_labels.index(previous))
            state["syncing"] = False

        panel.rebuild([var for var in var_axes if var not in (state["x_axis"], state["y_axis"])], var_values)

    def on_quantity(label: str) -> None:
        """Switch the plotted quantity.

        :param label: label of the selected quantity.
        :type label: str
        """
        key = label_to_key[label]
        state["quantity"] = key
        spec = QUANTITY_SPECS[key]
        img.set_cmap(spec.cmap)
        cbar.set_label(spec.label)
        update()

    def on_x_axis(label: str) -> None:
        """Move a variable to the x axis.

        :param label: name of the selected variable.
        :type label: str
        """
        select_axis("x_axis", label)

    def on_y_axis(label: str) -> None:
        """Move a variable to the y axis.

        :param label: name of the selected variable.
        :type label: str
        """
        select_axis("y_axis", label)

    def on_dataset(label: str) -> None:
        """Switch the displayed dataframe.

        :param label: name of the selected experiment directory.
        :type label: str
        """
        state["dataset"] = names.index(label)
        update()

    panel = SliderPanel(fig, update, on_layout_change=layout)

    specs = [
        SelectorSpec(
            QUANTITY_RADIO_TITLE,
            [QUANTITY_SPECS[key].label for key in quantity_keys],
            quantity_keys.index(state["quantity"]),
            on_quantity,
        ),
        SelectorSpec(X_AXIS_RADIO_TITLE, axis_labels, axis_labels.index(state["x_axis"]), on_x_axis),
        SelectorSpec(Y_AXIS_RADIO_TITLE, axis_labels, axis_labels.index(state["y_axis"]), on_y_axis),
    ]
    if len(plot_dfs) > 1:
        specs.append(SelectorSpec(DATASET_RADIO_TITLE, names, state["dataset"], on_dataset))
    control_fig, radios = add_selector_window(specs, fig, title=f"{ipname} controls")

    panel.rebuild([var for var in var_axes if var not in (state["x_axis"], state["y_axis"])], var_values)

    plt.show()
    return PlotHandles(fig, ax, radios, panel, control_fig)
