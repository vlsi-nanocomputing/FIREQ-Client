"""Tests of the scriptable plotter API under the Agg backend."""

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest

from FIREQ_PLOTTER import load_experiment, plot_2d, plot_3d_heatmap, plot_iq, rotated_cdf
from FIREQ_PLOTTER.plotting import common


@pytest.fixture(autouse=True)
def _no_show(monkeypatch):
    shown = []
    monkeypatch.setattr(plt, "show", lambda *args, **kwargs: shown.append(kwargs))
    yield shown
    plt.close("all")


def _sweep_frame(offset=0.0):
    index = pd.MultiIndex.from_product([[1.0, 2.0, 3.0], [10.0, 20.0], range(4), [0]], names=["a", "b", "shot", "time"])
    a = index.get_level_values("a").to_numpy()
    b = index.get_level_values("b").to_numpy()
    return pd.DataFrame({"value": (a + 1j * b + offset).astype(complex)}, index=index)


def _shots(offset, seed):
    rng = np.random.default_rng(seed)
    index = pd.MultiIndex.from_product([[0], range(200), [0]], names=["none", "shot", "time"])
    return pd.DataFrame({"value": offset + rng.standard_normal(200) + 1j * rng.standard_normal(200)}, index=index)


def test_plot_2d_presets_without_showing(_no_show):
    handles = plot_2d(_sweep_frame(), ip="default", quantity="real", x="b", selection={"a": 2.9}, show=False)
    assert _no_show == []
    assert handles.panel.selection() == {"a": 3.0}
    (line,) = [line for line in handles.axes.lines if line.get_visible()]
    assert line.get_xdata().tolist() == [10.0, 20.0]
    assert line.get_ydata().tolist() == [3.0, 3.0]
    assert handles.radios["x-axis"].value_selected == "b"


def test_static_plots_have_no_widgets():
    (handles,) = plot_2d({"acq": _sweep_frame()}, interactive=False, show=False, selection={"a": 2.0})
    assert handles.controls is None
    assert handles.panel.sliders == {}
    assert handles.panel.selection() == {"a": 2.0}
    handles.figure.canvas.draw()


def test_heatmap_and_list_of_inputs_show_once(_no_show):
    handles = plot_3d_heatmap([_sweep_frame(), _sweep_frame(1.0)], x="a", y="b", dataset=1, block=False)
    assert _no_show == [{"block": False}]
    assert len(handles) == 1
    assert handles[0].axes.get_xlabel() == "a"
    assert handles[0].radios["dataset"].value_selected == "dataset 1"


def test_invalid_presets_raise():
    with pytest.raises(ValueError, match="quantity"):
        plot_2d(_sweep_frame(), quantity="bogus", show=False)
    with pytest.raises(ValueError, match="Unknown IP"):
        plot_2d(_sweep_frame(), ip="acq9", show=False)
    with pytest.raises(TypeError):
        plot_2d(42, show=False)


def test_plot_iq_returns_the_cdf_without_waiting():
    (handles,) = plot_iq([_shots(0, 1), _shots(4, 2)], show=False)
    assert handles.cdf.fidelity.assignment_fidelity > 0.9
    assert handles.cdf.figure is not None


def test_rotated_cdf_without_figure():
    result = rotated_cdf(_shots(0, 1), _shots(4, 2), plot=False)
    assert result.figure is None
    assert result.fidelity.n_0 == 200


def test_handles_registry_drops_closed_figures():
    handles = plot_2d(_sweep_frame(), ip="default", show=False)
    number = handles.figure.number
    assert common._OPEN_PLOTS[number] is handles
    plt.close(handles.figure)
    plot_2d(_sweep_frame(), ip="default", show=False)
    assert handles not in common._OPEN_PLOTS.values()


def test_load_exported_and_saved_directories(tmp_path):
    exported = tmp_path / "exported"
    exported.mkdir()
    _sweep_frame().to_pickle(exported / "data_acq0.pkl")
    assert list(load_experiment(exported)) == ["acq0"]
    (handles,) = plot_2d(exported, show=False)
    assert handles.axes.get_title() == "acq0"
    with pytest.raises(ValueError, match="no data"):
        load_experiment(tmp_path)
