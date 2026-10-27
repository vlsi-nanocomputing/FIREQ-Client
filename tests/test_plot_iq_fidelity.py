"""Tests for the readout fidelity analysis of the IQ plotter."""

import logging
import math

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest

from FIREQ_PLOTTER.plotting import fidelity as fidelity_module
from FIREQ_PLOTTER.plotting import plot_iq
from FIREQ_PLOTTER.plotting.fidelity import compute_readout_fidelity, rotate_to_real_axis
from FIREQ_PLOTTER.plotting.plot_iq import _plot_iq_curve, _plot_rotated_cdf


@pytest.fixture(autouse=True)
def _no_show(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(plt, "show", lambda *args, **kwargs: None)
    yield
    plt.close("all")


def _clouds(n_0, n_1, distance=3.0, sigma=1.0, seed=0, centre=0j):
    rng = np.random.default_rng(seed)
    iq_0 = centre + sigma * (rng.standard_normal(n_0) + 1j * rng.standard_normal(n_0))
    iq_1 = centre + distance + sigma * (rng.standard_normal(n_1) + 1j * rng.standard_normal(n_1))
    return iq_0, iq_1


def _brute_force(iq_0, iq_1):
    """Every midpoint threshold, per-state error rates, maximum of 1 - eps_0 - eps_1."""
    rotated_0, rotated_1, _ = rotate_to_real_axis(iq_0, iq_1)
    x_0, x_1 = rotated_0.real, rotated_1.real
    pooled = np.unique(np.concatenate([x_0, x_1]))
    best = -np.inf
    for threshold in (pooled[:-1] + pooled[1:]) / 2:
        eps_0 = np.mean(x_0 > threshold)
        eps_1 = np.mean(x_1 <= threshold)
        best = max(best, 1 - eps_0 - eps_1)
    return best, x_0, x_1


def _frame(shots, sweep=None, times=1, seed=0, offset=0j):
    """A dataframe shaped like the client's export: levels none, [sweep], shot, time, a complex value column."""
    rng = np.random.default_rng(seed)
    sweep_levels = list(sweep.items()) if sweep else []
    levels = [[0], *(values for _, values in sweep_levels), range(shots), range(times)]
    names = ["none", *(name for name, _ in sweep_levels), "shot", "time"]
    index = pd.MultiIndex.from_product(levels, names=names)
    values = offset + rng.standard_normal(len(index)) + 1j * rng.standard_normal(len(index))
    return pd.DataFrame({"value": values}, index=index)


# 1. brute-force reference
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_matches_brute_force_reference(seed):
    iq_0, iq_1 = _clouds(700, 400, distance=2.0, seed=seed)
    result = compute_readout_fidelity(iq_0, iq_1)
    best, x_0, x_1 = _brute_force(iq_0, iq_1)

    assert result.separation == pytest.approx(best, abs=1e-12)
    # the error rates are the ones of a classification at the reported threshold
    assert result.eps_0 == np.mean(x_0 > result.threshold)
    assert result.eps_1 == np.mean(x_1 <= result.threshold)
    assert (result.n_0, result.n_1) == (700, 400)


# 2. identities
@pytest.mark.parametrize(("n_0", "n_1", "distance"), [(500, 500, 2.0), (800, 300, 1.0), (50, 900, 4.0)])
def test_identities(n_0, n_1, distance):
    result = compute_readout_fidelity(*_clouds(n_0, n_1, distance=distance, seed=3))

    assert result.separation == 1 - result.eps_0 - result.eps_1
    assert result.assignment_fidelity == 1 - (result.eps_0 + result.eps_1) / 2
    assert result.separation == pytest.approx(2 * result.assignment_fidelity - 1, abs=1e-12)
    assert 0.5 <= result.assignment_fidelity <= 1
    assert 0 <= result.separation <= 1


# 3. per-state average, not pooled accuracy
def test_unequal_counts_use_per_state_average():
    # 10 of the 100 shots of state 0 land on state 1, the 400 shots of state 1 are all right
    iq_0 = np.concatenate([np.zeros(90), np.ones(10)]).astype(complex)
    iq_1 = np.ones(400, dtype=complex)
    result = compute_readout_fidelity(iq_0, iq_1)

    pooled_accuracy = (90 + 400) / 500
    assert result.eps_0 == pytest.approx(0.1)
    assert result.eps_1 == 0
    assert result.assignment_fidelity == pytest.approx(0.95)
    assert result.assignment_fidelity != pytest.approx(pooled_accuracy)


# 4. agreement with the colleague's script for equal counts
def test_matches_pooled_accuracy_for_equal_counts():
    iq_0, iq_1 = _clouds(3000, 3000, distance=2.0, seed=4)
    result = compute_readout_fidelity(iq_0, iq_1)

    # pooled accuracy (correct_0 + correct_1) / (N_0 + N_1), maximised over the thresholds
    rotated_0, rotated_1, _ = rotate_to_real_axis(iq_0, iq_1)
    x_0, x_1 = rotated_0.real, rotated_1.real
    pooled = np.sort(np.concatenate([x_0, x_1]))
    thresholds = (pooled[:-1] + pooled[1:]) / 2
    correct_0 = np.searchsorted(np.sort(x_0), thresholds, side="right")
    correct_1 = x_1.size - np.searchsorted(np.sort(x_1), thresholds, side="right")
    accuracy = ((correct_0 + correct_1) / (x_0.size + x_1.size)).max()

    assert result.assignment_fidelity == pytest.approx(accuracy, abs=1e-12)


# 5. analytic check
def test_analytic_gaussians():
    distance = 3.0
    result = compute_readout_fidelity(*_clouds(20_000, 20_000, distance=distance, seed=5))
    expected = 0.5 * (1 + math.erf(distance / 2 / math.sqrt(2)))

    assert expected == pytest.approx(0.9332, abs=1e-4)
    assert result.assignment_fidelity == pytest.approx(expected, abs=0.01)


# 6. perfect separation
def test_perfect_separation():
    iq_0, iq_1 = _clouds(200, 300, distance=50.0, seed=6)
    result = compute_readout_fidelity(iq_0, iq_1)

    assert result.assignment_fidelity == 1.0
    assert result.separation == 1.0
    assert result.eps_0 == 0
    assert result.eps_1 == 0


# 7. invariance
@pytest.mark.parametrize(("angle", "offset"), [(0.7, 0j), (2.5, 1e3 - 4e2j), (-1.9, -35 + 12j)])
def test_rotation_and_offset_invariance(angle, offset):
    iq_0, iq_1 = _clouds(900, 600, distance=2.0, seed=7)
    reference = compute_readout_fidelity(iq_0, iq_1)
    moved = compute_readout_fidelity(iq_0 * np.exp(1j * angle) + offset, iq_1 * np.exp(1j * angle) + offset)

    assert moved.assignment_fidelity == pytest.approx(reference.assignment_fidelity, abs=1e-12)
    assert moved.separation == pytest.approx(reference.separation, abs=1e-12)
    assert moved.eps_0 == pytest.approx(reference.eps_0, abs=1e-12)
    assert moved.eps_1 == pytest.approx(reference.eps_1, abs=1e-12)


def test_swapping_states_swaps_error_rates():
    iq_0, iq_1 = _clouds(900, 600, distance=2.0, seed=8)
    reference = compute_readout_fidelity(iq_0, iq_1)
    swapped = compute_readout_fidelity(iq_1, iq_0)

    assert swapped.assignment_fidelity == pytest.approx(reference.assignment_fidelity, abs=1e-12)
    assert swapped.separation == pytest.approx(reference.separation, abs=1e-12)
    assert swapped.eps_0 == pytest.approx(reference.eps_1, abs=1e-12)
    assert swapped.eps_1 == pytest.approx(reference.eps_0, abs=1e-12)
    assert (swapped.n_0, swapped.n_1) == (reference.n_1, reference.n_0)


def test_identical_values_and_equal_means():
    # every shot on the same value: the threshold is that value, every shot is read as 0
    result = compute_readout_fidelity(np.full(5, 2 + 1j), np.full(7, 2 + 1j))
    assert result.threshold == 2.0
    assert (result.eps_0, result.eps_1) == (0.0, 1.0)
    assert result.separation == 0.0
    assert result.rotation_angle_rad == 0.0

    # mirrored clouds share their mean: no rotation
    iq = np.array([-1, 1, -2, 2], dtype=complex)
    assert compute_readout_fidelity(iq, -iq).rotation_angle_rad == 0.0

    with pytest.raises(ValueError):
        compute_readout_fidelity(np.array([], dtype=complex), iq)


def test_rotated_cdf_returns_fidelity_and_logs_both_quantities(caplog):
    iq_0, iq_1 = _clouds(400, 400, seed=9)
    df_0 = pd.DataFrame({"value": iq_0})
    df_1 = pd.DataFrame({"value": iq_1})
    fig, result = _plot_rotated_cdf(df_0, df_1, ipname="ip")

    assert fig is not None
    assert result == compute_readout_fidelity(iq_0, iq_1)
    line = caplog.messages[-1]
    assert line.startswith("readout fidelity ip: assignment 1-(ε0+ε1)/2 = ")
    assert "| separation 1-ε0-ε1 = " in line
    assert "| shots 400/400" in line
    labels = [text.get_text() for text in fig.axes[1].get_legend().get_texts()]
    assert f"assignment {100 * result.assignment_fidelity:.2f}% / separation {100 * result.separation:.2f}%" in labels

    assert _plot_rotated_cdf(df_0.iloc[:1], df_1) == (None, None)


# 8. single-run datasets, with the default average_over="shot"
def test_single_run_datasets_plot_clouds_and_use_every_shot(monkeypatch, caplog):
    shots = 1000
    df_0 = _frame(shots, seed=10)
    df_1 = _frame(shots, seed=11, offset=3)

    calls = []

    def spy(iq_0, iq_1):
        result = fidelity_module.compute_readout_fidelity(iq_0, iq_1)
        calls.append(result)
        return result

    monkeypatch.setattr(plot_iq, "compute_readout_fidelity", spy)
    handles = _plot_iq_curve([df_0, df_1])

    assert len(calls) == 1
    assert (calls[0].n_0, calls[0].n_1) == (shots, shots)
    assert f"shots {shots}/{shots}" in caplog.text
    assert handles.cdf.fidelity == calls[0]
    # the raw shot clouds are scattered, with no selector and no slider
    assert [len(collection.get_offsets()) for collection in handles.axes.collections] == [shots, shots]
    assert handles.controls is None
    assert handles.radios == {}
    assert handles.panel.slider_vars == []


# 9. sweep datasets
def test_sweep_datasets_skip_or_reject(caplog):
    sweep = {"amp": [0.1, 0.2, 0.3]}
    dataframes = [_frame(50, sweep=sweep, seed=12), _frame(50, sweep=sweep, seed=13, offset=3)]

    _plot_iq_curve(dataframes, ipname="ip")
    out = caplog.text
    assert "readout fidelity ip: skipped" in out
    assert "amp" in out
    assert "assignment" not in out

    with pytest.raises(ValueError, match="amp"):
        _plot_iq_curve(dataframes, ipname="ip", cdf_analysis=True)


# 10. time traces
def test_time_traces_skip_or_reject(caplog):
    dataframes = [_frame(50, times=4, seed=14), _frame(50, times=4, seed=15, offset=3)]

    _plot_iq_curve(dataframes, ipname="ip")
    out = caplog.text
    assert "readout fidelity ip: skipped" in out
    assert "4 samples per shot" in out

    with pytest.raises(ValueError, match="one sample per shot"):
        _plot_iq_curve(dataframes, ipname="ip", cdf_analysis=True)
