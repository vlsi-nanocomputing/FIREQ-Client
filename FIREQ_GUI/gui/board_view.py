"""Board panel: converter channel map, IP allocation and Nyquist zone suggestions."""

from __future__ import annotations

from ..core.boards import Board, Connector
from ..core.model import Experiment
from ..core.validation import nyquist_hints
from .qt import (
    QBrush,
    QColor,
    QEvent,
    QFont,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPaintEvent,
    QPainter,
    QPen,
    QPushButton,
    QRectF,
    QScrollArea,
    QSize,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    Qt,
    pyqtSignal,
)

# connectors drawn per row and size of one connector cell in pixels
PER_ROW = 8
CELL_W, CELL_H = 86, 78


class ConnectorMap(QWidget):
    """Draws the converter channels of a board, coloured by the tracks using them."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create the map.

        :param parent: parent widget.
        :type parent: QWidget | None
        """
        super().__init__(parent)
        self.board: Board | None = None
        self.usage: dict[str, list[tuple[str, str]]] = {}
        # clickable areas of the connectors with their tooltip text, rebuilt on every paint
        self._hits: list[tuple[QRectF, str]] = []
        self.setMouseTracking(True)

    def set_data(self, board: Board, usage: dict[str, list[tuple[str, str]]]) -> None:
        """Set board and channel usage {name: [(track label, colour)]}.

        :param board: board profile.
        :type board: Board
        :param usage: per channel label: (use, colour) of the tracks using it.
        :type usage: dict[str, list[tuple[str, str]]]
        """
        self.board, self.usage = board, usage
        self.updateGeometry()
        self.update()

    def _rows(self, n: int) -> int:
        """Return how many rows ``n`` connectors take.

        :param n: number of connectors.
        :type n: int
        :return: the number of rows.
        :rtype: int
        """
        return max(1, (n + PER_ROW - 1) // PER_ROW)

    def sizeHint(self) -> QSize:  # noqa: N802
        """Return the preferred size.

        :return: the size that shows every connector.
        :rtype: QSize
        """
        if self.board is None:
            return QSize(400, 200)
        # one block of rows for the DACs and one for the ADCs, plus room for titles and margins
        rows = self._rows(len(self.board.dacs)) + self._rows(len(self.board.adcs))
        return QSize(PER_ROW * CELL_W + 40, rows * CELL_H + 90)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """Return the minimum size.

        :return: the same as :meth:`sizeHint`.
        :rtype: QSize
        """
        return self.sizeHint()

    def paintEvent(self, _ev: QPaintEvent) -> None:  # noqa: N802
        """Paint the board.

        :param _ev: paint event (unused)
        :type _ev: QPaintEvent
        """
        if self.board is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # board outline: rounded green rectangle inset from the widget border
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        p.setPen(QPen(QColor("#2b5d34"), 2))
        p.setBrush(QBrush(QColor("#e9f2ea")))
        p.drawRoundedRect(r, 10, 10)
        # board title in a slightly larger bold font
        p.setPen(QColor("#2b5d34"))
        f = QFont(self.font())
        f.setBold(True)
        f.setPointSize(f.pointSize() + 2)
        p.setFont(f)
        p.drawText(QRectF(r.left() + 14, r.top() + 6, r.width() - 28, 24), Qt.AlignmentFlag.AlignLeft, self.board.title)
        # reset hit areas, then draw the DAC group with the ADC group just below it
        self._hits = []
        y = r.top() + 36
        y = self._section(p, "DAC channels (outputs)", self.board.dacs, r.left() + 14, y)
        self._section(p, "ADC channels (inputs)", self.board.adcs, r.left() + 14, y + 8)
        p.end()

    def _section(self, p: QPainter, title: str, conns: list[Connector], x0: float, y0: float) -> float:
        """Draw one group of connectors (DAC or ADC).

        :param p: painter.
        :type p: QPainter
        :param title: group title.
        :type title: str
        :param conns: connectors to draw.
        :type conns: list[Connector]
        :param x0: left position.
        :type x0: float
        :param y0: top position.
        :type y0: float
        :return: the y coordinate after the group.
        :rtype: float
        """
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#2b5d34"))
        p.drawText(QRectF(x0, y0, 300, 16), Qt.AlignmentFlag.AlignLeft, title)
        # smaller font for the labels under each connector
        small = QFont(self.font())
        small.setPointSize(max(small.pointSize() - 1, 7))
        y0 += 18
        for i, c in enumerate(conns):
            # centre of the connector cell in the grid
            cx = x0 + (i % PER_ROW) * CELL_W + CELL_W / 2
            cy = y0 + (i // PER_ROW) * CELL_H + 20
            users = self.usage.get(c.label, [])
            # outer ring: gold if routed to an SMA, grey otherwise
            outer = QRectF(cx - 15, cy - 15, 30, 30)
            p.setPen(QPen(QColor("#8a6d1d"), 1.5))
            p.setBrush(QBrush(QColor("#e8c867") if c.routed else QColor("#d6d6d6")))
            p.drawEllipse(outer)
            inner = QRectF(cx - 8, cy - 8, 16, 16)
            # inner disc split into equal pie slices, one per track using the channel
            if users:
                n = len(users)
                for k, (_, col) in enumerate(users):
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QBrush(QColor(col)))
                    # qt angles are in 1/16 degree: start at 12 o'clock and go clockwise
                    p.drawPie(inner, int(90 * 16 - k * 5760 / n), int(-5760 / n))
            else:
                p.setPen(QPen(QColor("#777"), 1))
                p.setBrush(QBrush(QColor("#ffffff")))
                p.drawEllipse(inner)
            # connector name (and SMA label) below the ring
            p.setFont(small)
            p.setPen(QColor("#222") if c.routed else QColor("#999"))
            p.drawText(QRectF(cx - CELL_W / 2, cy + 17, CELL_W, 14), Qt.AlignmentFlag.AlignHCenter, c.name + (f" ({c.sma})" if c.sma else ""))
            # second label: tracks using the channel, or its free / not routed state
            sub = ", ".join(u for u, _ in users) if users else ("free" if c.routed else "not routed")
            p.setPen(QColor(users[0][1]) if users else QColor("#888"))
            p.drawText(QRectF(cx - CELL_W / 2, cy + 30, CELL_W, 14), Qt.AlignmentFlag.AlignHCenter, _elide(sub, 14))
            # tooltip with tile/block, routing and usage details
            tip = f"{c.label} — tile {c.tile}, block {c.block}" + (f", SMA {c.sma}" if c.sma else "")
            if c.kind == "dac":
                tip += f"\ncrossbar bit: {c.crossbar_bit if c.crossbar_bit is not None else 'not connected'}"
            else:
                tip += f"\nacquisition IP: {'?' if c.acq_ips is None else (c.acq_ips or 'none')}"
            if c.note:
                tip += f"\nnote: {c.note}"
            if users:
                tip += "\nused by: " + ", ".join(u for u, _ in users)
            # hit area covers the whole cell, used by event() to show the tooltip
            self._hits.append((QRectF(cx - CELL_W / 2, cy - 18, CELL_W, CELL_H - 10), tip))
        return y0 + self._rows(len(conns)) * CELL_H

    def event(self, ev: QEvent) -> bool:
        """Show connector tooltips.

        :param ev: widget event.
        :type ev: QEvent
        :return: True if the event was handled.
        :rtype: bool
        """
        from PyQt6.QtWidgets import QToolTip

        # show the tooltip of the connector under the mouse, if any
        if ev.type() == QEvent.Type.ToolTip:
            pos = ev.position() if hasattr(ev, "position") else ev.pos()
            for rect, tip in self._hits:
                if rect.contains(pos.x(), pos.y()):
                    QToolTip.showText(ev.globalPos(), tip, self)
                    return True
            QToolTip.hideText()
            return True
        return super().event(ev)


def _elide(s: str, n: int) -> str:
    """Shorten a text with an ellipsis.

    :param s: text.
    :type s: str
    :param n: maximum length.
    :type n: int
    :return: the text, at most ``n`` characters.
    :rtype: str
    """
    return s if len(s) <= n else s[: n - 1] + "…"


class BoardPanel(QScrollArea):
    """Board tab: channel map, IP allocation, Nyquist suggestions."""

    nyquistRequested = pyqtSignal(int, int, int)  # noqa: N815

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the panel.

        :param parent: parent widget.
        :type parent: QWidget | None
        """
        super().__init__(parent)
        # scrollable body: connector map, IP table and Nyquist table stacked vertically
        self.setWidgetResizable(True)
        body = QWidget()
        self.setWidget(body)
        lay = QVBoxLayout(body)
        self.map = ConnectorMap()
        lay.addWidget(self.map)

        # read-only table of generator and acquisition IPs
        ib = QGroupBox("FIREQ IP allocation")
        il = QVBoxLayout(ib)
        self.ips = QTableWidget(0, 3)
        self.ips.setHorizontalHeaderLabels(["IP", "Drive / readout use", "Channels"])
        self.ips.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.ips.verticalHeader().setVisible(False)
        self.ips.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        il.addWidget(self.ips)
        lay.addWidget(ib)

        # read-only table of suggested Nyquist zones with a per-row send button
        nb = QGroupBox("Suggested Nyquist zones")
        nl = QVBoxLayout(nb)
        self.nyq = QTableWidget(0, 5)
        self.nyq.setHorizontalHeaderLabels(["Channel", "Tile/block", "Max frequency", "Zone", ""])
        self.nyq.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.nyq.verticalHeader().setVisible(False)
        self.nyq.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        nl.addWidget(self.nyq)
        row = QHBoxLayout()
        self.send_all = QPushButton("Send all (set_nyquist)")
        self.send_all.clicked.connect(self._send_all)
        row.addStretch(1)
        row.addWidget(self.send_all)
        nl.addLayout(row)
        note = QLabel("Channel names, SMA labels and the crossbar / acquisition mapping come from FIREQ_GUI/core/boards.json: check them against the bitstream in use.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#777; font-size:11px;")
        nl.addWidget(note)
        lay.addWidget(nb)
        lay.addStretch(1)
        self._hints = []

    def set_experiment(self, exp: Experiment) -> None:
        """Refresh from the experiment.

        :param exp: experiment being edited.
        :type exp: Experiment
        """
        b = exp.board_profile
        # map each channel to the (use, colour) of the tracks routed to it
        usage: dict[str, list[tuple[str, str]]] = {}
        for d in exp.drive_tracks:
            usage.setdefault(f"DAC {d.dac}", []).append(("drive", d.color))
        for r in exp.readout_tracks:
            usage.setdefault(f"DAC {r.dac}", []).append(("readout", r.color))
        for a in exp.acq_tracks:
            usage.setdefault(f"ADC {a.adc}", []).append(("acq", a.color))
        self.map.set_data(b, usage)

        # one row per generator IP: drive tracks and readout tones assigned to it
        rows = []
        for g in range(b.generators):
            drives = [d.title for d in exp.drive_tracks if d.generator == g]
            tones = [f"{t.name} ({r.title})" for r, t in exp.all_tones() if t.generator == g]
            desc = " · ".join(filter(None, [("drive: " + ", ".join(drives)) if drives else "", ("readout: " + ", ".join(tones)) if tones else ""]))
            chans = sorted({d.dac for d in exp.drive_tracks if d.generator == g} | {r.dac for r, t in exp.all_tones() if t.generator == g})
            rows.append((f"axisGeneratorIP_{g}", desc or "unused", ", ".join(chans)))
        # one row per acquisition IP: readout windows and their ADC channels
        for a in range(b.acquisitions):
            wins = [(tr, w) for tr, w in exp.all_windows() if w.acquisition == a]
            desc = ", ".join(f"{exp.tone(w.tone_id)[1].name if exp.tone(w.tone_id)[1] else '?'}" for _, w in wins) or "unused"
            rows.append((f"axisAcquisitionIP_{a}", desc, ", ".join(sorted({tr.adc for tr, _ in wins}))))
        self.ips.setRowCount(len(rows))
        for i, cells in enumerate(rows):
            for j, text in enumerate(cells):
                item = QTableWidgetItem(text)
                # grey out unused IPs
                if text in ("free", "unused"):
                    item.setForeground(QBrush(QColor("#999")))
                self.ips.setItem(i, j, item)

        # rebuild the Nyquist table from the current hints
        self._hints = nyquist_hints(exp)
        self.nyq.setRowCount(len(self._hints))
        for i, h in enumerate(self._hints):
            for j, text in enumerate((f"{h.kind.upper()} {h.name}", f"{h.tile}/{h.block}", f"{h.freq:g} MHz", str(h.zone))):
                self.nyq.setItem(i, j, QTableWidgetItem(text))
            btn = QPushButton("Send")
            # bind h now, otherwise every button would send the last hint
            btn.clicked.connect(lambda _=False, h=h: self.nyquistRequested.emit(h.tile, h.block, h.zone))
            self.nyq.setCellWidget(i, 4, btn)

    def _send_all(self) -> None:
        """Send every suggested ``set_nyquist`` command."""
        for h in self._hints:
            self.nyquistRequested.emit(h.tile, h.block, h.zone)
