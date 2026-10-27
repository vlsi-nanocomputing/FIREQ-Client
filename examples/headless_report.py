"""Save static plots of exported experiments as PNG files, without a display."""

import sys

import matplotlib

matplotlib.use("Agg")

from FIREQ_PLOTTER import plot_2d  # noqa: E402 - the backend must be chosen first

for exp_dir in sys.argv[1:]:
    for handles in plot_2d(exp_dir, interactive=False, show=False):
        handles.figure.savefig(f"{handles.axes.get_title()}.png", dpi=150)
