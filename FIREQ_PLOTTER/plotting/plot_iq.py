"""IQ plane comparison plot."""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from .common import load_exported_dataframe


def _plot_iq(exp_0: str, exp_1: str, save: bool = False) -> None:
    """Plot the IQ plane of two experiments and the rotated CDF analysis.

    :param exp_0: directory of the first experiment.
    :type exp_0: str
    :param exp_1: directory of the second experiment.
    :type exp_1: str
    :param save: also save the figures.
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

    for config in [config_0, config_1]:
        if config["variables"] or config["sys_config"]["/axisAcquisitionIP_0"]["$output_type"] != "accumulated":
            print("Experiment config contains variables or the value is not accumulated")
            return

    iq_0: np.ndarray = df_0["value"].to_numpy()
    iq_1: np.ndarray = df_1["value"].to_numpy()
    plt.scatter(iq_0.real, iq_0.imag, s=5)
    plt.scatter(iq_1.real, iq_1.imag, s=5)
    if save:
        plt.savefig(exp_path_0 / "a_figure.png")
    plt.show()

    # define the mean value for 0 and 1
    mean_0 = iq_0.mean()
    mean_1 = iq_1.mean()

    # find the middle point between the two means
    mid_point = (mean_0 + mean_1) / 2

    # rotate around the middle point and move back
    angle = np.angle(mean_1 - mean_0)

    # Rotate the data so that the line joining the two means is parallel to the X axis
    iq_0_rot: np.ndarray = (iq_0 - mid_point) * np.exp(-1j * angle) + mid_point
    iq_1_rot: np.ndarray = (iq_1 - mid_point) * np.exp(-1j * angle) + mid_point

    mean_0_rot = (mean_0 - mid_point) * np.exp(-1j * angle) + mid_point
    mean_1_rot = (mean_1 - mid_point) * np.exp(-1j * angle) + mid_point

    # plot a scatter to show the data, show the two means and a line connecting them
    plt.figure(figsize=(10, 5))
    plt.subplot(1, 2, 1)
    plt.scatter(iq_0_rot.real, iq_0_rot.imag, s=5, alpha=0.5, label="Rotated 0")
    plt.scatter(iq_1_rot.real, iq_1_rot.imag, s=5, alpha=0.5, label="Rotated 1")
    plt.plot(
        [mean_0_rot.real, mean_1_rot.real],
        [mean_0_rot.imag, mean_1_rot.imag],
        color="red",
        marker="o",
        label="Means Line",
    )
    plt.title("Rotated IQ Plane")
    plt.xlabel("I (Real)")
    plt.ylabel("Q (Imag)")
    plt.legend()
    plt.grid(True)

    # consider only the values on the x axis, create cumulative distribution
    # for both, plot the two cumulative distributions
    plt.subplot(1, 2, 2)
    # Take only the X (real) component after the rotation
    x_0 = iq_0_rot.real
    x_1 = iq_1_rot.real

    # get the limits of the plot
    x_max = max(x_0.max(), x_1.max())
    x_min = min(x_0.min(), x_1.min())

    # create a linspace for the x axis of the CDF
    # 1. Sort your input arrays first (required for searchsorted)
    x_0_sorted = np.sort(x_0)
    x_1_sorted = np.sort(x_1)

    # 2. Create your grid
    x_cdf = np.linspace(x_min, x_max, endpoint=True, num=(x_0.shape[0] + x_1.shape[0]))

    # 3. Use searchsorted to find how many elements are strictly less than each point in x_cdf
    # 'left' ensures we find the index where x_0_sorted < x_cdf[i]
    x_0_cdf = np.searchsorted(x_0_sorted, x_cdf, side="left")
    x_1_cdf = np.searchsorted(x_1_sorted, x_cdf, side="left")
    # normalize to 1
    x_0_cdf = x_0_cdf / x_0.shape[0]
    x_1_cdf = x_1_cdf / x_1.shape[0]

    # find where the two distances are maximized
    x_cdf_abs_diff = np.abs(x_0_cdf - x_1_cdf)
    threshold = np.argmax(x_cdf_abs_diff)
    fidelity = 100 * x_cdf_abs_diff[threshold]

    print(f"maximum fidelity with threshold: {threshold} is equal to: {fidelity:.2f}%")

    # Plot the two cumulative distributions (CDF)
    plt.plot(x_cdf, x_0_cdf, label="CDF 0", linewidth=1.5)
    plt.plot(x_cdf, x_1_cdf, label="CDF 1", linewidth=1.5)
    plt.plot(
        [x_cdf[threshold]] * 2,
        [x_0_cdf[threshold], x_1_cdf[threshold]],
        color="red",
        marker="o",
        label=f"Point of maximum fidelity: {fidelity:.2f}%",
    )
    plt.title("Cumulative Distribution (X-axis)")
    plt.xlabel("Projected X value")
    plt.ylabel("Probability")
    plt.legend()
    plt.grid(True)

    plt.tight_layout()
    if save:
        plt.savefig(exp_path_0 / "rotated_analysis.png")
    plt.show()
