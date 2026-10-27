"""On-disk layout of the experiment data.

``<output_dir>/<name>/experiment_<timestamp>/`` holds ``experiment_summary.yaml``, ``runtimes.yaml`` and one
sub-directory per acquisition source with a ``data_<i>_<j>.pkl``/``.csv`` pair per point.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd
import yaml


class ExperimentWriter:
    """Write the data of one run in a new timestamped experiment directory."""

    def __init__(self, output_dir: str | os.PathLike, name: str) -> None:
        """Create the experiment directory.

        :param output_dir: root of the experiment data.
        :type output_dir: str | os.PathLike
        :param name: experiment name, the parent of the timestamped directory.
        :type name: str
        """
        base_name = Path(output_dir) / name / f"experiment_{time.strftime('%Y%m%d_%H%M%S')}"
        # two runs can start within the same second: suffix the directory until one is free
        candidate = base_name
        index = 1
        while True:
            try:
                os.makedirs(candidate)
                break
            except FileExistsError:
                candidate = Path(f"{base_name}_{index}")
                index += 1
        self.directory = candidate

    def save_dict(self, d: dict, file_name: str) -> None:
        """Save a dict as YAML in the experiment directory.

        :param d: dict to save.
        :type d: dict
        :param file_name: name of the YAML file (without extension).
        :type file_name: str
        """
        with open(self.directory / f"{file_name}.yaml", "w") as f:
            yaml.dump(d, f, indent=2)

    def save_point(self, source: str, indices: tuple[int, ...], df: pd.DataFrame) -> None:
        """Save the data of one point of one acquisition source, as pickle and CSV.

        :param source: acquisition source directory name.
        :type source: str
        :param indices: index of each swept variable, outer to inner.
        :type indices: tuple[int, ...]
        :param df: data of the point.
        :type df: pd.DataFrame
        """
        directory = self.directory / source
        directory.mkdir(exist_ok=True)
        file_name = f"data_{'_'.join(map(str, indices))}"
        df.to_pickle(directory / f"{file_name}.pkl")
        df.to_csv(directory / f"{file_name}.csv")
