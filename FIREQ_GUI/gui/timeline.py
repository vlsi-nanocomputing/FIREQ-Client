"""Multi-track timeline editor, in the style of an audio/video editor.

One row per physical converter channel:

* drive tracks (``DAC 228_0`` ...) hold pulse blocks drawn with their envelope;
* readout tracks (``DAC 229_0`` ...) have one lane per multiplexed tone;
* acquisition tracks (``ADC 224_0`` ...) hold the acquisition windows, each linked to a
  tone by a dashed arrow whose length is the time of flight.

Blocks are moved by dragging their body and resized by dragging their right edge. When
snapping is on, every time is quantised to the trigger generator tick (~1.713 ns).
Blocks whose start or duration is a ``%macro``/``#expression`` are drawn at the sweep
preview point and cannot be dragged (dashed border).

The view keeps the track headers (left) and the time ruler (top) fixed while scrolling;
both are painted in :meth:`TimelineView.drawForeground`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..core.envelopes import SHAPES, envelope
from ..core.model import AcqWindow, DriveBlock, Experiment, Tone
from ..core.yaml_export import Exporter
from .qt import (
    QAbstractItemView,
    QBrush,
    QColor,
    QContextMenuEvent,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSceneHoverEvent,
    QGraphicsSceneMouseEvent,
    QGraphicsView,
    QKeyEvent,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMimeData,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPointF,
    QRectF,
    QResizeEvent,
    QStyleOptionGraphicsItem,
    QWheelEvent,
    QWidget,
    Qt,
    pyqtSignal,
)

MIME = "application/x-fireq-block"
HEADER_W = 150
RULER_H = 30
LANE_H = 30
DRIVE_H = 70
TRACK_PAD = 6
TRACK_GAP = 4
MIN_SCALE, MAX_SCALE = 0.02, 40.0  # px per ns

KIND_BG = {"drive": QColor("#f7f9fc"), "readout": QColor("#f4faf7"), "acq": QColor("#f7f6f9")}
KIND_TAG = {"drive": "DRIVE", "readout": "READOUT", "acq": "ACQUISITION"}


@dataclass
class TrackRow:
    """Geometry of one track in scene coordinates.

    :param track: model track.
    :type track: object
    :param kind: ``drive``, ``readout`` or ``acq``.
    :type kind: str
    :param y: top of the row in scene coordinates.
    :type y: float
    :param h: height of the row.
    :type h: float
    :param lanes: number of lanes.
    :type lanes: int
    """

    track: object
    kind: str  # drive | readout | acq
    y: float
    h: float
    lanes: int


def _num(v: object) -> bool:
    """Tell whether a value is a plain number (not a macro/expression, not a bool).

    :param v: parameter value.
    :type v: object
    :return: True for int/float.
    :rtype: bool
    """
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# --------------------------------------------------------------------------- palette
class BlockPalette(QListWidget):
    """Library of blocks that can be dragged onto the timeline."""

    def __init__(self) -> None:
        """Fill the library."""
        super().__init__()
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setToolTip("Drag a block onto a track")
        self.setMaximumWidth(170)
        self._section("Drive pulses")
        for key, label in SHAPES.items():
            self._entry(f"  {label}", f"pulse:{key}", "#2f6fdf")
        self._entry("  Virtual Z gate", "vz", "#7a5cd6")
        self._section("Readout")
        self._entry("  Readout tone", "tone", "#2a9d6f")

    def _section(self, text: str) -> None:
        """Add a bold, non-draggable section title.

        :param text: title.
        :type text: str
        """
        it = QListWidgetItem(text)
        f = QFont(self.font())
        f.setBold(True)
        it.setFont(f)
        it.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.addItem(it)

    def _entry(self, text: str, payload: str, color: str) -> None:
        """Add a draggable entry.

        :param text: label.
        :type text: str
        :param payload: MIME payload (``pulse:<shape>``, ``vz`` or ``tone``)
        :type payload: str
        :param color: text colour.
        :type color: str
        """
        it = QListWidgetItem(text)
        it.setData(Qt.ItemDataRole.UserRole, payload)
        it.setForeground(QBrush(QColor(color)))
        it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)
        self.addItem(it)

    def mimeTypes(self) -> list[str]:  # noqa: N802
        """Return the MIME type used for drags.

        :return: ``[MIME]``.
        :rtype: list[str]
        """
        return [MIME]

    def mimeData(self, items: list[QListWidgetItem]) -> QMimeData:  # noqa: N802
        """Encode the dragged block kind.

        :param items: dragged entries.
        :type items: list[QListWidgetItem]
        :return: MIME data carrying the payload of the first entry.
        :rtype: QMimeData
        """
        md = QMimeData()
        payload = items[0].data(Qt.ItemDataRole.UserRole) if items else None
        if payload:
            md.setData(MIME, str(payload).encode())
        return md


# --------------------------------------------------------------------------- blocks
class BlockItem(QGraphicsRectItem):
    """A block on the timeline (drive pulse, readout tone or acquisition window)."""

    EDGE_PX = 7

    def __init__(self, view: TimelineView, kind: str, track: object, item: object, rect: QRectF, color: str, editable: tuple[bool, bool]) -> None:
        """Create the item; ``editable`` = (start draggable, duration resizable).

        :param view: timeline view that owns the item.
        :type view: TimelineView
        :param kind: ``drive``, ``readout`` or ``acq``.
        :type kind: str
        :param track: model track.
        :type track: object
        :param item: model item (DriveBlock, Tone or AcqWindow)
        :type item: object
        :param rect: rectangle in scene coordinates.
        :type rect: QRectF
        :param color: block colour.
        :type color: str
        :param editable: (start draggable, duration resizable)
        :type editable: tuple[bool, bool]
        """
        super().__init__(rect)
        self.view, self.kind, self.track, self.item = view, kind, track, item
        self.color = QColor(color)
        self.can_move, self.can_resize = editable
        self.selected = False
        self._mode = ""
        self._press_x = 0.0
        self._orig = QRectF(rect)
        self.setAcceptHoverEvents(True)
        self.setZValue(10)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True)

    # ----------------------------------------------------------- painting
    def paint(self, p: QPainter, _opt: QStyleOptionGraphicsItem | None = None, _w: QWidget | None = None) -> None:
        """Draw the block.

        :param p: painter.
        :type p: QPainter
        :param _opt: style options (unused)
        :type _opt: QStyleOptionGraphicsItem | None
        :param _w: widget being painted (unused)
        :type _w: QWidget | None
        """
        r = self.rect()
        fill = QColor(self.color)
        fill.setAlpha(60 if self.kind != "acq" else 35)
        pen = QPen(self.color.darker(115) if self.selected else self.color, 2.4 if self.selected else 1.2)
        # dashed border marks blocks driven by macros/expressions (not fully draggable)
        if not (self.can_move and self.can_resize):
            pen.setStyle(Qt.PenStyle.DashLine)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(pen)
        p.setBrush(QBrush(fill))
        p.drawRoundedRect(r, 3, 3)
        if self.kind == "acq":
            hatch = QBrush(self.color, Qt.BrushStyle.BDiagPattern)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(hatch)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 3, 3)
        # clip the contents to the block so curves and text do not overflow
        p.save()
        p.setClipRect(r.adjusted(1, 1, -1, -1))
        if self.kind == "drive":
            self._paint_envelope(p, r)
        elif self.kind == "readout":
            self._paint_tone(p, r)
        self._paint_label(p, r)
        # small grip on the right edge shows the resize handle
        if self.selected and self.can_resize:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(self.color))
            p.drawRect(QRectF(r.right() - 3, r.top() + r.height() * 0.3, 3, r.height() * 0.4))
        p.restore()

    def _paint_envelope(self, p: QPainter, r: QRectF) -> None:
        """Draw the normalised envelope of a drive pulse (I in colour, Q in grey).

        :param p: painter.
        :type p: QPainter
        :param r: block rectangle.
        :type r: QRectF
        """
        b: DriveBlock = self.item
        if b.kind == "vz":
            return
        try:
            # rect needs only two samples; other shapes are sampled at 8..200 points
            n = 2 if b.shape == "rect" else min(max(b.n_samples, 8), 200)
            env = envelope(b.shape, n, b.sigma, b.beta, b.rise, b.expr)
        except Exception:  # noqa: BLE001 - drawing must never fail
            env = np.ones(8, dtype=complex)
        # the envelope is drawn normalised; the gain is in the label and in the inspector
        base = r.bottom() - 4
        amp = r.height() - 22
        if b.shape == "rect":
            xs, ys = np.array([0.0, 0.0, 1.0, 1.0]), np.array([0.0, 1.0, 1.0, 0.0])
            curves = [(xs, ys, self.color)]
        else:
            # sample centres in [0, 1]; Q is drawn only when it is not identically zero
            xs = (np.arange(len(env)) + 0.5) / len(env)
            curves = [(xs, env.real, self.color)]
            if np.any(np.abs(env.imag) > 1e-6):
                curves.append((xs, env.imag, QColor("#8a8f99")))
        # map normalised (x, y) into the block: x across the width, y up from the baseline
        for xs, ys, col in curves:
            path = QPainterPath()
            for k, (xv, yv) in enumerate(zip(xs, ys)):
                pt = QPointF(r.left() + xv * r.width(), base - yv * amp)
                path.moveTo(pt) if k == 0 else path.lineTo(pt)
            p.setPen(QPen(col, 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

    def _paint_tone(self, p: QPainter, r: QRectF) -> None:
        """Draw an illustrative carrier inside a readout block.

        :param p: painter.
        :type p: QPainter
        :param r: block rectangle.
        :type r: QRectF
        """
        t: Tone = self.item
        f = self.view.exp.ev(t.frequency) or 0.0
        # illustrative cycle count: grows with the width, varies with the frequency, bounded
        cycles = max(3.0, min(r.width() / 6.0, 4 + (f % 97) / 8))
        path = QPainterPath()
        mid, amp = r.center().y() + 4, (r.height() - 14) / 2.8
        n = max(int(r.width() / 2), 8)
        for k in range(n + 1):
            x = r.left() + r.width() * k / n
            y = mid - amp * math.sin(2 * math.pi * cycles * k / n)
            path.moveTo(x, y) if k == 0 else path.lineTo(x, y)
        c = QColor(self.color)
        c.setAlpha(120)
        p.setPen(QPen(c, 1.0))
        p.drawPath(path)

    def _paint_label(self, p: QPainter, r: QRectF) -> None:
        """Draw the one-line description of the block.

        :param p: painter.
        :type p: QPainter
        :param r: block rectangle.
        :type r: QRectF
        """
        exp = self.view.exp
        f = QFont(self.view.font())
        f.setPointSizeF(max(f.pointSizeF() - 1.5, 7.0))
        p.setFont(f)
        p.setPen(QPen(self.color.darker(160)))
        if self.kind == "drive":
            b: DriveBlock = self.item
            if b.kind == "vz":
                text = f"VZ {b.name}"
            else:
                text = f"{b.name} · {_fmt(exp.ev(b.carrier))} MHz · g {_fmt(exp.ev(b.gain))}"
        elif self.kind == "readout":
            t: Tone = self.item
            text = f"{t.name} · {_fmt(exp.ev(t.frequency))} MHz · {_fmt(exp.ev(t.duration))} ns"
        else:
            w: AcqWindow = self.item
            _, tone = exp.tone(w.tone_id)
            text = f"acq {w.acquisition} · {tone.name if tone else '?'} · ToF {_fmt(exp.ev(w.tof))} ns · {w.output_type}"
        p.drawText(QRectF(r.left() + 4, r.top() + 1, max(r.width() - 6, 0), 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

    # ----------------------------------------------------------- interaction
    def _near_edge(self, x: float) -> bool:
        """Tell whether a point is on the resize handle (right edge).

        :param x: scene x coordinate.
        :type x: float
        :return: True if dragging there resizes the block.
        :rtype: bool
        """
        # tolerance is EDGE_PX screen pixels converted to scene units
        tol = self.EDGE_PX / max(self.view.transform().m11(), 1e-9)
        # narrow blocks have no resize zone so they can still be moved
        return self.can_resize and self.rect().width() > 2 * tol and x > self.rect().right() - tol

    def hoverMoveEvent(self, e: QGraphicsSceneHoverEvent) -> None:  # noqa: N802
        """Show resize / move cursors.

        :param e: hover event.
        :type e: QGraphicsSceneHoverEvent
        """
        if self._near_edge(e.pos().x()):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif self.can_move:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, e: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        """Select and start a drag.

        :param e: mouse event.
        :type e: QGraphicsSceneMouseEvent
        """
        if e.button() != Qt.MouseButton.LeftButton:
            e.ignore()
            return
        self.view.select(self.item.id)
        self._orig = QRectF(self.rect())
        self._press_x = e.scenePos().x()
        # grabbing near the right edge resizes, elsewhere moves (if allowed)
        self._mode = "resize" if self._near_edge(e.pos().x()) else ("move" if self.can_move else "")
        if self._mode == "move":
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.view.begin_drag(self)
        e.accept()

    def mouseMoveEvent(self, e: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        """Move or resize, snapping to the tick grid.

        :param e: mouse event.
        :type e: QGraphicsSceneMouseEvent
        """
        if not self._mode:
            return
        view = self.view
        # mouse displacement converted from scene px to ns
        dt = (e.scenePos().x() - self._press_x) / view.scale_x
        r = QRectF(self._orig)
        if self._mode == "move":
            # new start snapped to the tick, never before the allowed minimum
            t0 = view.exp.snap_time(max(view.time_of(self._orig.left()) + dt, view.min_start(self)))
            r.moveLeft(view.x_of(t0))
        else:
            # new duration from the original span plus the displacement, at least one tick
            dur = view.time_of(self._orig.right()) - view.time_of(self._orig.left()) + dt
            dur = max(view.exp.snap_time(dur), view.exp.tick)
            r.setWidth(dur * view.scale_x)
        self.setRect(r)
        view.drag_feedback(self)

    def mouseReleaseEvent(self, e: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        """Commit the change to the model.

        :param e: mouse event.
        :type e: QGraphicsSceneMouseEvent
        """
        mode, self._mode = self._mode, ""
        self.unsetCursor()
        if not mode or self.rect() == self._orig:
            return
        view = self.view
        # convert the final rect back to ns, snapped to the tick
        start = view.exp.snap_time(view.time_of(self.rect().left()))
        dur = view.exp.snap_time(self.rect().width() / view.scale_x)
        view.commit_drag(self, start if mode == "move" else None, dur if mode == "resize" else None)

    def mouseDoubleClickEvent(self, e: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        """Focus the inspector on this block.

        :param e: mouse event.
        :type e: QGraphicsSceneMouseEvent
        """
        self.view.select(self.item.id)
        self.view.inspectRequested.emit(self.item.id)


def _fmt(v: float | None) -> str:
    """Format a number for block labels.

    :param v: value (None = unknown)
    :type v: float | None
    :return: the text (``?`` if None)
    :rtype: str
    """
    if v is None:
        return "?"
    return f"{v:.6g}" if abs(v) < 1e5 else f"{v:.4g}"


# --------------------------------------------------------------------------- view
class TimelineView(QGraphicsView):
    """Scrollable, zoomable timeline of all tracks."""

    selectionChanged = pyqtSignal(str)  # noqa: N815 - track or item id ("" = none)
    modelChanged = pyqtSignal()  # noqa: N815
    inspectRequested = pyqtSignal(str)  # noqa: N815
    hoverTime = pyqtSignal(float)  # noqa: N815

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create the view.

        :param parent: editor that owns the view (receives delete/duplicate/... requests)
        :type parent: QWidget | None
        """
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.exp: Experiment | None = None
        self.rows: list[TrackRow] = []
        self.items: dict[str, BlockItem] = {}
        self.links: list[tuple[QGraphicsPathItem, str]] = []
        self.selected_id = ""
        self.scale_x = 0.5  # px per ns
        self.events: list = []
        self.shot_end = 0.0
        self.content_end = 0.0
        self._link_base: dict[str, QRectF] = {}
        self.editor = parent  # object implementing delete/duplicate/copy_to_acquisition/toggle_tone
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        # full updates because headers and ruler are fixed in viewport coordinates
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setViewportMargins(0, 0, 0, 0)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # repaint on scroll so the fixed headers and ruler are redrawn
        self.horizontalScrollBar().valueChanged.connect(lambda _: self.viewport().update())
        self.verticalScrollBar().valueChanged.connect(lambda _: self.viewport().update())

    # ----------------------------------------------------------- coordinates
    def x_of(self, t: float) -> float:
        """Return the scene x of a time in ns.

        :param t: time in ns.
        :type t: float
        :return: the scene x coordinate.
        :rtype: float
        """
        # time 0 starts right after the header column
        return HEADER_W + t * self.scale_x

    def time_of(self, x: float) -> float:
        """Return the time in ns of a scene x.

        :param x: scene x coordinate.
        :type x: float
        :return: the time in ns.
        :rtype: float
        """
        return (x - HEADER_W) / self.scale_x

    def row_at(self, scene_y: float) -> TrackRow | None:
        """Return the track row under a scene y.

        :param scene_y: scene y coordinate.
        :type scene_y: float
        :return: the row, or None between/after the tracks.
        :rtype: TrackRow | None
        """
        return next((r for r in self.rows if r.y <= scene_y < r.y + r.h), None)

    def min_start(self, item: BlockItem) -> float:
        """Return the minimum start time allowed for a block (windows: start of their tone).

        :param item: dragged block.
        :type item: BlockItem
        :return: the earliest start in ns.
        :rtype: float
        """
        if item.kind == "acq":
            _, tone = self.exp.tone(item.item.tone_id)
            if tone is not None:
                return self.exp.ev(tone.start) or 0.0
        return 0.0

    # ----------------------------------------------------------- building
    def set_experiment(self, exp: Experiment) -> None:
        """Attach an experiment and draw it.

        :param exp: experiment to show.
        :type exp: Experiment
        """
        self.exp = exp
        self.rebuild()

    def rebuild(self) -> None:  # noqa: PLR0915
        """Recreate the scene from the model (keeps scroll and selection)."""
        exp = self.exp
        sc = self.scene()
        # remember the scroll position, restored after the scene is rebuilt
        hval, vval = self.horizontalScrollBar().value(), self.verticalScrollBar().value()
        sc.clear()
        self.items.clear()
        self.links.clear()
        self.rows = []
        if exp is None:
            return
        # lay out the rows below the ruler: drive, then readout, then acquisition
        y = RULER_H + TRACK_GAP
        for tr in exp.drive_tracks:
            self.rows.append(TrackRow(tr, "drive", y, DRIVE_H, 1))
            y += DRIVE_H + TRACK_GAP
        for tr in exp.readout_tracks:
            lanes = max(len(tr.tones), 1)
            h = lanes * LANE_H + 2 * TRACK_PAD
            self.rows.append(TrackRow(tr, "readout", y, h, lanes))
            y += h + TRACK_GAP
        for tr in exp.acq_tracks:
            lanes = max(len(tr.windows), 1)
            h = lanes * LANE_H + 2 * TRACK_PAD
            self.rows.append(TrackRow(tr, "acq", y, h, lanes))
            y += h + TRACK_GAP
        total_h = y + 40

        # trigger events and shot length are evaluated at the sweep preview point
        pt = exp.preview_values()
        ex = Exporter(exp)
        try:
            self.events = ex.events(pt)
            self.shot_end = float(exp.ev(exp.experiment_duration) or 0.0) if exp.experiment_duration is not None else ex.auto_duration()
        except Exception:  # noqa: BLE001
            self.events, self.shot_end = [], 0.0
        t_max = max([self.shot_end] + [0.0])
        self.content_end = 0.0
        for row in self.rows:
            for _, it in self._row_items(row):
                s, d = exp.ev(self._start(row, it), pt), exp.ev(self._dur(it), pt)
                if s is not None and d is not None:
                    t_max = max(t_max, s + d)
                # Fit covers the whole sweep range, not only the preview point
                for c in exp.corner_values():
                    sc_, dc_ = exp.ev(self._start(row, it), c), exp.ev(self._dur(it), c)
                    if sc_ is not None and dc_ is not None:
                        self.content_end = max(self.content_end, sc_ + dc_)
        # scene width with a 15% margin plus some slack after the last block
        width = self.x_of(t_max * 1.15 + 200)
        sc.setSceneRect(0, 0, max(width, self.viewport().width()), max(total_h, self.viewport().height()))

        for row in self.rows:
            for lane, it in self._row_items(row):
                self._add_block(row, lane, it, pt)
        self._add_links()
        self.horizontalScrollBar().setValue(hval)
        self.verticalScrollBar().setValue(vval)
        self.viewport().update()

    def _row_items(self, row: TrackRow) -> list[tuple[int, object]]:
        """Return the items of a row with their lane.

        :param row: track row.
        :type row: TrackRow
        :return: (lane, item) pairs.
        :rtype: list[tuple[int, object]]
        """
        if row.kind == "drive":
            return [(0, b) for b in row.track.blocks]
        if row.kind == "readout":
            return list(enumerate(row.track.tones))
        return list(enumerate(row.track.windows))

    def _start(self, row: TrackRow, it: object) -> object:
        """Return the start of an item (windows: tone start + time of flight).

        :param row: track row.
        :type row: TrackRow
        :param it: model item.
        :type it: object
        :return: the start (number or expression)
        :rtype: object
        """
        if row.kind == "acq":
            return Exporter(self.exp).window_start(it)
        return it.start

    @staticmethod
    def _dur(it: object) -> object:
        """Return the duration of an item (0 for VZ gates).

        :param it: model item.
        :type it: object
        :return: the duration (number or expression)
        :rtype: object
        """
        return 0.0 if isinstance(it, DriveBlock) and it.kind == "vz" else it.duration

    def _block_rect(self, row: TrackRow, lane: int, start: float, dur: float) -> QRectF:
        """Return the scene rectangle of a block.

        :param row: track row.
        :type row: TrackRow
        :param lane: lane index inside the row.
        :type lane: int
        :param start: start in ns.
        :type start: float
        :param dur: duration in ns.
        :type dur: float
        :return: the rectangle.
        :rtype: QRectF
        """
        if row.kind == "drive":
            top, h = row.y + 6, row.h - 12
        else:
            top, h = row.y + TRACK_PAD + lane * LANE_H + 2, LANE_H - 4
        # minimum width keeps zero-length blocks visible and clickable
        return QRectF(self.x_of(start), top, max(dur * self.scale_x, 3.0), h)

    def _add_block(self, row: TrackRow, lane: int, it: object, pt: dict) -> None:
        """Create the graphics item of a model item.

        :param row: track row.
        :type row: TrackRow
        :param lane: lane index.
        :type lane: int
        :param it: model item.
        :type it: object
        :param pt: sweep preview point.
        :type pt: dict
        """
        exp = self.exp
        start_v = self._start(row, it)
        s, d = exp.ev(start_v, pt), exp.ev(self._dur(it), pt)
        if s is None or d is None:
            return
        if row.kind == "acq":
            # windows move via their ToF; colour comes from the readout track of their tone
            editable = (_num(it.tof), _num(it.duration))
            _, tone = exp.tone(it.tone_id)
            color = "#555555" if tone is None else next((r.track.color for r in self.rows if r.kind == "readout" and tone in r.track.tones), "#555555")
        elif isinstance(it, DriveBlock) and it.kind == "vz":
            editable = (_num(it.start), False)
            color = "#7a5cd6"
            # VZ gates have no duration: draw them as a fixed 4 px marker
            d = 4 / self.scale_x
        else:
            editable = (_num(it.start), _num(it.duration))
            color = row.track.color
        if row.kind == "readout" and not it.enabled:
            color = "#b0b4bb"
        item = BlockItem(self, {"drive": "drive", "readout": "readout", "acq": "acq"}[row.kind], row.track, it, self._block_rect(row, lane, s, d), color, editable)
        item.selected = it.id == self.selected_id
        tip = {"drive": "Drag to move, drag the right edge to resize. Double-click: properties."}
        item.setToolTip(tip.get(row.kind, "Drag to move, drag the right edge to resize.") + ("" if all(editable) else "\nDashed: value set by a macro/expression, edit it in the inspector."))
        self.scene().addItem(item)
        self.items[it.id] = item

    def _add_links(self) -> None:
        """Dashed arrows from each tone to its acquisition windows (length = ToF)."""
        for row in self.rows:
            if row.kind != "acq":
                continue
            for w in row.track.windows:
                path_item = QGraphicsPathItem()
                pen = QPen(QColor("#6b7280"), 1.2, Qt.PenStyle.DashLine)
                path_item.setPen(pen)
                path_item.setZValue(5)
                self.scene().addItem(path_item)
                self.links.append((path_item, w.id))
        self._update_links()

    def _update_links(self) -> None:
        """Redraw the tone -> window arrows from the current block positions."""
        for path_item, wid in self.links:
            _, w = self.exp.find(wid)
            src = self.items.get(w.tone_id) if w else None
            dst = self.items.get(wid)
            if src is None or dst is None:
                path_item.setPath(QPainterPath())
                continue
            # S curve from the bottom-left of the tone to the top-left of the window
            a = QPointF(src.rect().left(), src.rect().bottom())
            b = QPointF(dst.rect().left(), dst.rect().top())
            path = QPainterPath(a)
            mid_y = (a.y() + b.y()) / 2
            path.cubicTo(QPointF(a.x(), mid_y), QPointF(b.x(), mid_y), b)
            # arrow head
            path.moveTo(b)
            path.lineTo(b + QPointF(-4, -7))
            path.moveTo(b)
            path.lineTo(b + QPointF(4, -7))
            path_item.setPath(path)

    # ----------------------------------------------------------- drag feedback / commit
    def drag_feedback(self, item: BlockItem) -> None:
        """Live update while dragging: windows follow their tone, links are redrawn.

        :param item: block being dragged.
        :type item: BlockItem
        """
        # dragging a tone moves its windows by the same offset (ToF unchanged)
        if item.kind == "readout":
            dx = item.rect().left() - item._orig.left()
            for wid, base in self._link_base.items():
                wi = self.items.get(wid)
                if wi is not None:
                    wi.setRect(base.translated(dx, 0))
        self._update_links()
        self.hoverTime.emit(self.time_of(item.rect().left()))
        self.viewport().update()

    def begin_drag(self, item: BlockItem) -> None:
        """Remember the windows linked to a tone before it is dragged.

        :param item: block about to be dragged.
        :type item: BlockItem
        """
        self._link_base = {}
        if item.kind == "readout":
            for _, w in self.exp.windows_of(item.item.id):
                wi = self.items.get(w.id)
                if wi is not None:
                    self._link_base[w.id] = QRectF(wi.rect())

    def commit_drag(self, item: BlockItem, start: float | None, duration: float | None) -> None:
        """Write a drag result into the model.

        :param item: dragged block.
        :type item: BlockItem
        :param start: new start in ns (None = unchanged)
        :type start: float | None
        :param duration: new duration in ns (None = unchanged)
        :type duration: float | None
        """
        it = item.item
        # windows store the ToF relative to their tone, not an absolute start
        if item.kind == "acq":
            _, tone = self.exp.tone(it.tone_id)
            if start is not None and tone is not None:
                t0 = self.exp.ev(tone.start) or 0.0
                it.tof = self.exp.snap_time(max(start - t0, 0.0))
        elif start is not None:
            it.start = start
        if duration is not None:
            it.duration = duration
        self.modelChanged.emit()

    # ----------------------------------------------------------- selection
    def select(self, item_id: str) -> None:
        """Select a track or an item.

        :param item_id: id to select ('' = nothing)
        :type item_id: str
        """
        if item_id == self.selected_id:
            self.selectionChanged.emit(item_id)
            return
        old = self.items.get(self.selected_id)
        if old is not None:
            old.selected = False
            old.update()
        self.selected_id = item_id
        new = self.items.get(item_id)
        if new is not None:
            new.selected = True
            new.update()
        self.viewport().update()
        self.selectionChanged.emit(item_id)

    # ----------------------------------------------------------- zoom
    def set_scale(self, scale: float, anchor_view_x: float | None = None) -> None:
        """Set the zoom (px per ns), keeping the time under the anchor fixed.

        :param scale: pixels per ns.
        :type scale: float
        :param anchor_view_x: viewport x that keeps its time (None = centre)
        :type anchor_view_x: float | None
        """
        scale = min(max(scale, MIN_SCALE), MAX_SCALE)
        if anchor_view_x is None:
            anchor_view_x = (self.viewport().width() + HEADER_W) / 2
        # time under the anchor before zooming
        t = self.time_of(self.mapToScene(int(anchor_view_x), 0).x())
        self.scale_x = scale
        self.rebuild()
        # scroll so that the same time is back under the anchor
        new_x = self.x_of(t)
        self.horizontalScrollBar().setValue(int(new_x - anchor_view_x))

    def fit(self) -> None:
        """Zoom to show the whole shot."""
        if self.exp is None:
            return
        # fit the sequence; the end of the shot is shown too when it is not much later
        end = self.content_end or self.shot_end
        if self.shot_end and self.shot_end < 1.5 * end:
            end = self.shot_end
        # at least 100 ns plus a 6% margin, over the width right of the header
        span = max(end, 100.0) * 1.06
        avail = max(self.viewport().width() - HEADER_W - 30, 100)
        self.scale_x = min(max(avail / span, MIN_SCALE), MAX_SCALE)
        self.rebuild()
        self.horizontalScrollBar().setValue(0)

    def wheelEvent(self, e: QWheelEvent) -> None:  # noqa: N802
        """Ctrl+wheel zooms, Shift+wheel scrolls horizontally.

        :param e: wheel event.
        :type e: QWheelEvent
        """
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.25 if e.angleDelta().y() > 0 else 0.8
            self.set_scale(self.scale_x * factor, e.position().x())
            e.accept()
        elif e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            sb = self.horizontalScrollBar()
            sb.setValue(sb.value() - e.angleDelta().y())
            e.accept()
        else:
            super().wheelEvent(e)

    # ----------------------------------------------------------- mouse on empty areas
    def _scene_pos(self, view_pos: QPointF) -> QPointF:
        """Map a viewport position to the scene.

        :param view_pos: viewport position.
        :type view_pos: QPointF
        :return: the scene position.
        :rtype: QPointF
        """
        return self.mapToScene(int(view_pos.x()), int(view_pos.y()))

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        """Header click selects the track; empty click clears the selection.

        :param e: mouse event.
        :type e: QMouseEvent
        """
        pos = e.position()
        # clicks on the fixed header column select the track of that row
        if pos.x() < HEADER_W and pos.y() > RULER_H:
            row = self.row_at(self._scene_pos(pos).y())
            if row is not None:
                self.select(row.track.id)
                return
        item = self.itemAt(int(pos.x()), int(pos.y()))
        if item is None and e.button() == Qt.MouseButton.LeftButton:
            row = self.row_at(self._scene_pos(pos).y())
            self.select(row.track.id if row else "")
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        """Report the time under the cursor.

        :param e: mouse event.
        :type e: QMouseEvent
        """
        sp = self._scene_pos(e.position())
        if e.position().x() > HEADER_W:
            self.hoverTime.emit(self.time_of(sp.x()))
        super().mouseMoveEvent(e)

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        """Double-click on an empty track adds a block there.

        :param e: mouse event.
        :type e: QMouseEvent
        """
        pos = e.position()
        if self.itemAt(int(pos.x()), int(pos.y())) is not None or pos.x() < HEADER_W:
            super().mouseDoubleClickEvent(e)
            return
        sp = self._scene_pos(pos)
        row = self.row_at(sp.y())
        if row is not None:
            self.add_at(row, self.time_of(sp.x()), "pulse:gaussian" if row.kind == "drive" else "tone")

    def add_at(self, row: TrackRow, t: float, payload: str) -> None:
        """Add a block of kind ``payload`` on a row at time t.

        :param row: target row.
        :type row: TrackRow
        :param t: time in ns (snapped)
        :type t: float
        :param payload: ``pulse:<shape>``, ``vz`` or ``tone``.
        :type payload: str
        """
        exp = self.exp
        t = exp.snap_time(max(t, 0.0))
        new = None
        if row.kind == "drive" and payload.startswith("pulse:"):
            new = exp.add_drive_block(row.track, t, shape=payload.split(":", 1)[1])
        elif row.kind == "drive" and payload == "vz":
            new = exp.add_drive_block(row.track, t, kind="vz")
        elif row.kind == "readout" and payload == "tone":
            new = exp.add_tone(row.track, t)
            new.start = t
        if new is not None:
            self.selected_id = new.id
            self.modelChanged.emit()
            self.selectionChanged.emit(new.id)

    # ----------------------------------------------------------- drag & drop from the palette
    def dragEnterEvent(self, e: QDragEnterEvent) -> None:  # noqa: N802
        """Accept palette blocks.

        :param e: drag event.
        :type e: QDragEnterEvent
        """
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e: QDragMoveEvent) -> None:  # noqa: N802
        """Accept the drop only over a compatible track.

        :param e: drag event.
        :type e: QDragMoveEvent
        """
        if not e.mimeData().hasFormat(MIME):
            e.ignore()
            return
        payload = bytes(e.mimeData().data(MIME)).decode()
        row = self.row_at(self._scene_pos(e.position()).y())
        # pulses and VZ gates go on drive tracks, tones on readout tracks
        ok = row is not None and ((row.kind == "drive" and payload != "tone") or (row.kind == "readout" and payload == "tone"))
        if ok:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e: QDropEvent) -> None:  # noqa: N802
        """Create the dropped block.

        :param e: drop event.
        :type e: QDropEvent
        """
        payload = bytes(e.mimeData().data(MIME)).decode()
        sp = self._scene_pos(e.position())
        row = self.row_at(sp.y())
        if row is not None:
            self.add_at(row, self.time_of(sp.x()), payload)
            e.acceptProposedAction()

    # ----------------------------------------------------------- context menu / keys
    def contextMenuEvent(self, e: QContextMenuEvent) -> None:  # noqa: N802
        """Context actions for blocks and tracks.

        :param e: context menu event.
        :type e: QContextMenuEvent
        """
        pos = QPointF(e.pos())
        item = self.itemAt(e.pos())
        sp = self._scene_pos(pos)
        row = self.row_at(sp.y())
        menu = QMenu(self)
        owner = self.editor
        # block menu: the actions depend on the block kind
        if isinstance(item, BlockItem):
            self.select(item.item.id)
            if item.kind == "readout":
                menu.addAction("Copy to acquisition", lambda: owner.copy_to_acquisition(item.item.id))
            if item.kind == "drive":
                menu.addAction("Duplicate (new gate)", lambda: owner.duplicate(item.item.id, same_gate=False))
                menu.addAction("Repeat later (same gate)", lambda: owner.duplicate(item.item.id, same_gate=True))
            if item.kind == "readout":
                menu.addAction("Disable tone" if item.item.enabled else "Enable tone", lambda: owner.toggle_tone(item.item.id))
            menu.addSeparator()
            menu.addAction("Delete", lambda: owner.delete(item.item.id))
        # empty track area: add a block at the clicked time or delete the track
        elif row is not None:
            t = self.time_of(sp.x())
            if row.kind == "drive":
                sub = menu.addMenu("Add pulse here")
                for key, label in SHAPES.items():
                    sub.addAction(label, lambda k=key: self.add_at(row, t, f"pulse:{k}"))
                menu.addAction("Add virtual Z here", lambda: self.add_at(row, t, "vz"))
            elif row.kind == "readout":
                menu.addAction("Add tone here", lambda: self.add_at(row, t, "tone"))
            menu.addSeparator()
            menu.addAction(f"Delete track {row.track.title}", lambda: owner.delete(row.track.id))
        if not menu.isEmpty():
            menu.exec(e.globalPos())

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        """Delete removes the selection; +/- zoom.

        :param e: key event.
        :type e: QKeyEvent
        """
        owner = self.editor
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.selected_id:
            owner.delete(self.selected_id)
        elif e.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.set_scale(self.scale_x * 1.25)
        elif e.key() == Qt.Key.Key_Minus:
            self.set_scale(self.scale_x * 0.8)
        else:
            super().keyPressEvent(e)

    def resizeEvent(self, e: QResizeEvent) -> None:  # noqa: N802
        """Keep the scene at least as large as the viewport.

        :param e: resize event.
        :type e: QResizeEvent
        """
        super().resizeEvent(e)
        if self.exp is not None:
            r = self.scene().sceneRect()
            self.scene().setSceneRect(0, 0, max(r.width(), self.viewport().width()), max(r.height(), self.viewport().height()))

    # ----------------------------------------------------------- background / foreground
    def _ruler_step(self) -> float:
        """Return a 'nice' label step in ns for the current zoom.

        :return: the step (1, 2, 5 x 10^n ns) giving about 90 px between labels.
        :rtype: float
        """
        # smallest 1-2-5 step giving at least ~90 px between labels
        target = 90 / self.scale_x
        for s in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000, 200000, 500000, 1e6):
            if s >= target:
                return float(s)
        return 1e6

    def drawBackground(self, p: QPainter, rect: QRectF) -> None:  # noqa: N802
        """Track bands and time grid (scene coordinates).

        :param p: painter (scene coordinates)
        :type p: QPainter
        :param rect: exposed scene area.
        :type rect: QRectF
        """
        p.fillRect(rect, QColor("#ffffff"))
        for i, row in enumerate(self.rows):
            band = QRectF(rect.left(), row.y, rect.width(), row.h)
            p.fillRect(band, KIND_BG[row.kind] if i % 2 == 0 else KIND_BG[row.kind].darker(102))
            if row.kind != "drive":
                p.setPen(QPen(QColor("#e6e8ec"), 1, Qt.PenStyle.DotLine))
                for k in range(1, row.lanes):
                    y = row.y + TRACK_PAD + k * LANE_H
                    p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        exp = self.exp
        if exp is None:
            return
        t0, t1 = max(self.time_of(rect.left()), 0.0), self.time_of(rect.right())
        tick_px = exp.tick * self.scale_x
        if tick_px >= 6:  # FPGA tick grid visible when zoomed in
            p.setPen(QPen(QColor("#eef0f4"), 1))
            # start from the first tick inside the exposed area
            k0 = int(t0 / exp.tick)
            k = k0
            while k * exp.tick <= t1:
                x = self.x_of(k * exp.tick)
                p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                k += 1
        step = self._ruler_step()
        p.setPen(QPen(QColor("#dfe3ea"), 1))
        t = math.floor(t0 / step) * step
        while t <= t1:
            x = self.x_of(t)
            p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            t += step
        # trigger events and end of shot
        p.setPen(QPen(QColor("#c4c9d2"), 1, Qt.PenStyle.DotLine))
        for e in self.events:
            if not math.isnan(e.preview):
                x = self.x_of(e.preview)
                p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        if self.shot_end:
            x = self.x_of(self.shot_end)
            p.setPen(QPen(QColor("#d0453b"), 1.5, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))

    def drawForeground(self, p: QPainter, rect: QRectF) -> None:  # noqa: N802
        """Fixed ruler and track headers, painted in viewport coordinates.

        :param p: painter (reset to viewport coordinates)
        :type p: QPainter
        :param rect: exposed scene area.
        :type rect: QRectF
        """
        exp = self.exp
        p.save()
        # draw in viewport pixels so headers and ruler stay fixed while scrolling
        p.resetTransform()
        vw, vh = self.viewport().width(), self.viewport().height()
        f = QFont(self.font())
        small = QFont(f)
        small.setPointSizeF(max(f.pointSizeF() - 1.5, 7.0))
        # ---- headers
        p.fillRect(QRectF(0, 0, HEADER_W, vh), QColor("#eef1f5"))
        p.setPen(QPen(QColor("#cfd4dc"), 1))
        p.drawLine(QPointF(HEADER_W - 0.5, 0), QPointF(HEADER_W - 0.5, vh))
        for row in self.rows:
            # row top mapped from scene to viewport; skip rows hidden under the ruler or below
            top = self.mapFromScene(QPointF(0, row.y)).y()
            r = QRectF(0, top, HEADER_W - 1, row.h)
            if r.bottom() < RULER_H or r.top() > vh:
                continue
            tr = row.track
            sel = tr.id == self.selected_id
            p.fillRect(r, QColor("#dbe7fb") if sel else QColor("#eef1f5"))
            p.fillRect(QRectF(0, top, 5, row.h), QColor(tr.color))
            p.setPen(QColor("#1f2733"))
            bold = QFont(f)
            bold.setBold(True)
            p.setFont(bold)
            p.drawText(QRectF(12, top + 4, HEADER_W - 16, 18), Qt.AlignmentFlag.AlignLeft, tr.title)
            p.setFont(small)
            p.setPen(QColor("#5b6472"))
            p.drawText(QRectF(12, top + 21, HEADER_W - 16, 14), Qt.AlignmentFlag.AlignLeft, self._subtitle(row))
            # per-lane labels, only where they do not overlap the subtitle
            if row.kind != "drive":
                for k, it in enumerate(self._row_items(row)):
                    _, obj = it
                    ly = top + TRACK_PAD + k * LANE_H
                    if row.lanes > 1 and ly + LANE_H - 2 > top + 36:
                        label = obj.name if row.kind == "readout" else f"acq {obj.acquisition}"
                        p.drawText(QRectF(60, ly + 8, HEADER_W - 64, 14), Qt.AlignmentFlag.AlignRight, label)
            p.setPen(QPen(QColor("#d5d9e0"), 1))
            p.drawLine(QPointF(0, r.bottom() + TRACK_GAP / 2), QPointF(HEADER_W, r.bottom() + TRACK_GAP / 2))
        # ---- ruler
        p.fillRect(QRectF(0, 0, vw, RULER_H), QColor("#f7f8fa"))
        p.setPen(QPen(QColor("#cfd4dc"), 1))
        p.drawLine(QPointF(0, RULER_H - 0.5), QPointF(vw, RULER_H - 0.5))
        if exp is not None:
            p.setFont(small)
            left_t = self.time_of(self.mapToScene(HEADER_W, 0).x())
            right_t = self.time_of(self.mapToScene(vw, 0).x())
            step = self._ruler_step()
            # major ticks with labels, hidden under the header column
            t = math.floor(max(left_t, 0) / step) * step
            while t <= right_t:
                x = self.mapFromScene(QPointF(self.x_of(t), 0)).x()
                if x >= HEADER_W:
                    p.setPen(QColor("#4a5566"))
                    p.drawLine(QPointF(x, RULER_H - 8), QPointF(x, RULER_H - 1))
                    p.drawText(QRectF(x + 3, 2, 90, 14), Qt.AlignmentFlag.AlignLeft, f"{t:g} ns")
                # four minor ticks between labels
                for m in range(1, 5):
                    xm = self.mapFromScene(QPointF(self.x_of(t + m * step / 5), 0)).x()
                    if xm >= HEADER_W:
                        p.setPen(QColor("#9aa3b0"))
                        p.drawLine(QPointF(xm, RULER_H - 4), QPointF(xm, RULER_H - 1))
                t += step
            # trigger markers: triangles coloured by trigger type, labelled T1, T2, ...
            last_label_x = -1e9
            for i, e in enumerate(self.events):
                if math.isnan(e.preview):
                    continue
                x = self.mapFromScene(QPointF(self.x_of(e.preview), 0)).x()
                if x < HEADER_W:
                    continue
                col = QColor("#2f6fdf") if e.ttype == "drive" else QColor("#2a9d6f")
                tri = QPainterPath()
                tri.moveTo(x - 5, 16)
                tri.lineTo(x + 5, 16)
                tri.lineTo(x, RULER_H - 2)
                tri.closeSubpath()
                p.fillPath(tri, col)
                if x - last_label_x > 26:  # avoid overlapping labels of close triggers
                    p.setPen(col)
                    p.drawText(QRectF(x + 6, 15, 40, 14), Qt.AlignmentFlag.AlignLeft, f"T{i + 1}")
                    last_label_x = x
            if self.shot_end:
                x = self.mapFromScene(QPointF(self.x_of(self.shot_end), 0)).x()
                if x >= HEADER_W:
                    p.setPen(QColor("#d0453b"))
                    p.drawText(QRectF(x - 80, 15, 76, 14), Qt.AlignmentFlag.AlignRight, "end of shot")
            p.setPen(QColor("#4a5566"))
            p.drawText(QRectF(6, 4, HEADER_W - 10, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"1 tick = {exp.tick:.3f} ns")
        p.restore()

    def _subtitle(self, row: TrackRow) -> str:
        """Return the second line of a track header.

        :param row: track row.
        :type row: TrackRow
        :return: e.g. ``drive · gen 0 · trig 1``.
        :rtype: str
        """
        tr = row.track
        if row.kind == "drive":
            return f"drive · gen {tr.generator} · trig {tr.trigger_channel}"
        if row.kind == "readout":
            n = len([t for t in tr.tones if t.enabled])
            return f"readout · {n} tone{'s' if n != 1 else ''}"
        return f"acquisition · {len(tr.windows)} window{'s' if len(tr.windows) != 1 else ''}"

