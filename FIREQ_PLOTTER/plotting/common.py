"""Shared helpers for the plotting functions."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.widgets import RadioButtons, Slider

VALUE_COLUMN: str = "value"
PLOT_STYLE: str = "seaborn-v0_8-darkgrid"

# font sizes of the selector boxes and of their box titles
SELECTOR_FONTSIZE: float = 11.0
SELECTOR_TITLE_FONTSIZE: float = 12.0

# layout constants, in inches, for the window holding the selector boxes
CONTROL_ITEM_HEIGHT: float = 0.34
CONTROL_TITLE_HEIGHT: float = 0.42
CONTROL_GAP: float = 0.30
CONTROL_MARGIN: float = 0.30
CONTROL_SUPTITLE_HEIGHT: float = 0.50
CONTROL_BASE_WIDTH: float = 1.5
CONTROL_CHAR_WIDTH: float = 0.098
CONTROL_MIN_WIDTH: float = 2.8

# layout constants, in figure fractions, for the slider stack in the bottom margin
SLIDER_LEFT: float = 0.18
SLIDER_WIDTH: float = 0.62
SLIDER_HEIGHT: float = 0.03
SLIDER_STRIDE: float = 0.05
SLIDER_BOTTOM: float = 0.03

# bottom margin a subplot must leave free for a stack of sliders
LAYOUT_BASE_BOTTOM: float = 0.09
LAYOUT_STRIDE: float = 0.05


class QuantitySpec(NamedTuple):
    """A quantity derived from the complex ``value`` column of an experiment."""

    key: str
    label: str
    transform: Callable[[np.ndarray], np.ndarray]
    cmap: str


class SelectorSpec(NamedTuple):
    """A radio-button selector to draw in the controls window."""

    title: str
    labels: Sequence[str]
    active: int
    on_change: Callable[[str], None]


class PlotHandles(NamedTuple):
    """The interactive elements of a plot, returned for inspection and tests."""

    figure: Figure
    axes: Axes
    radios: dict[str, RadioButtons]
    panel: SliderPanel
    controls: Figure


def _unwrap_phase(values: np.ndarray) -> np.ndarray:
    """Return the phase of complex values, unwrapped along the last axis.

    :param values: complex values.
    :type values: np.ndarray
    :return: unwrapped phase, in radians.
    :rtype: np.ndarray
    """
    return np.unwrap(np.angle(values))


QUANTITY_SPECS: dict[str, QuantitySpec] = {
    "magnitude": QuantitySpec("magnitude", "magnitude", np.abs, "viridis"),
    "phase": QuantitySpec("phase", "phase", np.angle, "twilight"),
    "phase_unwrapped": QuantitySpec("phase_unwrapped", "phase (unwrapped)", _unwrap_phase, "twilight"),
    "real": QuantitySpec("real", "real", np.real, "RdBu_r"),
    "imag": QuantitySpec("imag", "imag", np.imag, "RdBu_r"),
    # 'real + imag' is only offered by the 2D plot, which draws its two components;
    # the transform and colormap are placeholders so every spec stays complete.
    "real_imag": QuantitySpec("real_imag", "real + imag", np.real, "RdBu_r"),
}


def load_exported_dataframes(exp_dir: Path) -> dict[str, pd.DataFrame]:
    """Load the dataframes of an experiment exported from the client.

    Every exported experiment directory contains one dataframe per acquisition
    IP, named data_<acquisition_ip_name>.pkl.

    :param exp_dir: experiment directory.
    :type exp_dir: Path
    :return: a dictionary of dataframes, keyed by acquisition ip name.
    :rtype: dict[str, pd.DataFrame]
    """
    return_dict = {}
    for file in exp_dir.iterdir():
        if file.is_file():
            fname = file.name
            if fname.startswith("data_") and fname.endswith(".pkl"):
                ipname = fname[len("data_") : -len(".pkl")]
                return_dict[ipname] = pd.read_pickle(file)

    return return_dict


def _dataset_names(exp_dirs: Sequence[Path]) -> list[str]:
    """Return a display name per experiment directory.

    :param exp_dirs: experiment directories.
    :type exp_dirs: Sequence[Path]
    :return: the directory basenames, made unique with a suffix when they repeat.
    :rtype: list[str]
    """
    names = [exp_dir.name for exp_dir in exp_dirs]
    if len(set(names)) != len(names):
        names = [f"{name} ({index})" for index, name in enumerate(names)]
    return names


def load_and_plot(
    exp_dirs: Sequence[Path],
    plotting_func: Callable[..., object],
    *,
    raise_on_error: bool = False,
    **kwargs: object,
) -> list[str]:
    """Load one or more exported experiments and plot them.

    plotting_func is called once per acquisition IP name common to ALL
    directories, with a list of dataframes (one per directory, in the same
    order as exp_dirs), the ip name and the directory names. A failure while
    plotting one IP does not prevent the other IPs from being plotted unless
    raise_on_error is set.

    :param exp_dirs: directories of the exported experiments to load.
    :type exp_dirs: Sequence[Path]
    :param plotting_func: callable receiving the dataframes, the ip name, the
        dataset names and any extra keyword argument, and producing the plot.
    :type plotting_func: Callable[..., object]
    :param raise_on_error: re-raise plotting errors instead of reporting them.
    :type raise_on_error: bool
    :param kwargs: additional keyword arguments forwarded to plotting_func.
    :type kwargs: object
    :return: the acquisition ip names that were plotted.
    :rtype: list[str]
    """
    dirs = [Path(exp_dir) for exp_dir in exp_dirs]
    if not dirs:
        raise ValueError("at least one experiment directory is required")

    all_dataframes: list[dict[str, pd.DataFrame]] = []
    for exp_dir in dirs:
        if not exp_dir.is_dir():
            raise ValueError(f"not a directory: {exp_dir}")
        loaded = load_exported_dataframes(exp_dir)
        if not loaded:
            raise ValueError(f"no data_<ipname>.pkl files found in {exp_dir}")
        all_dataframes.append(loaded)

    # ip names common to every loaded experiment
    common_ips = set.intersection(*(set(loaded) for loaded in all_dataframes))
    if not common_ips:
        raise ValueError("No IP names are common to all provided experiment directories")

    dataset_names = _dataset_names(dirs)
    plotted = []
    for ipname in sorted(common_ips):
        dataframes = [loaded[ipname] for loaded in all_dataframes]
        try:
            plotting_func(dataframes, ipname=ipname, dataset_names=dataset_names, **kwargs)
        except Exception as err:
            if raise_on_error:
                raise
            print(f"Failed to plot {ipname}: {type(err).__name__}: {err}")
            continue
        plotted.append(ipname)

    return plotted


def get_df_axes(df: pd.DataFrame) -> tuple[list[str], list[str]]:
    """Split the index levels of a dataframe into variable and fixed axes.

    :param df: DataFrame with a MultiIndex and a single 'value' column.
    :type df: pd.DataFrame
    :return: (variable_axes, fixed_axes), where a variable axis is an index
        level holding more than one unique value.
    :rtype: tuple[list[str], list[str]]
    """
    variable_axes = []
    fixed_axes = []

    for name in df.index.names:
        if name is None:
            continue
        unique_vals = df.index.get_level_values(name).unique()
        if len(unique_vals) > 1:
            variable_axes.append(name)
        else:
            fixed_axes.append(name)

    return variable_axes, fixed_axes


def common_variable_axes(dataframes: Sequence[pd.DataFrame]) -> list[str]:
    """Return the variable axes shared by all the given dataframes.

    :param dataframes: dataframes to compare, the first one is the reference.
    :type dataframes: Sequence[pd.DataFrame]
    :raises ValueError: if a dataframe has different index levels, different
        active axes, no 'value' column, or non-numeric axis values.
    :return: the names of the index levels with more than one unique value.
    :rtype: list[str]
    """
    if not dataframes:
        raise ValueError("at least one dataframe is required")

    reference = dataframes[0]
    reference_names = list(reference.index.names)

    for index, df in enumerate(dataframes[1:], start=1):
        if list(df.index.names) != reference_names:
            raise ValueError(
                f"Dataframe {index} has index levels {list(df.index.names)}, "
                f"expected {reference_names} (must match dataframe 0)"
            )

    for index, df in enumerate(dataframes):
        if VALUE_COLUMN not in df.columns:
            raise ValueError(f"Dataframe {index} has no {VALUE_COLUMN!r} column, found {df.columns.to_list()}")

    var_axes, _ = get_df_axes(reference)
    for index, df in enumerate(dataframes[1:], start=1):
        other_axes, _ = get_df_axes(df)
        if other_axes != var_axes:
            raise ValueError(f"Dataframe {index} has active axes {other_axes}, expected {var_axes}")

    for var in var_axes:
        values = reference.index.get_level_values(var).to_numpy()
        if not np.issubdtype(values.dtype, np.number):
            raise ValueError(f"Axis {var!r} has non-numeric values of dtype {values.dtype} and cannot be plotted")

    return var_axes


def union_index_values(dataframes: Sequence[pd.DataFrame], levels: Sequence[str]) -> dict[str, np.ndarray]:
    """Collect the sorted values available along each level, across dataframes.

    :param dataframes: dataframes to collect the values from.
    :type dataframes: Sequence[pd.DataFrame]
    :param levels: names of the index levels to collect.
    :type levels: Sequence[str]
    :return: a dictionary mapping each level to its sorted union of values.
    :rtype: dict[str, np.ndarray]
    """
    values = {}
    for level in levels:
        chunks = [df.index.get_level_values(level).to_numpy() for df in dataframes]
        values[level] = np.unique(np.concatenate(chunks))
    return values


def reduce_df_over_level(df: pd.DataFrame, level: str) -> pd.DataFrame:
    """Collapse the given index level by averaging.

    :param df: DataFrame with a MultiIndex.
    :type df: pd.DataFrame
    :param level: name of the index level to reduce.
    :type level: str
    :raises ValueError: if the level is absent or is the only index level.
    :return: DataFrame with the level removed, values averaged over it.
    :rtype: pd.DataFrame
    """
    if level not in df.index.names:
        raise ValueError(f"index level {level!r} is not present in {list(df.index.names)}")

    remaining_levels = [name for name in df.index.names if name != level]
    if not remaining_levels:
        raise ValueError("cannot reduce the only index level")

    return df.groupby(level=remaining_levels).mean()


def average_over_level(df: pd.DataFrame, level: str | None) -> pd.DataFrame:
    """Average a dataframe over an index level, when that level exists.

    :param df: DataFrame with a MultiIndex.
    :type df: pd.DataFrame
    :param level: name of the index level to average over, e.g. "shot". The
        dataframe is returned unchanged when it is None or absent.
    :type level: str | None
    :return: the averaged (or unchanged) DataFrame.
    :rtype: pd.DataFrame
    """
    if level is None or level not in df.index.names:
        return df
    return reduce_df_over_level(df, level)


def nearest_value(value: float, available: Sequence[float]) -> float:
    """Return the available value closest to the requested one.

    :param value: requested value.
    :type value: float
    :param available: values the data actually holds.
    :type available: Sequence[float]
    :return: the closest available value, the first one on a tie.
    :rtype: float
    """
    return min(available, key=lambda candidate: abs(candidate - value))


def slice_dataframe(df: pd.DataFrame, selection: Mapping[str, float]) -> pd.DataFrame:
    """Select one value per index level, dropping the selected levels.

    Each requested value is snapped to the closest value that the dataframe
    itself holds, so dataframes sharing a slider but sweeping different values
    stay comparable.

    :param df: DataFrame with a MultiIndex.
    :type df: pd.DataFrame
    :param selection: the value to select for each index level.
    :type selection: Mapping[str, float]
    :return: the selected rows, with the selected index levels dropped.
    :rtype: pd.DataFrame
    """
    filtered = df
    pinned = []
    for level, value in selection.items():
        if level not in df.index.names:
            continue
        available = np.unique(df.index.get_level_values(level).to_numpy())
        filtered = filtered[filtered.index.get_level_values(level) == nearest_value(value, available)]
        pinned.append(level)

    if pinned:
        filtered = filtered.droplevel(pinned)
    return filtered


def drop_constant_levels(df: pd.DataFrame, keep: Sequence[str]) -> pd.DataFrame:
    """Drop the index levels of a dataframe that are not in ``keep``.

    :param df: DataFrame with a MultiIndex.
    :type df: pd.DataFrame
    :param keep: names of the index levels to keep.
    :type keep: Sequence[str]
    :raises ValueError: if a level that is not kept still holds several values.
    :return: the DataFrame reduced to the kept levels.
    :rtype: pd.DataFrame
    """
    extra = [name for name in df.index.names if name not in keep]
    constant = [name for name in extra if df.index.get_level_values(name).nunique() == 1]
    varying = [name for name in extra if name not in constant]
    if varying:
        raise ValueError(f"Cannot reduce the index to {list(keep)}: levels {varying} still hold several values")

    return df.droplevel(constant) if constant else df


def make_formatter(var: str, values: Sequence[float]) -> Callable[[float], str]:
    """Return a formatter showing the variable value for a slider position.

    :param var: name of the variable to format.
    :type var: str
    :param values: values available for the variable.
    :type values: Sequence[float]
    :return: the formatting function.
    :rtype: Callable[[float], str]
    """

    def formatter(position: float) -> str:
        """Format a slider position as the value it points at.

        :param position: slider position, in index units.
        :type position: float
        :return: the formatted value.
        :rtype: str
        """
        index = int(np.clip(round(position), 0, len(values) - 1))
        return f"{values[index]:.6g}"

    return formatter


def _selector_columns(specs: Sequence[SelectorSpec]) -> int:
    """Return the number of columns the selector boxes are laid out in.

    :param specs: the selectors to draw.
    :type specs: Sequence[SelectorSpec]
    :return: 1 for a single selector, 2 otherwise.
    :rtype: int
    """
    return 1 if len(specs) < 2 else 2


def figure_is_open(fig: Figure) -> bool:
    """Return whether the window of a figure is still open.

    :param fig: figure to test.
    :type fig: Figure
    :return: True while the figure is still managed by pyplot.
    :rtype: bool
    """
    return plt.fignum_exists(fig.number)


def _guard_callback(callback: Callable[[str], None], target: Figure) -> Callable[[str], None]:
    """Wrap a selector callback so it is skipped once the target figure is closed.

    The selectors live in their own window, which outlives the window they act
    on: without this guard, clicking a selector after closing the plot would
    draw a figure that no longer exists.

    :param callback: the callback to wrap.
    :type callback: Callable[[str], None]
    :param target: the figure the selectors act on.
    :type target: Figure
    :return: the wrapped callback.
    :rtype: Callable[[str], None]
    """

    def wrapper(label: str) -> None:
        """Run the callback while the target figure is open.

        :param label: the label of the selected option.
        :type label: str
        """
        if figure_is_open(target):
            callback(label)

    return wrapper


def add_selector_window(
    specs: Sequence[SelectorSpec], target: Figure, title: str = "controls"
) -> tuple[Figure, dict[str, RadioButtons]]:
    """Draw the radio-button selectors in a window of their own.

    The window is sized to fit the selectors: its height follows the number of
    options, its width the length of the longest label. Its callbacks are
    ignored once the target figure has been closed.

    :param specs: the selectors to draw, in reading order.
    :type specs: Sequence[SelectorSpec]
    :param target: the figure the selectors act on, usually the plot window.
    :type target: Figure
    :param title: title displayed on top of the window.
    :type title: str
    :return: the window and its selectors, keyed by their title.
    :rtype: tuple[Figure, dict[str, RadioButtons]]
    """
    columns = _selector_columns(specs)
    rows = -(-len(specs) // columns)

    column_widths = []
    for column in range(columns):
        labels = [label for spec in specs[column::columns] for label in spec.labels]
        longest = max((len(label) for label in labels), default=1)
        column_widths.append(max(CONTROL_MIN_WIDTH, CONTROL_BASE_WIDTH + CONTROL_CHAR_WIDTH * longest))

    row_heights = []
    for row in range(rows):
        row_specs = specs[row * columns : (row + 1) * columns]
        row_heights.append(max(CONTROL_TITLE_HEIGHT + CONTROL_ITEM_HEIGHT * len(spec.labels) for spec in row_specs))

    width = sum(column_widths) + 2 * CONTROL_MARGIN
    height = CONTROL_SUPTITLE_HEIGHT + 2 * CONTROL_MARGIN + sum(row_heights) + CONTROL_GAP * (rows - 1)

    fig = plt.figure(figsize=(width, height))
    fig.suptitle(title, fontsize=SELECTOR_TITLE_FONTSIZE)

    boxes: dict[str, RadioButtons] = {}
    top = 1.0 - (CONTROL_SUPTITLE_HEIGHT + CONTROL_MARGIN) / height
    for row in range(rows):
        left = CONTROL_MARGIN / width
        for column in range(columns):
            index = row * columns + column
            if index >= len(specs):
                break
            spec = specs[index]
            box_height = (CONTROL_TITLE_HEIGHT + CONTROL_ITEM_HEIGHT * len(spec.labels)) / height
            ax = fig.add_axes(
                (
                    left,
                    top - box_height,
                    (column_widths[column] - CONTROL_MARGIN) / width,
                    box_height,
                )
            )
            ax.set_facecolor("none")
            ax.set_frame_on(False)
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_title(spec.title, fontsize=SELECTOR_TITLE_FONTSIZE, loc="left")
            radio = RadioButtons(
                ax,
                list(spec.labels),
                active=spec.active,
                label_props={"fontsize": [SELECTOR_FONTSIZE]},
            )
            radio.on_clicked(_guard_callback(spec.on_change, target))
            boxes[spec.title] = radio
            left += column_widths[column] / width
        top -= (row_heights[row] + CONTROL_GAP) / height

    return fig, boxes


class SliderPanel:
    """A stack of index sliders bound to a single update callback.

    The sliders are index based: moving one steps through the values the data
    actually holds, and the label shows that value. The panel can be rebuilt
    whenever the variables that need a slider change.
    """

    def __init__(
        self,
        fig: Figure,
        update: Callable[[], None],
        *,
        on_layout_change: Callable[[int], None] | None = None,
        left: float = SLIDER_LEFT,
        width: float = SLIDER_WIDTH,
        bottom: float = SLIDER_BOTTOM,
        height: float = SLIDER_HEIGHT,
        stride: float = SLIDER_STRIDE,
    ) -> None:
        """Initialize an empty panel.

        :param fig: figure the sliders are drawn in.
        :type fig: Figure
        :param update: callback invoked when a slider moves.
        :type update: Callable[[], None]
        :param on_layout_change: callback invoked with the number of live
            sliders after every rebuild, to reserve room for them.
        :type on_layout_change: Callable[[int], None] | None
        :param left: left edge of the sliders, in figure fractions.
        :type left: float
        :param width: width of the sliders, in figure fractions.
        :type width: float
        :param bottom: bottom edge of the first slider, in figure fractions.
        :type bottom: float
        :param height: height of a slider, in figure fractions.
        :type height: float
        :param stride: vertical distance between two sliders, in figure fractions.
        :type stride: float
        """
        self._fig = fig
        self._update = update
        self._on_layout_change = on_layout_change
        self._left = left
        self._width = width
        self._bottom = bottom
        self._height = height
        self._stride = stride
        self._sliders: dict[str, Slider] = {}
        self._axes: dict[str, Axes] = {}
        self._values: dict[str, np.ndarray] = {}

    def rebuild(self, slider_vars: Sequence[str], var_values: Mapping[str, np.ndarray]) -> None:
        """Replace the slider stack with one slider per given variable.

        :param slider_vars: names of the variables that get a slider.
        :type slider_vars: Sequence[str]
        :param var_values: values available for each variable.
        :type var_values: Mapping[str, np.ndarray]
        """
        self.clear()
        for index, var in enumerate(slider_vars):
            values = np.asarray(var_values[var])
            if values.size < 2:
                continue
            self._values[var] = values
            slider_ax = self._fig.add_axes((self._left, self._bottom + self._stride * index, self._width, self._height))
            slider = Slider(
                slider_ax,
                var,
                valmin=0,
                valmax=values.size - 1,
                valinit=0,
                valstep=1,
                valfmt=make_formatter(var, values),
            )
            slider.on_changed(self._on_slider)
            self._sliders[var] = slider
            self._axes[var] = slider_ax

        if self._on_layout_change is not None:
            self._on_layout_change(len(self._sliders))
        self._update()

    def clear(self) -> None:
        """Disconnect and remove every slider of the panel."""
        for slider in self._sliders.values():
            slider.disconnect_events()
        for ax in self._axes.values():
            ax.remove()
        self._sliders = {}
        self._axes = {}
        self._values = {}

    def _on_slider(self, _value: float) -> None:
        """Redraw the plot after a slider moved.

        :param _value: new slider position (unused).
        :type _value: float
        """
        self._update()

    def selected_index(self, var: str) -> int:
        """Return the index the slider of a variable points at.

        :param var: name of the variable.
        :type var: str
        :return: the slider position, snapped to a valid index.
        :rtype: int
        """
        values = self._values[var]
        return int(np.clip(round(self._sliders[var].val), 0, values.size - 1))

    def selected_value(self, var: str) -> float:
        """Return the value the slider of a variable points at.

        :param var: name of the variable.
        :type var: str
        :return: the selected value.
        :rtype: float
        """
        return float(self._values[var][self.selected_index(var)])

    def selection(self) -> dict[str, float]:
        """Return the current selection, ready for :func:`slice_dataframe`.

        :return: the selected value for each variable of the panel.
        :rtype: dict[str, float]
        """
        return {var: self.selected_value(var) for var in self._sliders}

    @property
    def slider_vars(self) -> list[str]:
        """Return the names of the variables that currently have a slider.

        :return: the variable names, in slider order.
        :rtype: list[str]
        """
        return list(self._sliders)

    @property
    def sliders(self) -> dict[str, Slider]:
        """Return the live sliders, keyed by variable name.

        :return: the sliders of the panel.
        :rtype: dict[str, Slider]
        """
        return dict(self._sliders)
