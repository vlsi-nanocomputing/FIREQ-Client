"""Experiment-level settings: board, shots, shot duration, macros and sweep variables."""

from __future__ import annotations

from ..core.boards import BOARDS
from ..core.expressions import format_param, parse_param
from ..core.model import Experiment, Variable, unique_name
from ..core.yaml_export import Exporter
from .qt import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
)
from .widgets import ParamEdit

# ($samples format key, label shown in the combo box)
SAMPLE_FORMATS = [("iq_pairs", "[[I, Q], ...]"), ("complex_str", '["I+Qj", ...]'), ("real", "[I, ...] (I only)")]


class ExperimentPanel(QScrollArea):
    """Global settings of the experiment."""

    changed = pyqtSignal()
    boardChanged = pyqtSignal()  # noqa: N815

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the panel.

        :param parent: parent widget.
        :type parent: QWidget | None
        """
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.exp: Experiment | None = None
        # true while widgets are filled from the model, so edit handlers ignore the signals
        self._loading = False
        body = QWidget()
        self.setWidget(body)
        root = QVBoxLayout(body)

        # general settings: name, board, shots and shot duration
        box = QGroupBox("Experiment")
        form = QFormLayout(box)
        self.name = QLineEdit()
        self.name.setToolTip("Name of the YAML file and of the output folder")
        self.name.editingFinished.connect(self._set_name)
        form.addRow("Name", self.name)
        self.board = QComboBox()
        self.board.setToolTip("Built-in boards, or descriptions loaded with File → Load hardware description / received from the server")
        self.refresh_boards()
        self.board.currentIndexChanged.connect(self._set_board)
        form.addRow("Board", self.board)
        self.board_info = QLabel()
        self.board_info.setWordWrap(True)
        self.board_info.setStyleSheet("color:#5b6472;")
        form.addRow("", self.board_info)
        self.shots = ParamEdit(1000)
        self.shots.edited.connect(lambda v: self._set("shots", v))
        form.addRow("Shots per point", self.shots)
        # shot duration row: auto checkbox, fixed value and margin used in auto mode
        drow = QHBoxLayout()
        self.auto = QCheckBox("auto")
        self.auto.setToolTip("End of the last event (worst case over the sweep) + margin")
        self.auto.toggled.connect(self._set_auto)
        self.duration = ParamEdit(10000, "ns")
        self.duration.edited.connect(lambda v: self._set("experiment_duration", v))
        self.margin = QDoubleSpinBox()
        self.margin.setRange(0, 1e8)
        self.margin.setDecimals(0)
        self.margin.setSingleStep(100)
        self.margin.setSuffix(" ns")
        self.margin.setToolTip("Margin added in auto mode (qubit relaxation time)")
        self.margin.valueChanged.connect(lambda v: self._set("auto_margin", float(v)))
        drow.addWidget(self.auto)
        drow.addWidget(self.duration, 1)
        drow.addWidget(QLabel("margin"))
        drow.addWidget(self.margin)
        form.addRow("Shot duration", drow)
        self.auto_label = QLabel()
        self.auto_label.setStyleSheet("color:#5b6472;")
        form.addRow("", self.auto_label)
        root.addWidget(box)

        # editable table of %name preprocess macros
        pbox = QGroupBox("Preprocess macros (%name)")
        pl = QVBoxLayout(pbox)
        self.pre = QTableWidget(0, 2)
        self.pre.setHorizontalHeaderLabels(["Name", "Value"])
        self.pre.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.pre.verticalHeader().setVisible(False)
        self.pre.setMinimumHeight(120)
        self.pre.itemChanged.connect(self._pre_edited)
        pl.addWidget(self.pre)
        pb = QHBoxLayout()
        # add / remove buttons under the macro table
        for text, fn in (("+ Macro", self._add_macro), ("Remove", self._remove_macro)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            pb.addWidget(b)
        pb.addStretch(1)
        pl.addLayout(pb)
        root.addWidget(pbox)

        # editable table of #name sweep variables (column 1 holds a mode combo box)
        vbox = QGroupBox("Sweep variables (#name)")
        vl = QVBoxLayout(vbox)
        self.vars = QTableWidget(0, 6)
        self.vars.setHorizontalHeaderLabels(["Name", "Mode", "Start", "Stop", "Points", "Values (list)"])
        self.vars.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.vars.verticalHeader().setVisible(False)
        self.vars.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.vars.setMinimumHeight(120)
        self.vars.itemChanged.connect(self._var_edited)
        vl.addWidget(self.vars)
        vb = QHBoxLayout()
        for text, fn in (("+ Variable", self._add_var), ("Remove", self._remove_var)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            vb.addWidget(b)
        # total number of sweep points and shots, refreshed by update_derived
        self.points_label = QLabel()
        self.points_label.setStyleSheet("color:#5b6472;")
        vb.addWidget(self.points_label, 1)
        vl.addLayout(vb)
        root.addWidget(vbox)

        # rarely changed export options
        abox = QGroupBox("Advanced")
        af = QFormLayout(abox)
        self.fmt = QComboBox()
        for k, text in SAMPLE_FORMATS:
            self.fmt.addItem(text, k)
        self.fmt.setToolTip("Serialisation of $samples for custom envelopes (depends on the server version)")
        self.fmt.currentIndexChanged.connect(lambda _: self._set("sample_format", self.fmt.currentData()))
        af.addRow("$samples format", self.fmt)
        self.disable_unused = QCheckBox("Disable triggers of unused IPs ($dchannel/$rchannel = 0)")
        self.disable_unused.toggled.connect(lambda v: self._set("disable_unused_ips", bool(v)))
        af.addRow(self.disable_unused)
        self.trig_node = QLineEdit()
        # an empty trigger node falls back to the default one
        self.trig_node.editingFinished.connect(lambda: self._set("trigger_node", self.trig_node.text().strip() or "/axisTriggerGenerator_0"))
        af.addRow("Trigger node", self.trig_node)
        root.addWidget(abox)
        root.addStretch(1)

    def refresh_boards(self) -> None:
        """Fill the board list (built-in and loaded hardware descriptions)."""
        # rebuild the list without firing _set_board, keeping the current selection
        self.board.blockSignals(True)
        current = self.board.currentData()
        self.board.clear()
        for key, b in BOARDS.items():
            self.board.addItem(f"{b.title}  ({b.generators} gen · {b.acquisitions} acq · {len(b.dacs)} DAC · {len(b.adcs)} ADC)", key)
        # prefer the experiment's board, else the board selected before the rebuild
        idx = self.board.findData(self.exp.board if self.exp else current)
        self.board.setCurrentIndex(max(idx, 0))
        self.board.blockSignals(False)

    # ------------------------------------------------------------------ load
    def set_experiment(self, exp: Experiment) -> None:
        """Show an experiment.

        :param exp: experiment to show.
        :type exp: Experiment
        """
        self.exp = exp
        self.reload()

    def reload(self) -> None:
        """Refresh the widgets."""
        exp = self.exp
        if exp is None:
            return
        self._loading = True
        self.name.setText(exp.name)
        # board not in the list yet (e.g. a hardware description loaded later): rebuild it
        if self.board.findData(exp.board) < 0:
            self.refresh_boards()
        self.board.setCurrentIndex(max(self.board.findData(exp.board), 0))
        self.shots.setValue(exp.shots)
        # no fixed duration means auto mode: the duration field is disabled and the margin used
        auto = exp.experiment_duration is None
        self.auto.setChecked(auto)
        self.duration.setEnabled(not auto)
        self.duration.setValue(None if auto else exp.experiment_duration)
        self.margin.setValue(exp.auto_margin)
        self.margin.setEnabled(auto)
        self.fmt.setCurrentIndex(max(self.fmt.findData(exp.sample_format), 0))
        self.disable_unused.setChecked(exp.disable_unused_ips)
        self.trig_node.setText(exp.trigger_node)
        # fill the macro table without triggering _pre_edited
        self.pre.blockSignals(True)
        self.pre.setRowCount(len(exp.preprocess))
        for i, (k, v) in enumerate(exp.preprocess.items()):
            self.pre.setItem(i, 0, QTableWidgetItem(k))
            self.pre.setItem(i, 1, QTableWidgetItem(format_param(v)))
        self.pre.blockSignals(False)
        self._fill_vars()
        self._loading = False
        self.update_derived()

    def _fill_vars(self) -> None:
        """Fill the sweep variable table."""
        exp = self.exp
        self.vars.blockSignals(True)
        self.vars.setRowCount(len(exp.variables))
        for i, v in enumerate(exp.variables):
            self.vars.setItem(i, 0, QTableWidgetItem(v.name))
            # mode combo per row; v is bound now so each combo edits its own variable
            mode = QComboBox()
            mode.addItems(["lin", "list", "const"])
            mode.setCurrentText(v.mode)
            mode.currentTextChanged.connect(lambda m, v=v: self._var_mode(v, m))
            self.vars.setCellWidget(i, 1, mode)
            # const variables use only the Start column (as the value)
            if v.mode == "const":
                cells = [format_param(v.value), "", "", ""]
            else:
                cells = [format_param(v.start), format_param(v.stop), str(v.num), ", ".join(format_param(x) for x in v.values)]
            for j, text in enumerate(cells):
                self.vars.setItem(i, 2 + j, QTableWidgetItem(text))
        self.vars.blockSignals(False)

    def update_derived(self) -> None:
        """Refresh labels that depend on the whole experiment."""
        exp = self.exp
        if exp is None:
            return
        # auto duration may fail on an incomplete experiment: show nothing then
        try:
            self.auto_label.setText(f"auto duration: {Exporter(exp).auto_duration():.3f} ns")
        except Exception:  # noqa: BLE001
            self.auto_label.setText("")
        b = exp.board_profile
        self.board_info.setText(
            f"{b.generators} generators · {b.acquisitions} acquisition IPs · "
            f"{'frequency mux' if b.frequency_mux else 'no frequency mux'} · tick {b.tick_ns:.4f} ns ({b.trigger_clock_mhz:g} MHz)"
        )
        pts = exp.total_points()
        shots = exp.ev(exp.shots) or 0
        self.points_label.setText(f"{pts} points · {pts * shots:,.0f} shots in total")

    # ------------------------------------------------------------------ edits
    def _emit(self) -> None:
        """Refresh the derived labels and emit :attr:`changed` (not while loading)."""
        if not self._loading:
            self.update_derived()
            self.changed.emit()

    def _set(self, attr: str, value: object) -> None:
        """Set an experiment attribute from an editor.

        :param attr: attribute name.
        :type attr: str
        :param value: new value.
        :type value: object
        """
        if self._loading or self.exp is None:
            return
        setattr(self.exp, attr, value)
        self._emit()

    def _set_name(self) -> None:
        """Set the experiment name (characters unsafe in file names are replaced)."""
        # keep only characters that are safe in file and folder names
        text = "".join(c if c.isalnum() or c in "_-" else "_" for c in self.name.text().strip())
        if self.exp and text and text != self.exp.name:
            self.exp.name = text
            self.name.setText(text)
            self._emit()

    def _set_board(self) -> None:
        """Switch board and notify the main window."""
        if self._loading or self.exp is None:
            return
        self.exp.board = str(self.board.currentData())
        self.boardChanged.emit()

    def _set_auto(self, on: bool) -> None:
        """Switch between automatic and fixed shot duration.

        :param on: True for automatic.
        :type on: bool
        """
        if self._loading or self.exp is None:
            return
        # switching to fixed starts from the current auto duration
        if on:
            self.exp.experiment_duration = None
        else:
            self.exp.experiment_duration = Exporter(self.exp).auto_duration()
            self.duration.setValue(self.exp.experiment_duration)
        self.duration.setEnabled(not on)
        self.margin.setEnabled(on)
        self._emit()

    def _pre_edited(self, _item: QTableWidgetItem) -> None:
        """Rebuild the preprocess macros from the table.

        :param _item: edited cell (unused)
        :type _item: QTableWidgetItem
        """
        if self._loading or self.exp is None:
            return
        # rebuild the whole macro dict from the table, skipping rows without a name
        new = {}
        for i in range(self.pre.rowCount()):
            k = (self.pre.item(i, 0).text() if self.pre.item(i, 0) else "").strip()
            v = self.pre.item(i, 1).text() if self.pre.item(i, 1) else "0"
            if k:
                new[k] = parse_param(v)
        self.exp.preprocess = new
        self._emit()

    def _add_macro(self) -> None:
        """Add a preprocess macro."""
        self.exp.preprocess[unique_name("macro", set(self.exp.preprocess))] = 0
        self.reload()
        self.changed.emit()

    def _remove_macro(self) -> None:
        """Remove the selected preprocess macro."""
        row = self.pre.currentRow()
        if row < 0 or row >= len(self.exp.preprocess):
            return
        del self.exp.preprocess[list(self.exp.preprocess)[row]]
        self.reload()
        self.changed.emit()

    def _var_edited(self, item: QTableWidgetItem) -> None:
        """Write an edited cell into its sweep variable.

        :param item: edited cell.
        :type item: QTableWidgetItem
        """
        if self._loading or self.exp is None:
            return
        row, col = item.row(), item.column()
        v = self.exp.variables[row]
        text = item.text().strip()
        # map the edited column to the variable field; invalid numbers are ignored
        try:
            if col == 0:
                v.name = text or v.name
            elif v.mode == "const" and col == 2:
                v.value = float(text)
            elif col == 2:
                v.start = float(text)
            elif col == 3:
                v.stop = float(text)
            elif col == 4:
                # at least one point
                v.num = max(int(float(text)), 1)
            elif col == 5:
                # list values accept both ',' and ';' as separators
                v.values = [float(x) for x in text.replace(";", ",").split(",") if x.strip()]
        except ValueError:
            pass
        self._emit()

    def _var_mode(self, v: Variable, mode: str) -> None:
        """Change the mode of a sweep variable.

        :param v: sweep variable.
        :type v: Variable
        :param mode: ``lin``, ``list`` or ``const``.
        :type mode: str
        """
        if self._loading:
            return
        v.mode = mode
        # seed an empty list with the first points of the current sweep
        if mode == "list" and not v.values:
            v.values = [float(x) for x in v.points()][:10]
        # rebuild the table without re-entering the edit handlers
        self._loading = True
        self._fill_vars()
        self._loading = False
        self._emit()

    def _add_var(self) -> None:
        """Add a sweep variable."""
        self.exp.variables.append(Variable(name=unique_name("sweep", self.exp.var_names | set(self.exp.preprocess))))
        self.reload()
        self.changed.emit()

    def _remove_var(self) -> None:
        """Remove the selected sweep variable."""
        row = self.vars.currentRow()
        if row < 0 or row >= len(self.exp.variables):
            return
        del self.exp.variables[row]
        self.reload()
        self.changed.emit()
