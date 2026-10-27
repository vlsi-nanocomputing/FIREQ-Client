"""Turn the inputs of the plot functions into named datasets."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import pandas as pd

from FIREQ_CLIENT import load_experiment as load_saved_experiment

from .plotting.common import load_exported_dataframes, unique_names

DEFAULT_IP = "default"


def load_experiment(path: str | os.PathLike) -> dict[str, pd.DataFrame]:
    """Load the dataframes of an experiment directory, by acquisition IP.

    Both an exported directory (``data_<ip>.pkl`` files) and a directory saved by the client
    (``experiment_summary.yaml`` and one sub-directory per IP) are accepted.

    :param path: experiment directory.
    :type path: str | os.PathLike
    :raises ValueError: if the directory holds no experiment data.
    :return: the dataframes keyed by acquisition IP.
    :rtype: dict[str, pd.DataFrame]
    """
    directory = Path(path).expanduser()
    if not directory.is_dir():
        raise ValueError(f"not a directory: {directory}")
    frames = load_exported_dataframes(directory)
    if not frames and (directory / "experiment_summary.yaml").exists():
        frames = load_saved_experiment(directory).dataframes()
    if not frames:
        raise ValueError(f"no data_<ipname>.pkl files found in {directory}")
    return frames


def normalise(data: object) -> list[tuple[str, dict[str, pd.DataFrame]]]:
    """Turn any accepted plot input into (name, dataframes by IP) pairs.

    Accepted: an experiment directory, an ``ExperimentResult``, a DataFrame (one acquisition IP), a dict of
    DataFrames by IP, or a list or tuple of those.

    :param data: the input.
    :type data: object
    :raises TypeError: if the input is of none of these types.
    :return: one pair per experiment, with unique names.
    :rtype: list[tuple[str, dict[str, pd.DataFrame]]]
    """
    items = list(data) if isinstance(data, list | tuple) else [data]
    if not items:
        raise ValueError("Nothing to plot")
    datasets = []
    for index, item in enumerate(items):
        if isinstance(item, str | os.PathLike):
            datasets.append((Path(item).name, load_experiment(item)))
        elif isinstance(item, pd.DataFrame):
            datasets.append((f"dataset {index}", {DEFAULT_IP: item}))
        elif isinstance(item, Mapping):
            datasets.append((f"dataset {index}", dict(item)))
        elif hasattr(item, "dataframes"):  # an ExperimentResult
            datasets.append((item.name, item.dataframes()))
        else:
            raise TypeError(f"Cannot plot a {type(item).__name__}")
    names = unique_names([name for name, _ in datasets])
    return [(name, frames) for name, (_, frames) in zip(names, datasets, strict=True)]
