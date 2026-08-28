"""Export of experiment data."""

import json
import os
import shutil
from pathlib import Path

import numpy as np

from .data import load_dataframe, load_dataframe_raw


def export(from_dir: Path, to_dir: Path) -> None:
    """Export an experiment (or a tree of experiments) to another directory.

    :param from_dir: source directory.
    :type from_dir: Path
    :param to_dir: destination directory.
    :type to_dir: Path
    """
    from_dir = Path(from_dir)
    to_dir = Path(to_dir)
    if from_dir.name.startswith("experiment_") and from_dir.name != "experiment_output":
        export_experiment(from_dir, to_dir)
    else:
        for dir in from_dir.iterdir():
            if dir.is_dir():
                export(dir, to_dir / dir.name)


def export_experiment(from_dir: Path, to_dir: Path) -> None:
    """Copy one experiment directory's data and config to the destination.

    :param from_dir: source experiment directory.
    :type from_dir: Path
    :param to_dir: destination directory.
    :type to_dir: Path
    """
    print(f"exporting {from_dir}   to   {to_dir} ......")
    os.makedirs(to_dir, exist_ok=True)

    # Load experiment configuration
    try:
        with open(from_dir / "config.json") as f:
            config = json.load(f)
    except FileNotFoundError:
        # no config = no experiment
        return

    # Load the variable order
    var_order = None
    try:
        with open(from_dir / "var_order.json") as f:
            var_order = json.load(f)
    except FileNotFoundError:
        pass

    if var_order is not None:
        var_names = [var_order[str(i)] for i in range(len(var_order))]
        var_values = {
            name: np.linspace(
                config["variables"][name]["start"],
                config["variables"][name]["stop"],
                config["variables"][name]["num"],
            )
            for name in var_names
        }
        df = load_dataframe(var_names, var_values, from_dir)
    else:
        df = load_dataframe_raw(from_dir)

    # save the entire dataframe, plus the config and end_message.json
    df.to_pickle(to_dir / "data.pkl")
    shutil.copy(from_dir / "config.json", to_dir / "config.json")
    if os.path.exists(from_dir / "end_message.json"):
        shutil.copy(from_dir / "end_message.json", to_dir / "end_message.json")
    if os.path.exists(from_dir / "a_figure.png"):
        shutil.copy(from_dir / "a_figure.png", to_dir / "a_figure.png")
