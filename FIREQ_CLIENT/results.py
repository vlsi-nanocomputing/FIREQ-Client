"""Results returned by :class:`FIREQ_CLIENT.FireqClient`."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .errors import RunFailed
from .export import load_dataframe


@dataclass(frozen=True)
class ProgressEvent:
    """Progress of a running experiment, passed to the ``progress`` callback once per completed point."""

    point_index: int
    points_total: int
    #: value of each swept variable at this point, outer to inner
    coordinates: dict[str, Any]
    iteration_time_ns: int | None


@dataclass(frozen=True)
class PointResult:
    """The data of one completed point of a sweep, yielded by :meth:`FireqClient.run_iter`."""

    point_index: int
    #: index of each swept variable, outer to inner
    indices: tuple[int, ...]
    coordinates: dict[str, Any]
    #: shots of this point by acquisition source
    data: dict[str, pd.DataFrame]


@dataclass(frozen=True)
class ExperimentResult:
    """The outcome of a run that started (it got the experiment header), aborted or not."""

    name: str
    #: experiment directory, None when the run was not saved
    directory: Path | None
    #: outer to inner (["none"] for single runs)
    variable_order: list[str]
    variable_values: dict[str, list]
    points_expected: int
    points_completed: int
    aborted: bool
    server_errors: list[str]
    warnings: list[str]
    runtimes_ns: list[int]
    run_time_ns: int | None
    #: acquisition sources that produced data, as named in the directory (no "/")
    sources: list[str]
    _frames: dict[str, list[pd.DataFrame]] | None = field(default=None, repr=False, compare=False)

    def dataframe(self, source: str | None = None) -> pd.DataFrame:
        """Return the combined multi-index frame of one acquisition source, as ``export.load_dataframe`` does.

        :param source: acquisition source; may be omitted when the run has only one.
        :type source: str | None
        :raises ValueError: if the source is ambiguous or unknown.
        :return: the data, indexed by the swept variables, then shot and time.
        :rtype: pd.DataFrame
        """
        if source is None:
            if len(self.sources) != 1:
                raise ValueError(f"The run has the sources {self.sources}, choose one")
            source = self.sources[0]
        source = source.replace("/", "")
        if source not in self.sources:
            raise ValueError(f"Unknown source {source!r}, the run has {self.sources}")
        if self._frames is not None:
            frames = self._frames[source]
            return pd.concat(frames, keys=self._points()[: len(frames)], names=self.variable_order)
        if self.directory is None:
            raise ValueError("The run was neither saved nor kept in memory")
        return load_dataframe(self.variable_order, self.variable_values, self.directory / source)

    def dataframes(self) -> dict[str, pd.DataFrame]:
        """Return the frame of every acquisition source.

        :return: the frames keyed by source.
        :rtype: dict[str, pd.DataFrame]
        """
        return {source: self.dataframe(source) for source in self.sources}

    def raise_for_status(self) -> None:
        """Raise if the run was aborted or the server reported errors during it.

        :raises RunFailed: if the run failed.
        """
        if self.aborted or self.server_errors:
            reason = "; ".join(self.server_errors) or "aborted by the server"
            raise RunFailed(f"Run {self.name} failed: {reason}", self)

    def _points(self) -> list[tuple]:
        """Return the coordinates of every point, in run order.

        :return: one tuple of variable values per point.
        :rtype: list[tuple]
        """
        points = [()]
        for var in self.variable_order:
            points = [(*point, value) for point in points for value in self.variable_values[var]]
        return points


def load_experiment(path: str | Path) -> ExperimentResult:
    """Load an experiment directory saved by the client (not an exported one) as an :class:`ExperimentResult`.

    :param path: the ``experiment_<timestamp>`` directory.
    :type path: str | Path
    :return: the result, reading its data from disk.
    :rtype: ExperimentResult
    """
    directory = Path(path)
    with open(directory / "experiment_summary.yaml") as f:
        summary = yaml.unsafe_load(f)
    try:
        with open(directory / "runtimes.yaml") as f:
            runtimes = yaml.safe_load(f)
    except FileNotFoundError:
        # the client failed before the footer
        runtimes = {"runtimes": [], "run_time": None, "aborted": True}
    sources = sorted(child.name for child in directory.iterdir() if child.is_dir())
    return ExperimentResult(
        name=summary["experiment_name"],
        directory=directory,
        variable_order=summary["var_order"],
        variable_values=summary["var_values"],
        points_expected=summary["expected_iterations"],
        points_completed=len(runtimes["runtimes"]),
        aborted=runtimes["aborted"],
        server_errors=[],
        warnings=[],
        runtimes_ns=runtimes["runtimes"],
        run_time_ns=runtimes["run_time"],
        sources=sources,
    )
