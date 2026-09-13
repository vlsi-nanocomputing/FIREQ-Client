"""Export of experiment data."""

import itertools
import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


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

    # Load experiment summary
    try:
        with open(from_dir / "experiment_summary.yaml") as f:
            experiment_summary = yaml.unsafe_load(f)
    except FileNotFoundError:
        # no config = no experiment
        return

    var_order = experiment_summary.get("var_order")
    var_values = experiment_summary.get("var_values")

    for source_dir in from_dir.iterdir():
        if source_dir.is_dir():
            df = load_dataframe(var_order, var_values, source_dir)
            # save the entire dataframe, plus the config and end_message.json
            df.to_pickle(to_dir / f"data_{source_dir.name}.pkl")
            df.to_csv(to_dir / f"data_{source_dir.name}.csv")

    shutil.copy(from_dir / "experiment_summary.yaml", to_dir / "experiment_summary.yaml")


def load_dataframe(var_order: list[str], var_values: dict[str, np.ndarray], source_dir: Path) -> pd.DataFrame:
    """
    Load a dataframe from source_dir.

    The dataframe is the combination of all pieces found within the folder, which are named
    as data_x_y_..._z, where x,y,...,z are the variable indices and the variable names are defined
    by the var_order, where x is the first (outer loop) and z is the last (innermost loop)

    :param var_order: Order of variable names from outer to inner (left to right for the file names)
    :type var_order: list[str]
    :param var_values: Dictionary mapping variable name with its values
    :type var_values: dict[str, np.ndarray]
    :param source_dir: Directory where all data pieces are stored
    :type source_dir: Path
    :return: Whole dataframe as the combination of all pieces
    :rtype: DataFrame
    """
    # iterate over all variable combinations
    configs = []
    frames = []
    for point in itertools.product(*[range(len(var_values[v])) for v in var_order]):
        frame_name = f"data_{'_'.join(map(str, point))}"
        frame_df = pd.read_pickle(f"{source_dir}/{frame_name}.pkl")

        # Get the current var values, index from the checkpoint into the var values
        config = tuple(var_values[var][idx] for var, idx in zip(var_order, point, strict=True))
        configs.append(config)
        frames.append(frame_df)

    # Now build the complete DataFrame and return it
    return pd.concat(frames, keys=configs, names=var_order)
