"""Plot actions offered by the results browser.

Each action calls a function of :mod:`FIREQ_PLOTTER.plotting` (the same functions used by the
``run_plotter.py`` prompt). New plotting functions only need a new entry in :data:`ACTIONS`.

The plotter works on *exported* experiments (a single ``data.pkl``). Swept experiments written
by the client contain ``data_<i>.pkl`` files, so they are exported first (with
:func:`FIREQ_CLIENT.export.export_experiment`) into a mirror folder; this happens in the plot
process, see :mod:`FIREQ_GUI.tools.plot_runner`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExperimentInfo:
    """What the browser knows about an experiment folder.

    :param path: experiment folder.
    :type path: Path
    :param variables: sweep variables from ``config.json``.
    :type variables: dict
    :param output_type: output type of the first acquisition.
    :type output_type: str
    :param shots: shots per point.
    :type shots: int | None
    :param exported: True if the folder contains ``data.pkl`` (plotter input)
    :type exported: bool
    :param points: number of ``data_<idx>.pkl`` files.
    :type points: int
    """

    path: Path
    variables: dict = field(default_factory=dict)
    output_type: str = "accumulated"
    shots: int | None = None
    exported: bool = False  # contains data.pkl (plotter input)
    points: int = 0  # number of data_*.pkl files

    @property
    def n_vars(self) -> int:
        """Return the number of sweep variables.

        :return: the number of sweep variables.
        :rtype: int
        """
        return len(self.variables)

    def summary(self) -> str:
        """Return a one-line description.

        :return: output type, sweep, shots and export state.
        :rtype: str
        """
        if self.variables:
            sweep = ", ".join(f"{k} ({_count(v)})" for k, v in self.variables.items())
        else:
            sweep = "no sweep"
        state = "exported" if self.exported else f"{self.points} points, export needed"
        return f"{self.output_type} · {sweep} · {self.shots or '?'} shots · {state}"


def _count(spec: dict) -> int:
    """Return the number of points of a sweep variable description.

    :param spec: variable description (``lin``, ``list`` or ``const`` mode)
    :type spec: dict
    :return: the number of points.
    :rtype: int
    """
    mode = spec.get("mode", "lin")
    if mode == "list":
        return len(spec.get("values", []))
    if mode == "const":
        return 1
    return int(spec.get("num", 1))


def inspect_experiment(path: str | Path) -> ExperimentInfo | None:
    """Return information about an experiment folder (None if it is not one).

    :param path: folder to inspect.
    :type path: str | Path
    :return: the experiment information, or None if the folder has no readable ``config.json``.
    :rtype: ExperimentInfo | None
    """
    p = Path(path)
    # an experiment folder is any folder with a config.json (written by the client at the start)
    cfg = p / "config.json"
    if not p.is_dir() or not cfg.is_file():
        return None
    try:
        config = json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    sysc = config.get("sys_config", {})
    # the plotter reads the first acquisition node, so its output type decides the applicable plots
    acq = next((v for k, v in sysc.items() if k.startswith("/axisAcquisitionIP")), {})
    shots = sysc.get("$shots")
    return ExperimentInfo(
        path=p,
        variables=config.get("variables") or {},
        output_type=acq.get("$output_type", "accumulated"),
        shots=shots if isinstance(shots, int) else None,
        exported=(p / "data.pkl").is_file(),
        # "data_<idx>__<source>.pkl" files are additional acquisition sources, not extra points
        points=len([f for f in p.glob("data_*.pkl") if "__" not in f.name]),
    )


@dataclass
class PlotAction:
    """One entry of the context menu.

    :param label: text of the menu entry.
    :type label: str
    :param function: name of the function in :mod:`FIREQ_PLOTTER.plotting`.
    :type function: str
    :param kwargs: keyword arguments of the function.
    :type kwargs: dict
    :param n_dirs: number of experiments the function needs (1 or 2)
    :type n_dirs: int
    :param applies: tells whether the action applies to the selected experiments.
    :type applies: Callable[[list[ExperimentInfo]], bool]
    :param group: sub-menu name.
    :type group: str
    """

    label: str
    function: str  # name in FIREQ_PLOTTER.plotting
    kwargs: dict
    n_dirs: int  # experiments needed (1 or 2)
    applies: Callable[[list[ExperimentInfo]], bool]
    group: str = ""


def _raw(i: ExperimentInfo) -> bool:
    """Tell whether an experiment stores traces (raw or decimated output).

    :param i: experiment information.
    :type i: ExperimentInfo
    :return: True for ``raw``/``decimated`` output.
    :rtype: bool
    """
    return i.output_type in ("raw", "decimated")


def _line_ok(infos: list[ExperimentInfo]) -> bool:
    """Tell whether ``_plot_2d`` can plot the experiment.

    :param infos: the selected experiment (one)
    :type infos: list[ExperimentInfo]
    :return: True if applicable.
    :rtype: bool
    """
    i = infos[0]
    # traces are plotted against time; accumulated data needs a sweep variable for the x axis
    return _raw(i) or i.n_vars >= 1


def _heat_ok(infos: list[ExperimentInfo]) -> bool:
    """Tell whether ``_plot_3d_heatmap`` can plot the experiment.

    :param infos: the selected experiment (one)
    :type infos: list[ExperimentInfo]
    :return: True if applicable.
    :rtype: bool
    """
    i = infos[0]
    # a heatmap needs two axes: time x variable (traces) or variable x variable (accumulated)
    return (_raw(i) and i.n_vars >= 1) or (not _raw(i) and i.n_vars >= 2)


def _iq_ok(infos: list[ExperimentInfo]) -> bool:
    """Tell whether ``_plot_iq`` can compare the two experiments.

    :param infos: the two selected experiments.
    :type infos: list[ExperimentInfo]
    :return: True if both are accumulated and without sweep.
    :rtype: bool
    """
    return all(i.n_vars == 0 and i.output_type == "accumulated" for i in infos)


def _spectr_ok(infos: list[ExperimentInfo]) -> bool:
    """Tell whether ``_plot_spectr`` can compare the two experiments.

    :param infos: the two selected experiments.
    :type infos: list[ExperimentInfo]
    :return: True if both are swept over the same variables.
    :rtype: bool
    """
    return all(i.n_vars >= 1 for i in infos) and infos[0].variables.keys() == infos[1].variables.keys()


# Menu entries of the results browser. To expose a new FIREQ_PLOTTER function, add a line here:
# PlotAction(label, function name, keyword arguments, number of experiments, applicability rule, sub-menu)
ACTIONS: list[PlotAction] = [
    PlotAction("Line plot |S|", "_plot_2d", {}, 1, _line_ok, "Line plot"),
    PlotAction("Line plot I and Q", "_plot_2d", {"plot_magnitude": False, "plot_real": True, "plot_imag": True}, 1, _line_ok, "Line plot"),
    PlotAction("Line plot I", "_plot_2d", {"plot_magnitude": False, "plot_real": True}, 1, _line_ok, "Line plot"),
    PlotAction("Line plot Q", "_plot_2d", {"plot_magnitude": False, "plot_imag": True}, 1, _line_ok, "Line plot"),
    PlotAction("Heatmap |S|", "_plot_3d_heatmap", {}, 1, _heat_ok, "Heatmap"),
    PlotAction("Heatmap phase", "_plot_3d_heatmap", {"plot_magnitude": False, "plot_phase": True}, 1, _heat_ok, "Heatmap"),
    PlotAction("Heatmap I", "_plot_3d_heatmap", {"plot_magnitude": False, "plot_real": True}, 1, _heat_ok, "Heatmap"),
    PlotAction("Heatmap Q", "_plot_3d_heatmap", {"plot_magnitude": False, "plot_imag": True}, 1, _heat_ok, "Heatmap"),
    PlotAction("IQ clouds |0> vs |1>", "_plot_iq", {}, 2, _iq_ok, "Compare two experiments"),
    PlotAction("Spectra |0> vs |1>", "_plot_spectr", {}, 2, _spectr_ok, "Compare two experiments"),
]


def actions_for(infos: list[ExperimentInfo]) -> list[tuple[PlotAction, bool]]:
    """Return the actions matching the number of selected experiments, with their applicability.

    :param infos: selected experiments.
    :type infos: list[ExperimentInfo]
    :return: each action and whether it applies.
    :rtype: list[tuple[PlotAction, bool]]
    """
    # the menu shows every action for this number of experiments; non-applicable ones are greyed out
    return [(a, a.applies(infos)) for a in ACTIONS if a.n_dirs == len(infos)]


def export_target(info: ExperimentInfo, output_root: Path, export_root: Path) -> Path:
    """Return where an experiment is exported (mirror of its path below the output root).

    :param info: experiment to export.
    :type info: ExperimentInfo
    :param output_root: folder shown in the results browser.
    :type output_root: Path
    :param export_root: folder that receives the exported experiments.
    :type export_root: Path
    :return: the export folder.
    :rtype: Path
    """
    # mirror <output_root>/<name>/experiment_<ts> into <export_root>/<name>/experiment_<ts>
    try:
        rel = info.path.resolve().relative_to(output_root.resolve())
    except ValueError:
        # experiment outside the output root: export it by folder name
        rel = Path(info.path.name)
    return export_root / rel
