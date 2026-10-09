"""Timeline editor: toolbar + block palette + multi-track view."""

from __future__ import annotations

import copy
from collections.abc import Callable

from ..core.model import DriveBlock, Experiment, ReadoutTrack, Tone, new_id, unique_name
from .qt import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSlider,
    QSplitter,
    Qt,
    QToolButton,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
)
from .timeline import BlockPalette, TimelineView


class TimelineEditor(QWidget):
    """Owns the timeline view and performs the editing operations on the model."""

    modelChanged = pyqtSignal()  # noqa: N815
    selectionChanged = pyqtSignal(str)  # noqa: N815

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the editor.

        :param parent: parent widget.
        :type parent: QWidget | None
        """
        super().__init__(parent)
        self.exp: Experiment | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        # toolbar: add-track menus, block operations, snap and zoom, sweep preview
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 2)
        self.btn_drive = self._menu_button("+ Drive track", "Add a drive track on a DAC channel", self._fill_dac_menu_drive)
        self.btn_ro = self._menu_button("+ Readout track", "Add a readout track on a DAC channel", self._fill_dac_menu_ro)
        self.btn_acq = self._menu_button("+ Acquisition track", "Add an acquisition track on an ADC channel", self._fill_adc_menu)
        for b in (self.btn_drive, self.btn_ro, self.btn_acq):
            bar.addWidget(b)
        bar.addSpacing(10)
        self.btn_tone = self._button("+ Tone", "Add a multiplexed tone to the selected readout track", self.add_tone)
        self.btn_copy = self._button("Copy to acquisition", "Create the acquisition window of the selected tone (delayed by the time of flight)", self.copy_selected)
        self.btn_dup = self._button("Duplicate", "Duplicate the selected block (Ctrl+D)", lambda: self.duplicate(self.view.selected_id, False))
        self.btn_del = self._button("Delete", "Delete the selected block or track (Del)", lambda: self.delete(self.view.selected_id))
        for b in (self.btn_tone, self.btn_copy, self.btn_dup, self.btn_del):
            bar.addWidget(b)
        bar.addSpacing(10)
        self.snap = QCheckBox("Snap to tick")
        self.snap.setChecked(True)
        self.snap.toggled.connect(self._set_snap)
        bar.addWidget(self.snap)
        bar.addWidget(self._button("−", "Zoom out (Ctrl+wheel)", lambda: self.view.set_scale(self.view.scale_x * 0.8)))
        bar.addWidget(self._button("+", "Zoom in (Ctrl+wheel)", lambda: self.view.set_scale(self.view.scale_x * 1.25)))
        bar.addWidget(self._button("Fit", "Show the whole shot", lambda: self.view.fit()))
        bar.addSpacing(10)
        bar.addWidget(QLabel("Sweep preview"))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setFixedWidth(120)
        self.slider.setToolTip("Point of the sweep used to draw values that depend on #variables")
        self.slider.valueChanged.connect(self._preview_moved)
        bar.addWidget(self.slider)
        self.sweep_label = QLabel()
        self.sweep_label.setStyleSheet("color:#2f6fdf;")
        bar.addWidget(self.sweep_label)
        bar.addStretch(1)
        self.cursor_label = QLabel()  # shown in the main window status bar
        self.cursor_label.setStyleSheet("color:#5b6472; font-family: monospace;")
        lay.addLayout(bar)

        # palette on the left, timeline view taking the remaining width
        split = QSplitter(Qt.Orientation.Horizontal)
        self.palette = BlockPalette()
        split.addWidget(self.palette)
        self.view = TimelineView(self)
        self.view.modelChanged.connect(self._changed)
        self.view.selectionChanged.connect(self._selected)
        self.view.hoverTime.connect(self._hover)
        split.addWidget(self.view)
        split.setStretchFactor(1, 1)
        split.setSizes([150, 1200])
        lay.addWidget(split, 1)

    # ------------------------------------------------------------------ helpers
    def _button(self, text: str, tip: str, slot: Callable[[], None]) -> QToolButton:
        """Create a toolbar button.

        :param text: label.
        :type text: str
        :param tip: tooltip.
        :type tip: str
        :param slot: called when clicked.
        :type slot: Callable[[], None]
        :return: the button.
        :rtype: QToolButton
        """
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        b.clicked.connect(slot)
        return b

    def _menu_button(self, text: str, tip: str, filler: Callable[[QMenu], None]) -> QToolButton:
        """Create a toolbar button with a drop-down menu filled when opened.

        :param text: label.
        :type text: str
        :param tip: tooltip.
        :type tip: str
        :param filler: called with the menu before it is shown.
        :type filler: Callable[[QMenu], None]
        :return: the button.
        :rtype: QToolButton
        """
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(b)
        m.aboutToShow.connect(lambda: filler(m))
        b.setMenu(m)
        return b

    def _fill_channels(self, menu: QMenu, kind: str, add: Callable[[str], None]) -> None:
        """List the converter channels of the board in a menu.

        :param menu: menu to fill.
        :type menu: QMenu
        :param kind: ``dac`` or ``adc``.
        :type kind: str
        :param add: called with the chosen channel name.
        :type add: Callable[[str], None]
        """
        menu.clear()
        # list every channel of the board, marking the unrouted and already used ones
        b = self.exp.board_profile
        conns = b.dacs if kind == "dac" else b.adcs
        used = self.exp.used_dacs() if kind == "dac" else {a.adc for a in self.exp.acq_tracks}
        for c in conns:
            label = f"{c.label}" + (f"  ({c.sma})" if c.sma else "")
            if not c.routed:
                label += "  - not routed"
            elif c.name in used:
                label += "  - in use"
            act = menu.addAction(label, lambda name=c.name: add(name))
            # unrouted channels stay selectable on purpose
            act.setEnabled(c.routed or True)

    def _fill_dac_menu_drive(self, menu: QMenu) -> None:
        """Fill the '+ Drive track' menu.

        :param menu: menu to fill.
        :type menu: QMenu
        """
        self._fill_channels(menu, "dac", self.add_drive_track)

    def _fill_dac_menu_ro(self, menu: QMenu) -> None:
        """Fill the '+ Readout track' menu.

        :param menu: menu to fill.
        :type menu: QMenu
        """
        self._fill_channels(menu, "dac", self.add_readout_track)

    def _fill_adc_menu(self, menu: QMenu) -> None:
        """Fill the '+ Acquisition track' menu.

        :param menu: menu to fill.
        :type menu: QMenu
        """
        self._fill_channels(menu, "adc", self.add_acq_track)

    # ------------------------------------------------------------------ model binding
    def set_experiment(self, exp: Experiment) -> None:
        """Attach an experiment.

        :param exp: experiment to edit.
        :type exp: Experiment
        """
        self.exp = exp
        # sync the controls with the model without emitting change signals
        self.snap.blockSignals(True)
        self.snap.setChecked(exp.snap)
        self.snap.setText(f"Snap to tick ({exp.tick:.3f} ns)")
        self.snap.blockSignals(False)
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(exp.preview_fraction * 100)))
        self.slider.blockSignals(False)
        self.view.set_experiment(exp)
        self._update_sweep_label()
        self._update_buttons()

    def refresh(self) -> None:
        """Redraw after a model change made elsewhere."""
        self.snap.setText(f"Snap to tick ({self.exp.tick:.3f} ns)")
        self.view.rebuild()
        self._update_sweep_label()
        self._update_buttons()

    def select(self, item_id: str) -> None:
        """Select an item from outside (e.g. the checks list).

        :param item_id: id of the track or item.
        :type item_id: str
        """
        self.view.select(item_id)

    def _changed(self) -> None:
        """Forward a model change made in the view."""
        self.modelChanged.emit()

    def _selected(self, item_id: str) -> None:
        """Update the toolbar and forward a selection change.

        :param item_id: selected id.
        :type item_id: str
        """
        self._update_buttons()
        self.selectionChanged.emit(item_id)

    def _update_buttons(self) -> None:
        """Enable the toolbar buttons that apply to the selection."""
        # buttons follow the selected track/item kind
        tr, it = self.exp.find(self.view.selected_id) if self.exp else (None, None)
        self.btn_tone.setEnabled(isinstance(tr, ReadoutTrack))
        self.btn_copy.setEnabled(isinstance(it, Tone))
        self.btn_dup.setEnabled(isinstance(it, (DriveBlock, Tone)))
        self.btn_del.setEnabled(tr is not None)

    def _hover(self, t: float) -> None:
        """Show the time under the cursor in ns and ticks.

        :param t: time in ns.
        :type t: float
        """
        if self.exp is None:
            return
        self.cursor_label.setText(f"t = {t:9.3f} ns  ·  {t / self.exp.tick:8.1f} ticks")

    def _set_snap(self, on: bool) -> None:
        """Turn tick snapping on or off.

        :param on: True to snap.
        :type on: bool
        """
        self.exp.snap = on
        self.modelChanged.emit()

    def _preview_moved(self, v: int) -> None:
        """Move the sweep preview point.

        :param v: slider position, 0..100.
        :type v: int
        """
        self.exp.preview_fraction = v / 100.0
        self._update_sweep_label()
        self.view.rebuild()

    def _update_sweep_label(self) -> None:
        """Show the variable values at the preview point."""
        pt = self.exp.preview_values()
        # slider is useful only when there are swept variables
        self.sweep_label.setText(", ".join(f"{k} = {v:g}" for k, v in pt.items()))
        self.slider.setEnabled(bool(pt))

    # ------------------------------------------------------------------ operations
    def _done(self, select_id: str | None = None) -> None:
        """Finish an editing operation: notify and select.

        :param select_id: id to select (None = keep the selection)
        :type select_id: str | None
        """
        if select_id is not None:
            self.view.selected_id = select_id
        self.modelChanged.emit()
        self.selectionChanged.emit(self.view.selected_id)
        self._update_buttons()

    def add_drive_track(self, dac: str | None = None) -> None:
        """Add a drive track.

        :param dac: DAC channel name (None = first free one)
        :type dac: str | None
        """
        tr = self.exp.add_drive_track(dac)
        self._done(tr.id)

    def add_readout_track(self, dac: str | None = None) -> None:
        """Add a readout track and an acquisition window for its first tone.

        :param dac: DAC channel name (None = first free one)
        :type dac: str | None
        """
        tr = self.exp.add_readout_track(dac)
        # a new readout track comes with the acquisition window of its first tone
        self.exp.copy_to_acquisition(tr.tones[0])
        self._done(tr.id)

    def add_acq_track(self, adc: str | None = None) -> None:
        """Add an acquisition track.

        :param adc: ADC channel name (None = first free one)
        :type adc: str | None
        """
        tr = self.exp.add_acquisition_track(adc)
        self._done(tr.id)

    def add_tone(self) -> None:
        """Add a tone to the selected readout track (or the first one)."""
        tr, _ = self.exp.find(self.view.selected_id)
        # fall back to the first readout track when none is selected
        if not isinstance(tr, ReadoutTrack):
            tr = self.exp.readout_tracks[0] if self.exp.readout_tracks else None
        if tr is None:
            return
        t = self.exp.add_tone(tr)
        self._done(t.id)

    def copy_selected(self) -> None:
        """Copy the selected tone to the acquisition track."""
        self.copy_to_acquisition(self.view.selected_id)

    def copy_to_acquisition(self, tone_id: str) -> None:
        """Create the acquisition window of a tone.

        :param tone_id: id of the tone.
        :type tone_id: str
        """
        _, tone = self.exp.find(tone_id)
        if not isinstance(tone, Tone):
            return
        w = self.exp.copy_to_acquisition(tone)
        self._done(w.id)

    def duplicate(self, item_id: str, same_gate: bool) -> None:
        """Duplicate a drive block (new gate or same gate later) or a tone.

        :param item_id: id of the block or tone.
        :type item_id: str
        :param same_gate: True to repeat the same gate (same name) later in the shot.
        :type same_gate: bool
        """
        tr, it = self.exp.find(item_id)
        # drive block: deep copy with a new id, placed after the end of the track
        if isinstance(it, DriveBlock):
            new = copy.deepcopy(it)
            new.id = new_id()
            # a new gate needs a unique name; the same gate keeps its name
            if not same_gate:
                new.name = unique_name(it.name, self.exp.item_names())
            end = self.exp.track_end(tr)
            # leave a 10-tick gap after the last block
            new.start = self.exp.snap_time(end + 10 * self.exp.tick)
            tr.blocks.append(new)
            self._done(new.id)
        elif isinstance(it, Tone):
            # tone: new tone on the same track with the same parameters
            t = self.exp.add_tone(tr)
            t.frequency, t.gain, t.duration, t.start = it.frequency, it.gain, it.duration, it.start
            self._done(t.id)

    def toggle_tone(self, tone_id: str) -> None:
        """Enable/disable a tone.

        :param tone_id: id of the tone.
        :type tone_id: str
        """
        _, t = self.exp.find(tone_id)
        if isinstance(t, Tone):
            t.enabled = not t.enabled
            self._done()

    def delete(self, item_id: str) -> None:
        """Delete a block or a track.

        :param item_id: id of the item or track ('' does nothing)
        :type item_id: str
        """
        if not item_id:
            return
        tr, it = self.exp.find(item_id)
        self.exp.remove_item(item_id)
        # keep the track selected if it still exists, else clear the selection
        self._done(tr.id if it is not None and tr is not None and self.exp.find(tr.id)[0] else "")
