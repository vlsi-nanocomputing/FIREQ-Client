"""Spectroscopy comparison plot."""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from .common import get_var_names, load_exported_dataframe


def _plot_spectr(exp_0: str, exp_1: str, save: bool = False) -> None:
    """Compare two spectroscopy experiments and highlight the frequency of maximal distance.

    :param exp_0: directory of the first experiment.
    :type exp_0: str
    :param exp_1: directory of the second experiment.
    :type exp_1: str
    :param save: also save the figure as a_figure.png.
    :type save: bool
    """
    exp_path_0 = Path(exp_0)
    exp_path_1 = Path(exp_1)

    df_0 = load_exported_dataframe(exp_path_0)
    df_1 = load_exported_dataframe(exp_path_1)
    if df_0 is None or df_1 is None:
        return

    # Load experiment configuration
    with open(exp_path_0 / "config.json") as f:
        config_0 = json.load(f)
    with open(exp_path_1 / "config.json") as f:
        config_1 = json.load(f)

    if get_var_names(df_1) != get_var_names(df_0) or config_0["variables"] != config_1["variables"]:
        print("error, mismatch in variable configuration or var order")

    for config in [config_0, config_1]:
        if config["sys_config"]["/axisAcquisitionIP_0"]["$output_type"] != "accumulated":
            print("Experiment config contains variables or the value is not accumulated")
            return

    var_names = get_var_names(df_0)
    if not var_names:
        print("No sweep variables in the experiments; nothing to compare.")
        return
    var_values = {
        name: np.linspace(
            config_0["variables"][name]["start"],
            config_0["variables"][name]["stop"],
            config_0["variables"][name]["num"],
        )
        for name in var_names
    }

    # take the values
    iq_0: np.ndarray = df_0["value"].to_numpy()
    iq_1: np.ndarray = df_1["value"].to_numpy()

    # compute the point by point distance, take the maximum
    iq_distance = np.abs(iq_0 - iq_1)
    maxarg = np.argmax(iq_distance)

    plt.figure(figsize=(10, 5))
    plt.subplot(1, 2, 1)
    plt.scatter(iq_0.real, iq_0.imag, s=5)
    plt.scatter(iq_1.real, iq_1.imag, s=5)
    # plot the point with maximum distance
    frequency = var_values[var_names[0]]
    best_f = frequency[maxarg]
    plt.plot(
        [iq_0[maxarg].real, iq_1[maxarg].real],
        [iq_0[maxarg].imag, iq_1[maxarg].imag],
        color='red',
        marker='o',
        label=f"Frequency of maximal distance: {best_f:.4f}",
    )
    plt.title("Spectroscopy based frequency optimization")
    plt.xlabel("I values")
    plt.ylabel("Q values")
    plt.legend()

    # plot the two spectroscopies
    plt.subplot(1, 2, 2)
    plt.plot(frequency, np.abs(iq_0), label="Response at 0")
    plt.plot(frequency, np.abs(iq_1), label="Response at 1")
    plt.tight_layout()
    if save:
        plt.savefig(exp_path_0 / "a_figure.png")
    plt.show()
