"""Tests for the cell geometry of the heatmap plotter."""

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest
from matplotlib.collections import QuadMesh

from FIREQ_PLOTTER.plotting import plot_3d_heatmap
from FIREQ_PLOTTER.plotting.plot_3d_heatmap import _plot_3d_heatmap, cell_edges


@pytest.fixture(autouse=True)
def _no_show(monkeypatch):
    monkeypatch.setattr(plt, "show", lambda *args, **kwargs: None)
    yield
    plt.close("all")


def test_cell_edges_follow_uneven_centres():
    np.testing.assert_allclose(cell_edges(np.array([0, 1, 100])), [-0.5, 0.5, 50.5, 149.5])


def test_cell_edges_of_a_single_centre():
    np.testing.assert_allclose(cell_edges(np.array([3.0])), [2.5, 3.5])


def test_heatmap_draws_cells_at_their_coordinates():
    index = pd.MultiIndex.from_product([[0, 1, 100], [10, 20]], names=["x", "y"])
    df = pd.DataFrame({plot_3d_heatmap.VALUE_COLUMN: np.arange(6) + 1j}, index=index)
    handles = _plot_3d_heatmap([df], average_over=None)
    (mesh,) = [c for c in handles.axes.collections if isinstance(c, QuadMesh)]
    x_edges = mesh.get_coordinates()[0, :, 0]
    y_edges = mesh.get_coordinates()[:, 0, 1]
    np.testing.assert_allclose(x_edges, [5, 15, 25])
    np.testing.assert_allclose(y_edges, [-0.5, 0.5, 50.5, 149.5])
