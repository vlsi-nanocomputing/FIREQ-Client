"""Property inspector for the item selected on the timeline."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ..core.envelopes import SHAPE_PARAMS, SHAPES, envelope
from ..core.model import OUTPUT_TYPES, AcqWindow, AcquisitionTrack, DriveBlock, DriveTrack, Experiment, ReadoutTrack, Tone
from ..core.yaml_export import Exporter
from .qt import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    Qt,
    QVBoxLayout,
    QWidget,
    pg,
    pyqtSignal,
)
from .widgets import ParamEdit, color_button, fill_combo, with_unit


@dataclass
class Field:
    """Description of one editable property.

    :param label: label of the form row.
    :type label: str
    :param attr: attribute of the edited object.
    :type attr: str
    :param kind: editor kind: ``time``, ``param``, ``text``, ``float``, ``int``, ``bool``, ``enum`` or ``color``.
    :type kind: str
    :param unit: unit shown next to the editor.
    :type unit: str
    :param tip: tooltip.
    :type tip: str
    :param options: entries of an ``enum`` field.
    :type options: Callable[[Experiment, object], list[tuple[str, object, bool]]] | None
    :param visible: rule that hides the row for some objects.
    :type visible: Callable[[object], bool] | None
    :param lo: minimum (numeric editors)
    :type lo: float
    :param hi: maximum (numeric editors)
    :type hi: float
    :param step: step (float editor)
    :type step: float
    """

    label: str
    attr: str
    kind: str  # time | param | text | float | int | bool | enum | color
    unit: str = ""
    tip: str = ""
    # callback returning the (text, data, enabled) entries of an enum combo
    options: Callable[[Experiment, object], list[tuple[str, object, bool]]] | None = None
    # callback deciding whether the row is shown for the bound object
    visible: Callable[[object], bool] | None = None
    lo: float = 0.0
    hi: float = 1e9
    step: float = 0.01


class FormPage(QWidget):
    """A form bound to one model object, built from a list of :class:`Field`."""

    changed = pyqtSignal(str)  # attribute name

    def __init__(self, title: str, fields: list[Field], parent: QWidget | None = None) -> None:
        """Build the widgets.

        :param title: page title.
        :type title: str
        :param fields: properties shown by the page.
        :type fields: list[Field]
        :param parent: parent widget.
        :type parent: QWidget | None
        """
        super().__init__(parent)
        self.fields = fields
        self.obj: object = None
        self.exp: Experiment | None = None
        # set while reload() fills the widgets, so their signals do not write back
        self._loading = False
        # page layout: title, form rows, then a slot for extra views below
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(8, 8, 8, 8)
        self.title = QLabel(f"<b>{title}</b>")
        self.lay.addWidget(self.title)
        self.form = QFormLayout()
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.lay.addLayout(self.form)
        # editors and form rows by attribute name, plus the tick readouts of time fields
        self.widgets: dict[str, QWidget] = {}
        self.rows: dict[str, QWidget] = {}
        self.tick_labels: dict[str, QLabel] = {}
        # one form row per field
        for f in fields:
            w, row = self._make(f)
            if f.tip:
                w.setToolTip(f.tip)
            self.widgets[f.attr] = w
            self.rows[f.attr] = row
            self.form.addRow(f.label, row)
        # extra widgets (plots, info labels) added by the inspector go here
        self.extra = QVBoxLayout()
        self.lay.addLayout(self.extra)
        self.lay.addStretch(1)

    def _make(self, f: Field) -> tuple[QWidget, QWidget]:
        """Create the editor of a field.

        :param f: field description.
        :type f: Field
        :return: the editor and the widget placed in the form row.
        :rtype: tuple[QWidget, QWidget]
        """
        # time and parameter fields accept numbers, %macros and #expressions
        if f.kind in ("time", "param"):
            w = ParamEdit(0, f.unit)
            # time values are snapped to the tick before being stored
            w.edited.connect(lambda v, a=f.attr, k=f.kind: self._set(a, self._snap(v) if k == "time" else v))
            if f.kind == "time":
                # time fields also show their value in ticks next to the ns unit
                box = with_unit(w, "ns")
                lab = QLabel()
                lab.setStyleSheet("color:#7a8494; font-size: 11px;")
                lab.setMinimumWidth(80)
                box.layout().addWidget(lab)
                self.tick_labels[f.attr] = lab
                return w, box
            return w, (with_unit(w, f.unit) if f.unit else w)
        if f.kind == "text":
            w = QLineEdit()
            # an empty name is ignored
            w.editingFinished.connect(lambda a=f.attr, w=w: self._set(a, w.text().strip()) if w.text().strip() else None)
            return w, w
        if f.kind == "float":
            w = QDoubleSpinBox()
            w.setRange(f.lo, f.hi)
            w.setSingleStep(f.step)
            w.setDecimals(3)
            w.valueChanged.connect(lambda v, a=f.attr: self._set(a, float(v)))
            return w, w
        if f.kind == "int":
            w = QSpinBox()
            w.setRange(int(f.lo), int(f.hi))
            w.valueChanged.connect(lambda v, a=f.attr: self._set(a, int(v)))
            return w, w
        if f.kind == "bool":
            w = QCheckBox()
            w.toggled.connect(lambda v, a=f.attr: self._set(a, bool(v)))
            return w, w
        if f.kind == "enum":
            w = QComboBox()
            w.currentIndexChanged.connect(lambda _, a=f.attr, w=w: self._set(a, w.currentData()))
            return w, w
        if f.kind == "color":
            w = color_button("#000000", lambda c, a=f.attr: self._set(a, c))
            return w, w
        raise ValueError(f.kind)

    def _snap(self, v: object) -> object:
        """Snap a numeric time to the tick (expressions are left unchanged).

        :param v: edited value.
        :type v: object
        :return: the value to store.
        :rtype: object
        """
        # bool is excluded because it is a subclass of int
        if isinstance(v, (int, float)) and not isinstance(v, bool) and self.exp is not None:
            return self.exp.snap_time(float(v))
        return v

    def bind(self, exp: Experiment, obj: object) -> None:
        """Show an object.

        :param exp: experiment being edited.
        :type exp: Experiment
        :param obj: object whose attributes are edited.
        :type obj: object
        """
        self.exp, self.obj = exp, obj
        self.reload()

    def reload(self) -> None:
        """Refresh widgets from the object."""
        if self.obj is None:
            return
        # fill each editor from the object's attribute without emitting changes
        self._loading = True
        for f in self.fields:
            w = self.widgets[f.attr]
            v = getattr(self.obj, f.attr)
            if f.kind in ("time", "param"):
                w.setValue(v)
            elif f.kind == "text":
                w.setText(str(v))
            elif f.kind in ("float", "int"):
                w.setValue(v)
            elif f.kind == "bool":
                w.setChecked(bool(v))
            elif f.kind == "enum":
                fill_combo(w, f.options(self.exp, self.obj) if f.options else [], v)
            elif f.kind == "color":
                w.repaint_color(v)
            # show or hide the row according to the field's visibility rule
            if f.visible is not None:
                self.form.setRowVisible(self.rows[f.attr], f.visible(self.obj))
        self._update_ticks()
        self._loading = False

    def _update_ticks(self) -> None:
        """Show every time field in ticks."""
        for attr, lab in self.tick_labels.items():
            # evaluate the time (it may be an expression) and convert it to ticks
            v = self.exp.ev(getattr(self.obj, attr)) if self.exp else None
            lab.setText("" if v is None else f"= {v / self.exp.tick:.1f} ticks")

    def _set(self, attr: str, value: object) -> None:
        """Write an edited value into the object and emit :attr:`changed`.

        :param attr: attribute name.
        :type attr: str
        :param value: new value.
        :type value: object
        """
        if self._loading or self.obj is None:
            return
        setattr(self.obj, attr, value)
        # show the stored value, which may differ from the typed one after snapping
        if attr in self.widgets and isinstance(self.widgets[attr], ParamEdit):
            self.widgets[attr].setValue(value)
        # a change (e.g. the envelope shape) can show or hide other rows
        if any(f.visible for f in self.fields):
            self._loading = True
            for f in self.fields:
                if f.visible is not None:
                    self.form.setRowVisible(self.rows[f.attr], f.visible(self.obj))
            self._loading = False
        self._update_ticks()
        self.changed.emit(attr)


# --------------------------------------------------------------------------- option providers
# providers of combo entries, called with the experiment and the edited object
def _gens(exp: Experiment, _o: object) -> list[tuple[str, object, bool]]:
    """List the generator IPs of the board.

    :param exp: experiment being edited.
    :type exp: Experiment
    :param _o: edited object (unused)
    :type _o: object
    :return: (text, data, enabled) combo entries.
    :rtype: list[tuple[str, object, bool]]
    """
    return [(f"axisGeneratorIP_{g}", g, True) for g in range(exp.board_profile.generators)]


def _acqs(exp: Experiment, _o: object) -> list[tuple[str, object, bool]]:
    """List the acquisition IPs of the board.

    :param exp: experiment being edited.
    :type exp: Experiment
    :param _o: edited object (unused)
    :type _o: object
    :return: (text, data, enabled) combo entries.
    :rtype: list[tuple[str, object, bool]]
    """
    return [(f"axisAcquisitionIP_{a}", a, True) for a in range(exp.board_profile.acquisitions)]


def _dacs(exp: Experiment, _o: object) -> list[tuple[str, object, bool]]:
    """List the DAC channels of the board.

    :param exp: experiment being edited.
    :type exp: Experiment
    :param _o: edited object (unused)
    :type _o: object
    :return: (text, data, enabled) combo entries.
    :rtype: list[tuple[str, object, bool]]
    """
    # channels that are not routed to a connector are listed but disabled
    return [(f"{c.label}" + (f"  ({c.sma})" if c.sma else "") + ("" if c.routed else "  - not routed"), c.name, c.routed) for c in exp.board_profile.dacs]


def _adcs(exp: Experiment, _o: object) -> list[tuple[str, object, bool]]:
    """List the ADC channels of the board.

    :param exp: experiment being edited.
    :type exp: Experiment
    :param _o: edited object (unused)
    :type _o: object
    :return: (text, data, enabled) combo entries.
    :rtype: list[tuple[str, object, bool]]
    """
    return [(f"{c.label}" + (f"  ({c.sma})" if c.sma else "") + ("" if c.routed else "  - not routed"), c.name, c.routed) for c in exp.board_profile.adcs]


def _shapes(_e: Experiment, _o: object) -> list[tuple[str, object, bool]]:
    """List the envelope shapes.

    :param _e: experiment (unused)
    :type _e: Experiment
    :param _o: edited object (unused)
    :type _o: object
    :return: (text, data, enabled) combo entries.
    :rtype: list[tuple[str, object, bool]]
    """
    return [(v, k, True) for k, v in SHAPES.items()]


def _otypes(_e: Experiment, _o: object) -> list[tuple[str, object, bool]]:
    """List the acquisition output types.

    :param _e: experiment (unused)
    :type _e: Experiment
    :param _o: edited object (unused)
    :type _o: object
    :return: (text, data, enabled) combo entries.
    :rtype: list[tuple[str, object, bool]]
    """
    return [(k, k, True) for k in OUTPUT_TYPES]


def _tones(exp: Experiment, _o: object) -> list[tuple[str, object, bool]]:
    """List all readout tones.

    :param exp: experiment being edited.
    :type exp: Experiment
    :param _o: edited object (unused)
    :type _o: object
    :return: (text, data, enabled) combo entries.
    :rtype: list[tuple[str, object, bool]]
    """
    return [(f"{r.title} / {t.name}", t.id, True) for r in exp.readout_tracks for t in r.tones]


# visibility rules used by the drive pulse page
def _pulse_only(o: DriveBlock) -> bool:
    """Visibility rule: show the field for pulses, hide it for VZ gates.

    :param o: drive block.
    :type o: DriveBlock
    :return: True for pulses.
    :rtype: bool
    """
    return o.kind == "pulse"


def _has(param: str) -> Callable[[DriveBlock], bool]:
    """Visibility rule factory: show the field if the envelope shape uses a parameter.

    :param param: shape parameter (``sigma``, ``beta``, ``rise``, ``expr``)
    :type param: str
    :return: the rule.
    :rtype: Callable[[DriveBlock], bool]
    """
    return lambda o: o.kind == "pulse" and param in SHAPE_PARAMS.get(o.shape, ())


# --------------------------------------------------------------------------- inspector
class Inspector(QScrollArea):
    """Shows the page matching the selection."""

    changed = pyqtSignal()
    copyToAcquisition = pyqtSignal(str)  # noqa: N815

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build all pages.

        :param parent: parent widget.
        :type parent: QWidget | None
        """
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.exp: Experiment | None = None
        # one stacked page per kind of selected object
        self.stack = QStackedWidget()
        self.setWidget(self.stack)

        # placeholder page with usage hints when nothing is selected
        self.empty = QLabel(
            "Select a block or a track on the timeline.\n\n"
            "• Drag blocks from the palette onto a track\n"
            "• Double-click an empty track area to add a block\n"
            "• Drag a block to move it, its right edge to resize it\n"
            "• Right-click for more actions\n"
            "• Ctrl+wheel to zoom"
        )
        self.empty.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.empty.setStyleSheet("color:#5b6472; padding: 12px;")
        self.empty.setWordWrap(True)
        self.stack.addWidget(self.empty)

        # drive pulse page: fields depend on pulse vs VZ gate and on the envelope shape
        self.p_pulse = FormPage(
            "Drive pulse",
            [
                Field("Name", "name", "text", tip="Pulse (gate) name in the YAML; blocks with the same name are the same gate"),
                Field("Start", "start", "time", tip="Start time from the beginning of the shot"),
                Field("Duration", "duration", "time", visible=_pulse_only),
                Field("Carrier", "carrier", "param", "MHz", "Drive frequency ($dfrequency, one per generator)", visible=_pulse_only),
                Field("Envelope", "shape", "enum", options=_shapes, visible=_pulse_only),
                Field("Gain", "gain", "param", tip="Amplitude, -1 .. 1", visible=_pulse_only),
                Field("Sigma / duration", "sigma", "float", lo=0.01, hi=1.0, visible=_has("sigma")),
                Field("DRAG beta", "beta", "float", lo=-50, hi=50, step=0.1, visible=_has("beta")),
                Field("Rise / duration", "rise", "float", lo=0.01, hi=0.5, visible=_has("rise")),
                Field("Expression", "expr", "text", tip="numpy expression of t in [0, 1], may be complex, e.g. sin(pi*t)**2", visible=_has("expr")),
                Field("Samples", "n_samples", "int", lo=2, hi=16384, visible=lambda o: o.kind == "pulse" and o.shape != "rect"),
                Field("Interpolation", "for_interpolation", "bool", tip="_for_interpolation", visible=lambda o: o.kind == "pulse" and o.shape != "rect"),
                Field("Symmetric", "symmetric", "bool", tip="_is_symmetric: only the first half of the samples is sent", visible=lambda o: o.kind == "pulse" and o.shape != "rect"),
                Field("Swap I/Q", "switch_iq", "bool", visible=_pulse_only),
                Field("Keep last sample", "keep_last", "bool", visible=_pulse_only),
                Field("VZ rotation (×2π)", "vz_rotation", "param", visible=lambda o: o.kind == "vz"),
            ],
        )
        # envelope preview plot below the pulse form
        self.env_plot = pg.PlotWidget()
        self.env_plot.setMinimumHeight(170)
        self.env_plot.setLabel("bottom", "time", units="ns")
        self.env_plot.getAxis("bottom").enableAutoSIPrefix(False)
        self.env_plot.addLegend(offset=(5, 5))
        self.env_plot.setMouseEnabled(x=False, y=False)
        self.env_plot.hideButtons()
        self.p_pulse.extra.addWidget(self.env_plot)

        # readout tone page with trigger info, copy button and spectrum preview
        self.p_tone = FormPage(
            "Readout tone",
            [
                Field("Name", "name", "text"),
                Field("Enabled", "enabled", "bool"),
                Field("Frequency", "frequency", "param", "MHz", "Readout frequency ($rfrequency) and demodulation frequency of its window"),
                Field("Start", "start", "time"),
                Field("Duration", "duration", "time"),
                Field("Gain", "gain", "param", tip="-1 .. 1; the tones of a track are summed"),
                Field("Phase", "phase", "param", "rad"),
                Field("Generator", "generator", "enum", options=_gens, tip="Generator IP whose readout path plays this tone"),
            ],
        )
        self.tone_info = QLabel()
        self.tone_info.setWordWrap(True)
        self.tone_info.setStyleSheet("color:#5b6472;")
        self.p_tone.extra.addWidget(self.tone_info)
        # create the matching acquisition window for the shown tone
        self.copy_btn = QPushButton("Copy to acquisition")
        self.copy_btn.setToolTip("Create the acquisition window of this tone, delayed by the time of flight")
        self.copy_btn.clicked.connect(lambda: self.copyToAcquisition.emit(self.p_tone.obj.id))
        self.p_tone.extra.addWidget(self.copy_btn)
        # spectrum of all tones on the same readout track
        self.spec_plot = pg.PlotWidget()
        self.spec_plot.setMinimumHeight(160)
        self.spec_plot.setLabel("bottom", "frequency", units="MHz")
        self.spec_plot.getAxis("bottom").enableAutoSIPrefix(False)
        self.spec_plot.setMouseEnabled(x=True, y=False)
        self.spec_plot.hideButtons()
        self.p_tone.extra.addWidget(self.spec_plot)

        # acquisition window page
        self.p_window = FormPage(
            "Acquisition window",
            [
                Field("Readout tone", "tone_id", "enum", options=_tones, tip="The tone whose return signal is acquired and demodulated"),
                Field("Time of flight", "tof", "time", tip="Delay of the window after the start of the tone ($tof)"),
                Field("Duration", "duration", "time", tip="Acquisition window length ($duration)"),
                Field("Output", "output_type", "enum", options=_otypes, tip="accumulated: one IQ point per shot; decimated/raw: a trace per shot"),
                Field("Demod. phase", "demod_phase", "param", "rad"),
                Field("Acquisition IP", "acquisition", "enum", options=_acqs),
            ],
        )
        self.window_info = QLabel()
        self.window_info.setStyleSheet("color:#5b6472;")
        self.window_info.setWordWrap(True)
        self.p_window.extra.addWidget(self.window_info)

        # track pages: channel mapping and colour
        self.p_drive_track = FormPage(
            "Drive track",
            [
                Field("DAC channel", "dac", "enum", options=_dacs),
                Field("Generator", "generator", "enum", options=_gens, tip="Generator IP whose drive path plays this track"),
                Field("Trigger channel", "trigger_channel", "int", lo=1, hi=15, tip="$dchannel / _channel_mask bit of the drive triggers"),
                Field("Color", "color", "color"),
            ],
        )
        self.p_ro_track = FormPage("Readout track", [Field("DAC channel", "dac", "enum", options=_dacs), Field("Color", "color", "color")])
        self.ro_info = QLabel()
        self.ro_info.setWordWrap(True)
        self.ro_info.setStyleSheet("color:#5b6472;")
        self.p_ro_track.extra.addWidget(self.ro_info)
        self.p_acq_track = FormPage("Acquisition track", [Field("ADC channel", "adc", "enum", options=_adcs)])
        self.acq_info = QLabel()
        self.acq_info.setWordWrap(True)
        self.acq_info.setStyleSheet("color:#5b6472;")
        self.p_acq_track.extra.addWidget(self.acq_info)

        # add the pages to the stack and forward their edits
        for page in (self.p_pulse, self.p_tone, self.p_window, self.p_drive_track, self.p_ro_track, self.p_acq_track):
            self.stack.addWidget(page)
            page.changed.connect(self._page_changed)

    # ------------------------------------------------------------------ selection
    def show_item(self, exp: Experiment, item_id: str) -> None:
        """Show the page for a track or item id.

        :param exp: experiment being edited.
        :type exp: Experiment
        :param item_id: selected id ('' = nothing)
        :type item_id: str
        """
        self.exp = exp
        # the id may be a track or an item inside a track
        tr, it = exp.find(item_id) if item_id else (None, None)
        obj = it if it is not None else tr
        # choose the page from the type of the selected object
        page = {
            DriveBlock: self.p_pulse,
            Tone: self.p_tone,
            AcqWindow: self.p_window,
            DriveTrack: self.p_drive_track,
            ReadoutTrack: self.p_ro_track,
            AcquisitionTrack: self.p_acq_track,
        }.get(type(obj))
        if page is None:
            self.stack.setCurrentWidget(self.empty)
            return
        page.bind(exp, obj)
        self.stack.setCurrentWidget(page)
        self._extras()

    def reload(self) -> None:
        """Refresh the current page (after a timeline drag)."""
        page = self.stack.currentWidget()
        if isinstance(page, FormPage) and page.obj is not None and self.exp is not None:
            # the object may have been deleted on the timeline
            tr, it = self.exp.find(page.obj.id)
            if tr is None:
                self.stack.setCurrentWidget(self.empty)
                return
            page.reload()
            self._extras()

    def _page_changed(self, attr: str) -> None:
        """Refresh the extra views and notify a change.

        :param attr: edited attribute.
        :type attr: str
        """
        self._extras()
        self.changed.emit()

    # ------------------------------------------------------------------ extra views
    def _extras(self) -> None:
        """Refresh the views below the form (envelope preview, spectrum, information)."""
        page = self.stack.currentWidget()
        exp = self.exp
        # each page has its own extra view to refresh
        if page is self.p_pulse:
            self._envelope_preview(page.obj)
        elif page is self.p_tone:
            t: Tone = page.obj
            tr, _ = exp.find(t.id)
            # the copy button is only useful while the tone is not acquired yet
            wins = exp.windows_of(t.id)
            ch = exp.readout_channel_of(t)
            self.tone_info.setText(
                f"Readout trigger channel {ch} (tones starting together share a channel). "
                + (f"Acquired on {', '.join(a.title for a, _ in wins)}." if wins else "Not acquired yet.")
            )
            self.copy_btn.setEnabled(not wins)
            self._spectrum(tr)
        elif page is self.p_window:
            w: AcqWindow = page.obj
            # show which tone the window demodulates and its absolute start time
            r, t = exp.tone(w.tone_id)
            start = exp.ev(Exporter(exp).window_start(w))
            self.window_info.setText(
                f"Demodulates {t.name if t else '?'} at {exp.ev(t.frequency) if t else '?'} MHz. "
                f"Absolute start: {start:.3f} ns." if start is not None else ""
            )
        elif page is self.p_ro_track:
            tr: ReadoutTrack = page.obj
            n = len(tr.tones)
            b = exp.board_profile
            # explain how many tones the board can play on this track
            self.ro_info.setText(
                f"{n} tone(s). {b.title} has {b.generators} generators, so at most {b.generators} tones in total. "
                + ("The frequency multiplexer sums the tones on this DAC." if b.frequency_mux else "No frequency multiplexer in this bitstream: use one tone per DAC.")
            )
        elif page is self.p_acq_track:
            tr: AcquisitionTrack = page.obj
            c = exp.board_profile.adc(tr.adc)
            # list the acquisition IPs fed by this ADC, if known
            ips = "?" if c is None or c.acq_ips is None else (", ".join(map(str, c.acq_ips)) or "none")
            self.acq_info.setText(f"ADC {tr.adc}: tile {c.tile if c else '?'}, block {c.block if c else '?'}; feeds acquisition IP {ips}.")

    def _envelope_preview(self, b: DriveBlock) -> None:
        """Plot the I/Q envelope of a pulse with its duration and gain.

        :param b: drive block.
        :type b: DriveBlock
        """
        self.env_plot.clear()
        if b.kind != "pulse":
            return
        exp = self.exp
        # evaluate duration and gain at the sweep preview point, with fallbacks
        dur = exp.ev(b.duration) or 1.0
        gain = exp.ev(b.gain)
        gain = 1.0 if gain is None else gain
        # a custom expression can fail: show the error as the plot title
        try:
            env = envelope(b.shape, b.n_samples if b.shape != "rect" else 2, b.sigma, b.beta, b.rise, b.expr) * gain
        except Exception as e:  # noqa: BLE001
            self.env_plot.setTitle(f"<span style='color:#d0453b'>{e}</span>")
            return
        self.env_plot.setTitle(f"{len(env)} samples · gain {gain:g}")
        # a rectangular pulse is drawn as a box with vertical edges
        if b.shape == "rect":
            t = np.array([0, 0, dur, dur])
            self.env_plot.plot(t, np.array([0, gain, gain, 0]), pen=pg.mkPen("#2f6fdf", width=2), name="I")
        else:
            # otherwise plot I and Q at the sample centres
            t = (np.arange(len(env)) + 0.5) / len(env) * dur
            self.env_plot.plot(t, env.real, pen=pg.mkPen("#2f6fdf", width=2), name="I")
            self.env_plot.plot(t, env.imag, pen=pg.mkPen("#8a8f99", width=1.5), name="Q")
        self.env_plot.setYRange(-1.05, 1.05)

    def _spectrum(self, tr: ReadoutTrack | None) -> None:
        """Plot the tones of a readout track as a spectrum.

        :param tr: readout track (None clears the plot)
        :type tr: ReadoutTrack | None
        """
        self.spec_plot.clear()
        if tr is None:
            return
        exp = self.exp
        freqs = []
        # one stem per tone, thicker for the tone being edited
        for t in tr.tones:
            f, g = exp.ev(t.frequency), exp.ev(t.gain) or 0.0
            # skip tones whose frequency cannot be evaluated
            if f is None:
                continue
            freqs.append(f)
            col = tr.color if t.enabled else "#b0b4bb"
            width = 3 if t is self.p_tone.obj else 1.5
            self.spec_plot.plot([f, f], [0, g], pen=pg.mkPen(col, width=width))
            self.spec_plot.plot([f], [g], pen=None, symbol="o", symbolBrush=col, symbolPen=col, symbolSize=7)
            lab = pg.TextItem(t.name, color=col, anchor=(0.5, 1.2))
            lab.setPos(f, g)
            self.spec_plot.addItem(lab)
        # centre the view on the tones with some margin
        if freqs:
            span = max(max(freqs) - min(freqs), 20.0)
            self.spec_plot.setXRange(min(freqs) - 0.4 * span, max(freqs) + 0.4 * span)
        self.spec_plot.setYRange(0, 1.1)
        self.spec_plot.setTitle(f"Tones on {tr.title}")
