"""Main window: timeline editor in the centre, acquired data on the left, properties on the right."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from ..core.boards import load_hardware_file
from ..core.model import Experiment, default_experiment
from ..core.validation import ERROR, INFO, WARNING, validate
from ..core.yaml_export import export_yaml
from ..core.yaml_import import import_yaml_file
from .board_view import BoardPanel
from .experiment_panel import ExperimentPanel
from .inspector import Inspector
from .results_browser import ResultsBrowser
from .qt import (
    QAction,
    QApplication,
    QBrush,
    QCheckBox,
    QCloseEvent,
    QColor,
    QDockWidget,
    QFileDialog,
    QFontDatabase,
    QHBoxLayout,
    QKeySequence,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSettings,
    QSpinBox,
    QTabWidget,
    QTimer,
    QToolBar,
    QVBoxLayout,
    QWidget,
    Qt,
)
from .qt_session import SessionBridge
from .timeline_editor import TimelineEditor
from .widgets import NameRegistry, ParamEdit

PROJECT_FILTER = "FIREQ GUI project (*.fireq.json);;JSON (*.json)"
YAML_FILTER = "FIREQ experiment (*.yaml *.yml)"
LEVEL_COLORS = {ERROR: "#d0453b", WARNING: "#b07800", INFO: "#2f6fdf"}
LEVEL_ICONS = {ERROR: "✖", WARNING: "⚠", INFO: "ℹ"}
# indices of the tabs in the right-hand configuration dock
TAB_PROPS, TAB_EXP, TAB_BOARD, TAB_YAML, TAB_CHECKS, TAB_LOG = range(6)


class MainWindow(QMainWindow):
    """Experiment designer and run console."""

    def __init__(self) -> None:
        """Build the window."""
        super().__init__()
        self.settings = QSettings("vlsi-nanocomputing", "FIREQ-GUI")
        # start from the template of the last used board
        self.exp: Experiment = default_experiment(str(self.settings.value("board", "ZCU216")))
        self.project_path: str | None = None
        # json of the last saved state, used to detect unsaved changes
        self.saved_json = self.exp.to_json()
        # undo/redo stacks of json snapshots
        self.undo: list[str] = []
        self.redo: list[str] = []
        self._last_snapshot = self.saved_json
        self.issues = []

        self.setWindowTitle("FIREQ – Experiment designer")
        self.resize(1680, 980)
        self.bridge = SessionBridge(self)
        # build the ui: actions/menus, toolbar, timeline, docks, then session signals
        self._build_actions()
        self._build_toolbar()
        self._build_central()
        self._build_docks()
        self._wire_session()

        # debounce timer: many quick edits trigger a single refresh_derived
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(80)
        self.refresh_timer.timeout.connect(self.refresh_derived)
        # let every ParamEdit evaluate expressions with the current experiment
        ParamEdit.evaluator = lambda v: self.exp.ev(v)

        # restore window geometry and dock layout from the last session
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        state = self.settings.value("state")
        if state is not None:
            self.restoreState(state)
        self.load_experiment(self.exp, reset_history=True)
        # fit the timeline once the window has been laid out
        QTimer.singleShot(0, self.timeline.view.fit)
        self.statusBar().showMessage("Ready. Connect to the FIREQ server from the toolbar to run experiments.")

    # ------------------------------------------------------------------ UI construction
    def _act(self, text: str, slot: Callable[[], object], shortcut: object = None, tip: str = "") -> QAction:
        """Create an action.

        :param text: menu text.
        :type text: str
        :param slot: called when triggered.
        :type slot: Callable[[], object]
        :param shortcut: key sequence or standard key (None = no shortcut)
        :type shortcut: str | QKeySequence.StandardKey | None
        :param tip: tooltip and status tip.
        :type tip: str
        :return: the action.
        :rtype: QAction
        """
        a = QAction(text, self)
        if shortcut is not None:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        a.triggered.connect(slot)
        return a

    def _build_actions(self) -> None:
        """Create the actions and the menu bar."""
        # file actions
        self.a_new = self._act("New", self.new_project, QKeySequence.StandardKey.New)
        self.a_open = self._act("Open project…", self.open_project, QKeySequence.StandardKey.Open)
        self.a_save = self._act("Save project", self.save_project, QKeySequence.StandardKey.Save)
        self.a_save_as = self._act("Save project as…", lambda: self.save_project(as_new=True), QKeySequence.StandardKey.SaveAs)
        self.a_import = self._act("Import YAML…", self.import_yaml, "Ctrl+I", "Open an existing FIREQ YAML experiment")
        self.a_export = self._act("Export YAML…", self.export_yaml_file, "Ctrl+E", "Save the YAML file for run_yaml")
        # hardware description from a file or from the server
        self.a_hw_file = self._act("Load hardware description…", self.load_hardware, tip="Read generators, acquisitions and channels from a JSON/YAML file")
        self.a_hw_server = self._act("Read hardware from server", self.bridge.request_hardware, tip="Ask the connected server for its hardware description (get_hardware)")
        # edit actions
        self.a_undo = self._act("Undo", self.do_undo, QKeySequence.StandardKey.Undo)
        self.a_redo = self._act("Redo", self.do_redo, QKeySequence.StandardKey.Redo)
        self.a_dup = self._act("Duplicate block", lambda: self.timeline.duplicate(self.timeline.view.selected_id, False), "Ctrl+D")
        # server actions
        self.a_run = self._act("▶  Run", self.run_experiment, "F5", "Export the YAML and run the experiment on the server")
        self.a_stop = self._act("■  Stop", self.bridge.abort, "Shift+F5", "Stop the running experiment")
        self.a_quit = self._act("Quit", self.close, QKeySequence.StandardKey.Quit)

        # menu bar; None entries become separators
        mb = self.menuBar()
        m = mb.addMenu("&File")
        for a in (self.a_new, self.a_open, self.a_save, self.a_save_as, None, self.a_import, self.a_export, None, self.a_hw_file, None, self.a_quit):
            if a is None:
                m.addSeparator()
            else:
                m.addAction(a)
        m = mb.addMenu("&Edit")
        for a in (self.a_undo, self.a_redo, self.a_dup):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self._act("Add drive track", lambda: self.timeline.add_drive_track()))
        m.addAction(self._act("Add readout track", lambda: self.timeline.add_readout_track()))
        m.addAction(self._act("Add acquisition track", lambda: self.timeline.add_acq_track()))
        # server menu
        m = mb.addMenu("&Server")
        m.addAction(self.a_run)
        m.addAction(self.a_stop)
        m.addSeparator()
        m.addAction(self._act("Ping", self.bridge.ping))
        m.addAction(self._act("reset_all", self.bridge.reset_all))
        m.addAction(self._act("mts_sync", self.bridge.mts_sync))
        m.addSeparator()
        m.addAction(self.a_hw_server)
        # filled by _build_docks with the dock toggle actions
        self.view_menu = mb.addMenu("&View")

    def _build_toolbar(self) -> None:
        """Create the toolbar: files, server connection, run/stop."""
        tb = QToolBar("Main")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        self.addToolBar(tb)
        # file actions on the left
        for a in (self.a_new, self.a_open, self.a_save, self.a_import, self.a_export):
            tb.addAction(a)
        tb.addSeparator()
        # server connection fields, initialised from the saved settings
        tb.addWidget(QLabel(" Server "))
        self.host = QLineEdit(str(self.settings.value("host", "192.168.2.99")))
        self.host.setFixedWidth(130)
        self.host.setToolTip("IP address of the FIREQ server (board)")
        tb.addWidget(self.host)
        tb.addWidget(QLabel(":"))
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(self.settings.value("port", 5000)))
        tb.addWidget(self.port)
        tb.addWidget(QLabel(" token "))
        self.token = QLineEdit(str(self.settings.value("token", "fireq")))
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setFixedWidth(80)
        tb.addWidget(self.token)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setCheckable(True)
        self.connect_btn.clicked.connect(self.toggle_connection)
        tb.addWidget(self.connect_btn)
        # connection indicator, coloured by _connected_changed
        self.conn_dot = QLabel("●")
        self.conn_dot.setStyleSheet("color:#bbb; font-size:16px; padding: 0 6px;")
        tb.addWidget(self.conn_dot)
        # quick server commands
        for text, slot, tip in (
            ("Ping", self.bridge.ping, "Check the connection"),
            ("Reset", self.bridge.reset_all, "reset_all: clear the server state"),
            ("MTS", self.bridge.mts_sync, "mts_sync: multi-tile synchronisation"),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            tb.addWidget(b)
        tb.addSeparator()
        # run controls on the right
        self.reset_first = QCheckBox("reset_all before run")
        self.reset_first.setChecked(str(self.settings.value("reset_first", "true")).lower() == "true")
        self.reset_first.setToolTip("Avoid pulses and delays of previous experiments staying in the server memory")
        tb.addWidget(self.reset_first)
        tb.addAction(self.a_run)
        tb.addAction(self.a_stop)
        # stop is enabled only while an experiment runs
        self.a_stop.setEnabled(False)

    def _build_central(self) -> None:
        """Create the timeline editor (central widget)."""
        self.timeline = TimelineEditor()
        # forward timeline edits and selection to the rest of the window
        self.timeline.modelChanged.connect(self._timeline_changed)
        self.timeline.selectionChanged.connect(self._selection_changed)
        self.setCentralWidget(self.timeline)

    def _build_docks(self) -> None:
        """Create the results browser (left) and the configuration tabs (right)."""
        self.setDockNestingEnabled(True)
        # left dock: browser of acquired data
        out_root = self._output_root()
        self.browser = ResultsBrowser(out_root, str(Path(out_root).parent / "exported"))
        self.browser.log.connect(self.log)
        self.browser.openInTimeline.connect(self._open_config)
        self.browser.rootChanged.connect(lambda r: self.settings.setValue("output_root", r))
        d = QDockWidget("Acquired data", self)
        d.setObjectName("dock_data")
        d.setWidget(self.browser)
        d.setMinimumWidth(300)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, d)
        self.view_menu.addAction(d.toggleViewAction())

        # right dock: tabs with properties, experiment, board, yaml, checks and log
        self.right_tabs = QTabWidget()
        self.inspector = Inspector()
        self.inspector.changed.connect(self._inspector_changed)
        self.inspector.copyToAcquisition.connect(lambda tid: self.timeline.copy_to_acquisition(tid))
        self.right_tabs.addTab(self.inspector, "Properties")
        self.exp_panel = ExperimentPanel()
        self.exp_panel.changed.connect(self.model_changed)
        self.exp_panel.boardChanged.connect(self._board_changed)
        self.right_tabs.addTab(self.exp_panel, "Experiment")
        self.board_panel = BoardPanel()
        # nyquist zone changes requested from the board view go to the server
        self.board_panel.nyquistRequested.connect(self.bridge.set_nyquist)
        self.right_tabs.addTab(self.board_panel, "Board")
        # yaml tab: read-only preview of the export with copy/export buttons
        yw = QWidget()
        yl = QVBoxLayout(yw)
        yl.setContentsMargins(4, 4, 4, 4)
        self.yaml_view = QPlainTextEdit()
        self.yaml_view.setReadOnly(True)
        self.yaml_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.yaml_view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        yl.addWidget(self.yaml_view, 1)
        yb = QHBoxLayout()
        b = QPushButton("Copy")
        b.clicked.connect(lambda: QApplication.clipboard().setText(self.yaml_view.toPlainText()))
        yb.addWidget(b)
        b = QPushButton("Export YAML…")
        b.clicked.connect(self.export_yaml_file)
        yb.addWidget(b)
        yb.addStretch(1)
        yl.addLayout(yb)
        self.right_tabs.addTab(yw, "YAML")
        # checks tab: clicking an issue selects the related item
        self.issue_list = QListWidget()
        self.issue_list.setWordWrap(True)
        self.issue_list.itemClicked.connect(self._issue_clicked)
        self.right_tabs.addTab(self.issue_list, "Checks")
        # log tab
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.right_tabs.addTab(self.log_view, "Log")
        d = QDockWidget("Configuration", self)
        d.setObjectName("dock_config")
        d.setWidget(self.right_tabs)
        d.setMinimumWidth(400)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, d)
        self.view_menu.addAction(d.toggleViewAction())
        # status bar: cursor time and issue counters
        self.statusBar().addPermanentWidget(self.timeline.cursor_label)
        self.status_issues = QLabel()
        self.statusBar().addPermanentWidget(self.status_issues)

    def _wire_session(self) -> None:
        """Connect the session signals to the browser and the log."""
        # worker of the server session (runs in its own thread)
        w = self.bridge.worker
        w.log.connect(self.log)
        w.connectedChanged.connect(self._connected_changed)
        w.busyChanged.connect(self._busy_changed)
        # run progress and results go to the results browser
        w.started.connect(self.browser.run_started)
        w.progress.connect(self.browser.run_progress)
        w.finished.connect(self.browser.run_finished)
        w.finished.connect(lambda d, ok, m: self.log("info" if ok else "warning", f"experiment {m}: {d}"))
        w.serverMessage.connect(lambda h: self.log("server", str(h)))
        w.hardwareReceived.connect(self._hardware_from_server)

    # ------------------------------------------------------------------ model plumbing
    def load_experiment(self, exp: Experiment, reset_history: bool = False) -> None:
        """Show an experiment in every widget.

        :param exp: experiment to show.
        :type exp: Experiment
        :param reset_history: clear undo/redo (new or opened project)
        :type reset_history: bool
        """
        self.exp = exp
        # a new or opened project starts a fresh undo history
        if reset_history:
            self.undo.clear()
            self.redo.clear()
            self._last_snapshot = exp.to_json()
        # push the experiment into every view
        self.exp_panel.set_experiment(exp)
        self.timeline.set_experiment(exp)
        self.inspector.show_item(exp, self.timeline.view.selected_id)
        self.refresh_derived(redraw=False)

    def model_changed(self) -> None:
        """Called by every editor after modifying the model."""
        self.refresh_timer.start()

    def _timeline_changed(self) -> None:
        """Refresh after a change made on the timeline."""
        self.timeline.refresh()
        self.inspector.reload()
        self.exp_panel.update_derived()
        self.refresh_timer.start()

    def _inspector_changed(self) -> None:
        # the inspector already shows the change, only the timeline needs a redraw
        """Refresh after a change made in the inspector."""
        self.timeline.refresh()
        self.refresh_timer.start()

    def _selection_changed(self, item_id: str) -> None:
        """Show the selected item in the inspector.

        :param item_id: selected id.
        :type item_id: str
        """
        self.inspector.show_item(self.exp, item_id)
        # jump to the properties tab when something is selected
        if item_id:
            self.right_tabs.setCurrentIndex(TAB_PROPS)

    def refresh_derived(self, redraw: bool = True) -> None:
        """Recompute everything derived from the model (debounced).

        Pushes an undo snapshot, regenerates the YAML preview, runs the checks and refreshes the board tab.

        :param redraw: also redraw the timeline.
        :type redraw: bool
        """
        exp = self.exp
        snap = exp.to_json()
        # push an undo snapshot when the model really changed (max 200 levels)
        if snap != self._last_snapshot:
            self.undo.append(self._last_snapshot)
            self.undo = self.undo[-200:]
            self.redo.clear()
            self._last_snapshot = snap
        # update the auto-completion with the current macro and variable names
        NameRegistry.get().set_names(list(exp.preprocess), [v.name for v in exp.variables])
        # regenerate the yaml preview, keeping the scroll position
        try:
            text = export_yaml(exp)
        except Exception as e:  # noqa: BLE001
            text = f"# YAML generation failed:\n# {e}"
        sb = self.yaml_view.verticalScrollBar().value()
        self.yaml_view.setPlainText(text)
        self.yaml_view.verticalScrollBar().setValue(sb)
        # run the checks
        self.issues = validate(exp)
        self._fill_issues()
        if redraw:
            self.timeline.refresh()
        self.board_panel.set_experiment(exp)
        self.exp_panel.update_derived()
        self._update_title()

    def _fill_issues(self) -> None:
        """Show the checks in the Checks tab and in the status bar."""
        self.issue_list.clear()
        n_err = sum(i.level == ERROR for i in self.issues)
        n_warn = sum(i.level == WARNING for i in self.issues)
        # show a green entry when there are no issues
        if not self.issues:
            item = QListWidgetItem("✔ No problems found")
            item.setForeground(QBrush(QColor("#2a9d6f")))
            self.issue_list.addItem(item)
        for i in self.issues:
            item = QListWidgetItem(f"{LEVEL_ICONS[i.level]}  {i.where}: {i.message}" if i.where else f"{LEVEL_ICONS[i.level]}  {i.message}")
            item.setForeground(QBrush(QColor(LEVEL_COLORS[i.level])))
            # keep the item id so a click can select it on the timeline
            item.setData(Qt.ItemDataRole.UserRole, i.item_id)
            self.issue_list.addItem(item)
        # summary in the tab title and in the status bar
        self.right_tabs.setTabText(TAB_CHECKS, "Checks" + (f" ({n_err}✖ {n_warn}⚠)" if n_err or n_warn else ""))
        color = "#d0453b" if n_err else ("#b07800" if n_warn else "#2a9d6f")
        self.status_issues.setText(f"<span style='color:{color}'>{n_err} errors · {n_warn} warnings</span>")

    def _issue_clicked(self, item: QListWidgetItem) -> None:
        """Select the item of a clicked issue.

        :param item: clicked list entry.
        :type item: QListWidgetItem
        """
        item_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if item_id:
            self.timeline.select(item_id)

    def _update_title(self) -> None:
        """Show the project name and the unsaved-changes marker in the title."""
        dirty = self.exp.to_json() != self.saved_json
        name = Path(self.project_path).name if self.project_path else self.exp.name
        self.setWindowTitle(f"{'● ' if dirty else ''}{name} — FIREQ Experiment designer")

    def _board_changed(self) -> None:
        """Re-map tracks onto the channels of the new board."""
        exp = self.exp
        from ..core.boards import BUILTIN_BOARDS, RAW_BOARDS

        # built-in boards need no hardware dict; others keep their raw description
        exp.hardware = None if exp.board in BUILTIN_BOARDS else dict(RAW_BOARDS.get(exp.board, {})) or None
        b = exp.board_profile
        self.settings.setValue("board", exp.board)
        # move tracks whose channel does not exist on the new board to a free one
        for tr in [*exp.drive_tracks, *exp.readout_tracks]:
            if b.dac(tr.dac) is None:
                tr.dac = exp.free_dac(exclude=tr)
        for tr in exp.acq_tracks:
            if b.adc(tr.adc) is None:
                tr.adc = exp.free_adc(exclude=tr)
        # clamp generator and acquisition indices to the new board
        for d in exp.drive_tracks:
            d.generator = min(d.generator, b.generators - 1)
        for _, t in exp.all_tones():
            t.generator = min(t.generator, b.generators - 1)
        for _, w in exp.all_windows():
            w.acquisition = min(w.acquisition, b.acquisitions - 1)
        self.timeline.set_experiment(exp)
        self.inspector.show_item(exp, self.timeline.view.selected_id)
        self.model_changed()
        self.log("info", f"board: {b.title}; tracks re-mapped where needed")

    # ------------------------------------------------------------------ hardware description
    def load_hardware(self) -> None:
        """Load generators/acquisitions/channels from a JSON or YAML file."""
        path, _ = QFileDialog.getOpenFileName(self, "Hardware description", self._dir(), "Hardware description (*.json *.yaml *.yml)")
        if not path:
            return
        # read and parse the file, then apply it
        try:
            key, desc = load_hardware_file(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Hardware description", f"Cannot read {path}:\n{e}")
            return
        self.apply_hardware(key, desc, f"file {Path(path).name}")

    def _hardware_from_server(self, data: dict) -> None:
        """Apply a hardware description received from the server.

        :param data: description (or message containing it)
        :type data: dict
        """
        from ..core.boards import parse_hardware

        # the server data may be wrapped in a message; key it by the host
        key, desc = parse_hardware(data, default_key=f"server_{self.host.text().strip()}")
        try:
            self.apply_hardware(key, desc, "server")
        except Exception as e:  # noqa: BLE001
            self.log("error", f"invalid hardware description from the server: {e}")

    def apply_hardware(self, key: str, desc: dict, source: str) -> None:
        """Use a hardware description for the current experiment.

        :param key: board key.
        :type key: str
        :param desc: hardware description.
        :type desc: dict
        :param source: origin shown in the log (``server``, ``file x.yaml``)
        :type source: str
        """
        # replace the board profile and refresh every view that depends on it
        self.exp.apply_hardware(key, desc)
        b = self.exp.board_profile
        self.settings.setValue("board", key)
        self.exp_panel.refresh_boards()
        self.exp_panel.reload()
        self.timeline.set_experiment(self.exp)
        self.inspector.show_item(self.exp, self.timeline.view.selected_id)
        self.model_changed()
        msg = f"hardware from {source}: {b.title}, {b.generators} generators, {b.acquisitions} acquisitions, {len(b.dacs)} DAC, {len(b.adcs)} ADC"
        self.log("info", msg)
        self.statusBar().showMessage(msg, 8000)

    def _open_config(self, path: str) -> None:
        """Open the configuration of an acquired experiment in the timeline.

        :param path: ``config.json`` of an experiment folder.
        :type path: str
        """
        if not self._confirm_discard():
            return
        try:
            exp, warns = import_yaml_file(path, board=self.exp.board)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Error", f"Cannot open {path}:\n{e}")
            return
        # name the project after the experiment folder
        exp.name = Path(path).parent.parent.name or exp.name
        # keep the current hardware description
        exp.hardware = self.exp.hardware
        self.project_path = None
        self.saved_json = exp.to_json()
        self.load_experiment(exp, reset_history=True)
        self.timeline.view.fit()
        for w in warns:
            self.log("warning", f"import: {w}")

    # ------------------------------------------------------------------ undo
    def do_undo(self) -> None:
        """Restore the previous state."""
        if not self.undo:
            return
        # save the current state for redo and restore the previous snapshot
        self.redo.append(self.exp.to_json())
        self._last_snapshot = self.undo.pop()
        self.load_experiment(Experiment.from_json(self._last_snapshot))

    def do_redo(self) -> None:
        """Re-apply an undone change."""
        if not self.redo:
            return
        # save the current state for undo and restore the next snapshot
        self.undo.append(self.exp.to_json())
        self._last_snapshot = self.redo.pop()
        self.load_experiment(Experiment.from_json(self._last_snapshot))

    # ------------------------------------------------------------------ files
    def _confirm_discard(self) -> bool:
        """Ask before discarding unsaved changes.

        :return: True if there are no changes or the user accepts.
        :rtype: bool
        """
        # nothing to lose if the model matches the saved json
        if self.exp.to_json() == self.saved_json:
            return True
        r = QMessageBox.question(
            self, "Unsaved changes", "The current project has unsaved changes. Continue?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        return r == QMessageBox.StandardButton.Yes

    def _dir(self) -> str:
        """Return the last folder used in a file dialog.

        :return: the folder.
        :rtype: str
        """
        return str(self.settings.value("last_dir", os.getcwd()))

    def new_project(self) -> None:
        """Start from the default template."""
        if not self._confirm_discard():
            return
        self.project_path = None
        exp = default_experiment(self.exp.board)
        self.saved_json = exp.to_json()
        self.load_experiment(exp, reset_history=True)
        self.timeline.view.fit()

    def open_project(self) -> None:
        """Open a project file (or a YAML file)."""
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open project", self._dir(), PROJECT_FILTER + ";;" + YAML_FILTER)
        if not path:
            return
        # yaml files are imported instead of loaded as a project
        if path.endswith((".yaml", ".yml")):
            self._import(path)
            return
        try:
            exp = Experiment.from_json(Path(path).read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Error", f"Cannot open the project:\n{e}")
            return
        self.settings.setValue("last_dir", str(Path(path).parent))
        self.project_path = path
        self.saved_json = exp.to_json()
        self.load_experiment(exp, reset_history=True)
        self.timeline.view.fit()

    def save_project(self, as_new: bool = False) -> bool:
        """Save the project as JSON.

        :param as_new: always ask for a file name.
        :type as_new: bool
        :return: True if saved.
        :rtype: bool
        """
        path = self.project_path
        # ask for a file name on Save As or for a never-saved project
        if as_new or not path:
            path, _ = QFileDialog.getSaveFileName(self, "Save project", os.path.join(self._dir(), f"{self.exp.name}.fireq.json"), PROJECT_FILTER)
            if not path:
                return False
        # write the json and mark the project as saved
        Path(path).write_text(self.exp.to_json(), encoding="utf-8")
        self.settings.setValue("last_dir", str(Path(path).parent))
        self.project_path = path
        self.saved_json = self.exp.to_json()
        self._update_title()
        self.statusBar().showMessage(f"Saved {path}", 4000)
        return True

    def import_yaml(self) -> None:
        """Import an existing FIREQ YAML file."""
        if not self._confirm_discard():
            return
        # start in the bundled yaml examples folder on first use
        start = self._dir()
        examples = Path(__file__).resolve().parents[2] / "yaml_experiment_configurations_examples"
        if examples.is_dir() and not self.settings.value("last_dir"):
            start = str(examples)
        path, _ = QFileDialog.getOpenFileName(self, "Import YAML", start, YAML_FILTER)
        if path:
            self._import(path)

    def _import(self, path: str) -> None:
        """Import a YAML experiment into the timeline.

        :param path: YAML file.
        :type path: str
        """
        try:
            # convert the yaml to an experiment, collecting warnings
            exp, warns = import_yaml_file(path, board=self.exp.board)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Error", f"Cannot import the file:\n{e}")
            return
        self.settings.setValue("last_dir", str(Path(path).parent))
        # an imported yaml is not a project file yet
        self.project_path = None
        self.saved_json = exp.to_json()
        self.load_experiment(exp, reset_history=True)
        self.timeline.view.fit()
        for w in warns:
            self.log("warning", f"import: {w}")
        self.statusBar().showMessage(f"Imported {path}" + (f" with {len(warns)} warnings (see log)" if warns else ""), 6000)

    def export_yaml_file(self) -> str | None:
        """Save the YAML file.

        :return: the chosen path, or None if cancelled.
        :rtype: str | None
        """
        path, _ = QFileDialog.getSaveFileName(self, "Export YAML", os.path.join(self._dir(), f"{self.exp.name}.yaml"), YAML_FILTER)
        if not path:
            return None
        Path(path).write_text(export_yaml(self.exp), encoding="utf-8")
        self.settings.setValue("last_dir", str(Path(path).parent))
        self.statusBar().showMessage(f"YAML saved to {path}", 5000)
        self.log("info", f"YAML exported: {path}  (CLI: run_yaml {path})")
        return path

    # ------------------------------------------------------------------ server
    def toggle_connection(self) -> None:
        """Connect or disconnect."""
        # the button toggles: disconnect if already connected
        if self.bridge.connected:
            self.bridge.disconnect_from()
            return
        # remember the connection parameters for next time
        self.settings.setValue("host", self.host.text().strip())
        self.settings.setValue("port", self.port.value())
        self.settings.setValue("token", self.token.text())
        self.connect_btn.setText("Connecting…")
        self.bridge.connect_to(self.host.text().strip(), self.port.value(), self.token.text() or "fireq", self._output_root())

    def _output_root(self) -> str:
        """Folder where runs are saved: the folder shown in the results browser.

        :return: the output folder.
        :rtype: str
        """
        # the browser is not built yet when called from _build_docks
        if hasattr(self, "browser"):
            return str(self.browser.root)
        return str(self.settings.value("output_root", os.path.join(os.getcwd(), "experiment_output")))

    def _connected_changed(self, on: bool) -> None:
        """Update the connection button and indicator.

        :param on: True when connected.
        :type on: bool
        """
        self.connect_btn.setChecked(on)
        self.connect_btn.setText("Disconnect" if on else "Connect")
        self.conn_dot.setStyleSheet(f"color:{'#2a9d6f' if on else '#bbb'}; font-size:16px; padding: 0 6px;")
        self.statusBar().showMessage(f"Connected to {self.host.text()}:{self.port.value()}" if on else "Not connected", 5000)

    def _busy_changed(self, busy: bool) -> None:
        """Enable Run or Stop while the worker is busy.

        :param busy: True while a command runs.
        :type busy: bool
        """
        self.a_run.setEnabled(not busy)
        self.a_stop.setEnabled(busy)

    def run_experiment(self) -> None:
        """Export the YAML into experiments/ and run it."""
        # ask before running a configuration with errors
        errors = [i for i in self.issues if i.level == ERROR]
        if errors:
            r = QMessageBox.warning(
                self,
                "Configuration errors",
                "The checks found errors:\n\n" + "\n".join(f"• {i.where}: {i.message}" for i in errors[:10]) + "\n\nRun anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if r != QMessageBox.StandardButton.Yes:
                self.right_tabs.setCurrentIndex(TAB_CHECKS)
                return
        if not self.bridge.connected:
            QMessageBox.information(self, "Server", "Connect to the FIREQ server first.")
            return
        # write the yaml to experiments/ in the working folder and run it
        out_dir = Path(os.getcwd()) / "experiments"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{self.exp.name}.yaml"
        path.write_text(export_yaml(self.exp), encoding="utf-8")
        self.settings.setValue("reset_first", "true" if self.reset_first.isChecked() else "false")
        self.log("info", f"running {path}")
        self.bridge.run(str(path), self.exp.name, self.reset_first.isChecked(), self._output_root())

    # ------------------------------------------------------------------ misc
    def log(self, level: str, text: str) -> None:
        """Write to the Log tab and, for errors, to the status bar.

        :param level: ``info``, ``warning``, ``error`` or ``server``.
        :type level: str
        :param text: message.
        :type text: str
        """
        self.log_view.appendPlainText(f"[{level}] {text}")
        if level == "error":
            self.statusBar().showMessage(text, 8000)

    def closeEvent(self, ev: QCloseEvent) -> None:  # noqa: N802
        """Ask to save and stop the worker.

        :param ev: close event.
        :type ev: QCloseEvent
        """
        # offer to save unsaved changes; Cancel or a cancelled save keeps the window open
        if self.exp.to_json() != self.saved_json:
            r = QMessageBox.question(
                self,
                "Quit",
                "Save the project before quitting?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            )
            if r == QMessageBox.StandardButton.Cancel or (r == QMessageBox.StandardButton.Save and not self.save_project()):
                ev.ignore()
                return
        # remember the layout and stop the session worker
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("state", self.saveState())
        self.bridge.shutdown()
        ev.accept()
