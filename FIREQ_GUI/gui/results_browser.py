"""Results browser: a file explorer of the acquired data with right-click plotting."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ..core.plot_actions import ExperimentInfo, PlotAction, actions_for, export_target, inspect_experiment
from .qt import (
    QAbstractItemView,
    QDesktopServices,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPoint,
    QProgressBar,
    QPushButton,
    QUrl,
    QVBoxLayout,
    QWidget,
    Qt,
    pyqtSignal,
)

# qt classes not re-exported by .qt, imported directly from PyQt6
from PyQt6.QtCore import QDir, QModelIndex, QProcess, QProcessEnvironment, QSortFilterProxyModel  # noqa: E402
from PyQt6.QtGui import QFileSystemModel  # noqa: E402
from PyQt6.QtWidgets import QTreeView  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]  # folder containing FIREQ_CLIENT and FIREQ_PLOTTER


class _NewestFirst(QSortFilterProxyModel):
    """Folders sorted by modification time (newest first), files by name."""

    def lessThan(self, a: QModelIndex, b: QModelIndex) -> bool:  # noqa: N802
        """Compare two entries.

        Folders come first, newest first; files are sorted by name.

        :param a: left index.
        :type a: QModelIndex
        :param b: right index.
        :type b: QModelIndex
        :return: True if ``a`` comes before ``b``.
        :rtype: bool
        """
        # the proxy compares source indexes, so ask the file-system model
        src = self.sourceModel()
        da, db = src.isDir(a), src.isDir(b)
        if da != db:
            return da  # folders first
        if da:
            # newest folder first, so the last experiment is on top
            return src.lastModified(a) > src.lastModified(b)
        return src.fileName(a).lower() < src.fileName(b).lower()


class ResultsBrowser(QWidget):
    """Shows ``experiment_output`` as a tree; right-click an experiment to plot or export it."""

    openInTimeline = pyqtSignal(str)  # noqa: N815 - path of a config.json
    log = pyqtSignal(str, str)  # level, text
    rootChanged = pyqtSignal(str)  # noqa: N815

    def __init__(self, root: str, export_root: str, parent: QWidget | None = None) -> None:
        """Build the browser rooted at the output folder.

        :param root: output folder (created if missing)
        :type root: str
        :param export_root: folder that receives exported experiments.
        :type export_root: str
        :param parent: parent widget.
        :type parent: QWidget | None
        """
        super().__init__(parent)
        self.root = Path(root)
        self.export_root = Path(export_root)
        self.root.mkdir(parents=True, exist_ok=True)
        # running plot/export processes, kept referenced until they finish
        self.processes: list[QProcess] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        # top row: current data folder and a button to change it
        top = QHBoxLayout()
        self.path_label = QLabel()
        self.path_label.setStyleSheet("color:#5b6472;")
        self.path_label.setWordWrap(True)
        top.addWidget(self.path_label, 1)
        change = QPushButton("Folder…")
        change.setToolTip("Choose the folder with the acquired data")
        change.clicked.connect(self._choose_root)
        top.addWidget(change)
        lay.addLayout(top)

        # run status and progress of the experiment being acquired
        self.status = QLabel("Idle")
        self.status.setStyleSheet("font-weight:600;")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setMaximumHeight(14)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        lay.addWidget(self.progress)

        # file-system model limited to folders and the data/figure/config files
        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.Filter.AllDirs | QDir.Filter.Files | QDir.Filter.NoDotAndDotDot)
        self.model.setNameFilters(["*.json", "*.pkl", "*.png", "*.yaml"])
        # hide non-matching files instead of showing them greyed out
        self.model.setNameFilterDisables(False)
        # sorted through the proxy: newest experiment folders first
        self.proxy = _NewestFirst(self)
        self.proxy.setSourceModel(self.model)
        self.tree = QTreeView()
        self.tree.setModel(self.proxy)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        # keep only name and date columns (hide size and type)
        for col in (1, 2):
            self.tree.hideColumn(col)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, header.ResizeMode.Stretch)
        header.setSectionResizeMode(3, header.ResizeMode.ResizeToContents)
        # multi-selection is used to compare two experiments
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.doubleClicked.connect(self._double_clicked)
        self.tree.selectionModel().selectionChanged.connect(lambda *_: self._show_info())
        self.tree.setToolTip("Right-click an experiment folder to plot or export it.\nSelect two folders (Ctrl+click) to compare them.")
        lay.addWidget(self.tree, 1)

        # summary of the selection below the tree
        self.info = QLabel("Right-click an experiment to plot it.\nCtrl+click two experiments to compare them.")
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color:#5b6472; padding: 4px; background:#f4f5f7; border-radius: 3px;")
        lay.addWidget(self.info)
        self.set_root(self.root)

    # ------------------------------------------------------------------ folders
    def set_root(self, root: Path) -> None:
        """Show another output folder.

        :param root: output folder.
        :type root: str | Path
        """
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # setRootPath starts watching the folder; map its index to the proxy for the view
        idx = self.model.setRootPath(str(self.root))
        self.tree.setRootIndex(self.proxy.mapFromSource(idx))
        self.path_label.setText(str(self.root))

    def _choose_root(self) -> None:
        """Ask for another output folder."""
        d = QFileDialog.getExistingDirectory(self, "Folder with the acquired data", str(self.root))
        if d:
            self.set_root(Path(d))
            # exported data goes next to the new data folder
            self.export_root = Path(d).parent / "exported"
            self.rootChanged.emit(d)

    def reveal(self, path: str) -> None:
        """Select a folder (e.g. the experiment that just finished).

        :param path: folder to select ('' does nothing)
        :type path: str
        """
        if not path:
            return
        # try the resolved path first, then the path as given (symlinks)
        idx = self.model.index(str(Path(path).resolve()))
        if not idx.isValid():
            idx = self.model.index(str(Path(path)))
        if idx.isValid():
            # select and scroll to the folder in the sorted view
            pidx = self.proxy.mapFromSource(idx)
            self.tree.expand(pidx.parent())
            self.tree.setCurrentIndex(pidx)
            self.tree.scrollTo(pidx)

    def _path(self, pidx: QModelIndex) -> Path:
        """Return the file path of a tree index.

        :param pidx: index of the sorted model.
        :type pidx: QModelIndex
        :return: the path.
        :rtype: Path
        """
        return Path(self.model.filePath(self.proxy.mapToSource(pidx)))

    def selected_paths(self) -> list[Path]:
        """Return the selected files/folders (column 0 only, in selection order).

        :return: the selected paths.
        :rtype: list[Path]
        """
        return [self._path(i) for i in self.tree.selectionModel().selectedRows(0)]

    def selected_experiments(self) -> list[ExperimentInfo]:
        """Return the selected experiment folders.

        :return: information about the selected experiment folders.
        :rtype: list[ExperimentInfo]
        """
        infos = [inspect_experiment(p) for p in self.selected_paths()]
        return [i for i in infos if i is not None]

    # ------------------------------------------------------------------ run status
    def run_started(self, info: dict) -> None:
        """Show the running experiment.

        :param info: run information.
        :type info: dict
        """
        self.status.setText(f"Running {info.get('name')}: {info.get('total_points')} points × {info.get('shots')} shots")
        self.progress.setVisible(True)
        self.progress.setValue(0)

    def run_progress(self, done: int, total: int) -> None:
        """Update the progress bar.

        :param done: shots received.
        :type done: int
        :param total: shots expected.
        :type total: int
        """
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)

    def run_finished(self, exp_dir: str, ok: bool, message: str) -> None:
        """Show the result and select its folder.

        :param exp_dir: output folder.
        :type exp_dir: str
        :param ok: True if completed.
        :type ok: bool
        :param message: result.
        :type message: str
        """
        self.progress.setVisible(False)
        self.status.setText(("✔ " if ok else "✖ ") + message + (f": {Path(exp_dir).name}" if exp_dir else ""))
        self.reveal(exp_dir)

    # ------------------------------------------------------------------ info / menu
    def _show_info(self) -> None:
        """Describe the selection below the tree."""
        exps = self.selected_experiments()
        if len(exps) == 1:
            self.info.setText(f"<b>{exps[0].path.parent.name}/{exps[0].path.name}</b><br>{exps[0].summary()}")
        elif len(exps) == 2:
            self.info.setText("Two experiments selected: right-click to compare them.")
        else:
            self.info.setText("Right-click an experiment to plot it.\nCtrl+click two experiments to compare them.")

    def _context_menu(self, pos: QPoint) -> None:
        """Show the plot/export menu for the selection.

        :param pos: position in the tree viewport.
        :type pos: QPoint
        """
        # right-click on an unselected item selects it, like a file manager
        pidx = self.tree.indexAt(pos)
        if pidx.isValid() and not self.tree.selectionModel().isSelected(pidx):
            self.tree.setCurrentIndex(pidx)
        paths = self.selected_paths()
        exps = self.selected_experiments()
        menu = QMenu(self)
        if exps and len(exps) <= 2:
            # one submenu per action group; actions with no group go in the top menu
            groups: dict[str, QMenu] = {}
            for action, ok in actions_for(exps):
                if action.group not in groups:
                    groups[action.group] = menu.addMenu(action.group) if action.group else menu
                # bind the action now so each menu item plots its own action
                act = groups[action.group].addAction(action.label, lambda a=action: self.plot(a, exps))
                act.setEnabled(ok)
                if not ok:
                    act.setToolTip("Not applicable to this experiment (sweep / output type)")
            menu.addSeparator()
            # checkable option shared by all plot actions
            save = menu.addAction("Save figures in the experiment folder")
            save.setCheckable(True)
            save.setChecked(getattr(self, "_save_figures", False))
            save.toggled.connect(lambda v: setattr(self, "_save_figures", v))
        # single experiment: export and open its configuration
        if len(exps) == 1:
            e = exps[0]
            menu.addSeparator()
            if not e.exported:
                menu.addAction("Export (data.pkl for the plotter)", lambda: self.export(e))
            menu.addAction("Open configuration in the timeline", lambda: self.openInTimeline.emit(str(e.path / "config.json")))
        # single file or folder: open it with the system application
        if len(paths) == 1:
            p = paths[0]
            if p.suffix.lower() in (".png", ".json", ".yaml"):
                menu.addAction("Open file", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(p))))
            menu.addAction("Show in file manager", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(p if p.is_dir() else p.parent))))
        if not menu.isEmpty():
            menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _double_clicked(self, pidx: QModelIndex) -> None:
        """Plot an experiment with its first applicable action, or open a file.

        :param pidx: double-clicked index.
        :type pidx: QModelIndex
        """
        p = self._path(pidx)
        info = inspect_experiment(p)
        if info is None:
            if p.is_file() and p.suffix.lower() in (".png", ".json", ".yaml"):
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
            return
        # double-click runs the first plot action that applies
        first = next((a for a, ok in actions_for([info]) if ok), None)
        if first is not None:
            self.plot(first, [info])

    # ------------------------------------------------------------------ plotting / export
    def _request(self, function: str | None, exps: list[ExperimentInfo], kwargs: dict) -> dict:
        """Build the request for :mod:`FIREQ_GUI.tools.plot_runner`.

        :param function: plotting function name.
        :type function: str | None
        :param exps: experiments to plot.
        :type exps: list[ExperimentInfo]
        :param kwargs: keyword arguments of the function.
        :type kwargs: dict
        :return: the request.
        :rtype: dict
        """
        return {
            "function": function,
            "dirs": [str(e.path) for e in exps],
            # data not yet exported is exported first by the plot runner
            "export_to": [None if e.exported else str(export_target(e, self.root, self.export_root)) for e in exps],
            "kwargs": kwargs,
        }

    def plot(self, action: PlotAction, exps: list[ExperimentInfo]) -> QProcess:
        """Run a plot action in a separate process (non-blocking).

        :param action: plot action.
        :type action: PlotAction
        :param exps: experiments to plot.
        :type exps: list[ExperimentInfo]
        :return: the plot process.
        :rtype: QProcess
        """
        kwargs = dict(action.kwargs)
        # the save option asks the plotting function to save its figures
        if getattr(self, "_save_figures", False):
            kwargs["save"] = True
        req = self._request(action.function, exps, kwargs)
        self.log.emit("info", f"plot: {action.label} on {', '.join(e.path.name for e in exps)}")
        # the plot runner reads the request as a JSON command-line argument
        return self._start(["-m", "FIREQ_GUI.tools.plot_runner", json.dumps(req)], action.label)

    def export(self, info: ExperimentInfo) -> QProcess:
        """Export an experiment into the export folder (same layout as the client 'export' command).

        :param info: experiment to export.
        :type info: ExperimentInfo
        :return: the export process.
        :rtype: QProcess
        """
        dst = export_target(info, self.root, self.export_root)
        # run the client export in a child process to keep the GUI responsive
        code = "import sys; from pathlib import Path; from FIREQ_CLIENT.export import export_experiment; export_experiment(Path(sys.argv[1]), Path(sys.argv[2]))"
        self.log.emit("info", f"export {info.path} -> {dst}")
        return self._start(["-c", code, str(info.path), str(dst)], "export")

    def _start(self, args: list[str], label: str) -> QProcess:
        """Start a Python process with the repository on ``PYTHONPATH``.

        :param args: interpreter arguments.
        :type args: list[str]
        :param label: name used in log messages.
        :type label: str
        :return: the process.
        :rtype: QProcess
        """
        proc = QProcess(self)
        # child python with the repository on PYTHONPATH so FIREQ_CLIENT and FIREQ_PLOTTER import
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONPATH", os.pathsep.join(filter(None, [str(REPO_ROOT), env.value("PYTHONPATH")])))
        proc.setProcessEnvironment(env)
        proc.setWorkingDirectory(str(REPO_ROOT))
        # merge stderr into stdout so errors reach the log too
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        # bind proc and label now, since several processes may run at once
        proc.readyReadStandardOutput.connect(lambda p=proc: self._output(p))
        proc.finished.connect(lambda code, _status, p=proc, lab=label: self._done(p, lab, code))
        # keep a reference, otherwise the process could be garbage collected while running
        self.processes.append(proc)
        proc.start(sys.executable, args)
        return proc

    def _output(self, proc: QProcess) -> None:
        """Copy the output of a process to the log.

        :param proc: process.
        :type proc: QProcess
        """
        # decode whatever is available, replacing invalid bytes
        text = bytes(proc.readAllStandardOutput()).decode(errors="replace").strip()
        for line in text.splitlines():
            self.log.emit("info", f"plotter: {line}")

    def _done(self, proc: QProcess, label: str, code: int) -> None:
        """Report the end of a process and refresh the tree.

        :param proc: finished process.
        :type proc: QProcess
        :param label: name used in log messages.
        :type label: str
        :param code: exit code.
        :type code: int
        """
        self._output(proc)
        if code != 0:
            self.log.emit("error", f"{label} failed (exit code {code}), see the log")
        if proc in self.processes:
            self.processes.remove(proc)
        self.model.setRootPath(str(self.root))  # refresh exported files
