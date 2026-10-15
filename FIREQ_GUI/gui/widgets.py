"""Reusable widgets: parameter editor with %macro/#sweep support, helpers."""

from __future__ import annotations

from collections.abc import Callable

from ..core.expressions import Param, format_param, param_kind, parse_param
from .qt import (
    QApplication,
    QBrush,
    QColor,
    QColorDialog,
    QComboBox,
    QCompleter,
    QFrame,
    QHBoxLayout,
    QLabel,
    QEvent,
    QLineEdit,
    QMessageBox,
    QObject,
    QPushButton,
    QShowEvent,
    QStringListModel,
    Qt,
    QWidget,
    pyqtSignal,
)


class MessageBox(QMessageBox):
    """QMessageBox that does not fix its own size.

    QMessageBox makes itself fixed-size. On Wayland with client-side decorations
    (GNOME) the compositor then takes the decoration margins out of that size, so the
    contents get about 20 px less than they need and text and buttons are clipped.
    Keeping the size QMessageBox computed as minimum, but not as maximum, avoids it.
    Other platforms keep the stock fixed-size box. The static helpers mirror the
    QMessageBox ones.
    """

    def _unfix(self) -> None:
        """Turn the fixed size set by QMessageBox into a minimum size (Wayland only)."""
        if QApplication.platformName().startswith("wayland") and self.minimumSize() == self.maximumSize():
            self.setMaximumSize(16777215, 16777215)  # QWIDGETSIZE_MAX

    def showEvent(self, ev: QShowEvent) -> None:  # noqa: N802
        """Unfix the size before the window is mapped.

        :param ev: show event.
        :type ev: QShowEvent
        """
        super().showEvent(ev)
        self._unfix()

    def event(self, ev: QEvent) -> bool:
        """Unfix the size again after QMessageBox recomputes it (font, style or text changes).

        :param ev: widget event.
        :type ev: QEvent
        :return: True if the event was handled.
        :rtype: bool
        """
        handled = super().event(ev)
        self._unfix()
        return handled

    @classmethod
    def _ask(cls, icon: QMessageBox.Icon, parent: QWidget | None, title: str, text: str, buttons: QMessageBox.StandardButton) -> QMessageBox.StandardButton:
        """Show a modal message box and return the button pressed.

        :param icon: icon of the box.
        :type icon: QMessageBox.Icon
        :param parent: parent widget.
        :type parent: QWidget | None
        :param title: window title.
        :type title: str
        :param text: message.
        :type text: str
        :param buttons: standard buttons to show.
        :type buttons: QMessageBox.StandardButton
        :return: the button pressed.
        :rtype: QMessageBox.StandardButton
        """
        box = cls(icon, title, text, buttons, parent)
        return QMessageBox.StandardButton(box.exec())

    @classmethod
    def question(cls, parent: QWidget | None, title: str, text: str, buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) -> QMessageBox.StandardButton:
        """Ask a question, like :meth:`QMessageBox.question`."""
        return cls._ask(QMessageBox.Icon.Question, parent, title, text, buttons)

    @classmethod
    def warning(cls, parent: QWidget | None, title: str, text: str, buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok) -> QMessageBox.StandardButton:
        """Show a warning, like :meth:`QMessageBox.warning`."""
        return cls._ask(QMessageBox.Icon.Warning, parent, title, text, buttons)

    @classmethod
    def critical(cls, parent: QWidget | None, title: str, text: str, buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok) -> QMessageBox.StandardButton:
        """Show an error, like :meth:`QMessageBox.critical`."""
        return cls._ask(QMessageBox.Icon.Critical, parent, title, text, buttons)

    @classmethod
    def information(cls, parent: QWidget | None, title: str, text: str, buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok) -> QMessageBox.StandardButton:
        """Show a message, like :meth:`QMessageBox.information`."""
        return cls._ask(QMessageBox.Icon.Information, parent, title, text, buttons)


class NameRegistry(QObject):
    """Shared list of ``%macro`` and ``#variable`` names for auto-completion."""

    # lazily created singleton shared by every ParamEdit
    _instance: NameRegistry | None = None

    def __init__(self) -> None:
        """Create the completion model."""
        super().__init__()
        # string list model shared by all the completers
        self.model = QStringListModel()

    @classmethod
    def get(cls) -> NameRegistry:
        """Return the singleton.

        :return: the shared registry.
        :rtype: NameRegistry
        """
        if cls._instance is None:
            cls._instance = NameRegistry()
        return cls._instance

    def set_names(self, macros: list[str], variables: list[str]) -> None:
        """Update the completion entries.

        :param macros: names of the preprocess macros.
        :type macros: list[str]
        :param variables: names of the sweep variables.
        :type variables: list[str]
        """
        # macros are completed with a % prefix, sweep variables with #
        self.model.setStringList([f"%{m}" for m in macros] + [f"#{v}" for v in variables])


class ParamEdit(QLineEdit):
    """Line edit for a FIREQ parameter: number, ``%macro`` or ``#expression``.

    The evaluated value at the current sweep preview point is shown in the tooltip.
    """

    # emitted with the parsed value when the user commits a change
    edited = pyqtSignal(object)
    # set by the main window to evaluate expressions at the sweep preview point
    evaluator: Callable[[Param], float | None] | None = None

    def __init__(self, value: Param | None = 0, unit: str = "", allow_none: bool = False, placeholder: str = "", parent: QWidget | None = None) -> None:
        """Create the editor.

        :param value: initial value (None = empty)
        :type value: Param | None
        :param unit: unit shown in the tooltip.
        :type unit: str
        :param allow_none: accept an empty field (value None)
        :type allow_none: bool
        :param placeholder: text shown when the field is empty.
        :type placeholder: str
        :param parent: parent widget.
        :type parent: QWidget | None
        """
        super().__init__(parent)
        self.unit = unit
        self.allow_none = allow_none
        self._value: Param | None = None
        if placeholder:
            self.setPlaceholderText(placeholder)
        # complete %macro and #variable names anywhere in the typed text
        comp = QCompleter(NameRegistry.get().model, self)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        comp.setFilterMode(Qt.MatchFlag.MatchContains)
        self.setCompleter(comp)
        # parse only when the user leaves the field or presses Enter
        self.editingFinished.connect(self._commit)
        self.setMinimumWidth(90)
        self.setValue(value)

    def value(self) -> Param | None:
        """Return the current value.

        :return: the value: a number, a ``%macro``/``#expression`` string or None.
        :rtype: Param | None
        """
        return self._value

    def setValue(self, value: Param | None) -> None:  # noqa: N802 - Qt naming
        """Set the value without emitting.

        :param value: new value (None = empty)
        :type value: Param | None
        """
        self._value = value
        self.setText("" if value is None else format_param(value))
        self.restyle()

    def _commit(self) -> None:
        """Parse the text when editing finishes and emit :attr:`edited` if the value changed."""
        text = self.text().strip()
        # empty text means None only if allowed, otherwise it is parsed as usual
        new: Param | None = None if (self.allow_none and text == "") else parse_param(text)
        # the type check catches e.g. 1 vs 1.0, which compare equal
        if new != self._value or type(new) is not type(self._value):
            self._value = new
            # rewrite the text in canonical form after parsing
            if new is not None and self.text() != format_param(new):
                self.setText(format_param(new))
            self.restyle()
            self.edited.emit(new)

    def restyle(self) -> None:
        """Update colours and tooltip from the value kind."""
        v = self._value
        kind = "none" if v is None else param_kind(v)
        # tooltip text for each value kind
        tip = {
            "number": "number",
            "macro": "preprocess macro (resolved by the client)",
            "sweep": "sweep expression (evaluated by the server at every point)",
            "none": "empty: default value",
        }[kind]
        # for expressions, append the value evaluated at the preview point
        if v is not None and ParamEdit.evaluator is not None and kind != "number":
            ev = ParamEdit.evaluator(v)
            tip += "\n= " + ("cannot be evaluated" if ev is None else f"{ev:g} {self.unit}") + " (sweep preview point)"
        tip += "\nType a number, %macro or #expression (e.g. #tau + 200)"
        self.setToolTip(tip)
        # tint macro and sweep fields so they stand out from plain numbers
        colors = {"macro": "#f3ecff", "sweep": "#e8f1ff"}
        self.setStyleSheet(f"QLineEdit {{ background: {colors[kind]}; }}" if kind in colors else "")


def color_button(color: str, on_change: Callable[[str], None]) -> QPushButton:
    """Return a button that shows and picks a colour.

    :param color: initial colour (``#rrggbb``)
    :type color: str
    :param on_change: called with the new colour.
    :type on_change: Callable[[str], None]
    :return: the button (``repaint_color(c)`` updates it)
    :rtype: QPushButton
    """
    btn = QPushButton()
    btn.setFixedWidth(44)

    # paint the swatch and remember the colour on the button itself
    def paint(c: str) -> None:
        """Show a colour on the button.

        :param c: colour (``#rrggbb``)
        :type c: str
        """
        btn.setStyleSheet(f"QPushButton {{ background: {c}; border: 1px solid #888; border-radius: 3px; min-height: 18px; }}")
        btn.setProperty("color", c)

    def pick() -> None:
        """Open the colour dialog and report the chosen colour."""
        c = QColorDialog.getColor(QColor(btn.property("color")), btn, "Track color")
        if c.isValid():
            paint(c.name())
            on_change(c.name())

    paint(color)
    btn.clicked.connect(pick)
    btn.setToolTip("Track color on the timeline")
    # expose the painter so callers can update the swatch without signals
    btn.repaint_color = paint  # type: ignore[attr-defined]
    return btn


def hline() -> QFrame:
    """Return a horizontal separator.

    :return: a sunken horizontal line.
    :rtype: QFrame
    """
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFrameShadow(QFrame.Shadow.Sunken)
    return f


def with_unit(widget: QWidget, unit: str) -> QWidget:
    """Return the widget followed by a unit label.

    :param widget: editor widget.
    :type widget: QWidget
    :param unit: unit text.
    :type unit: str
    :return: a widget containing the editor and the unit label.
    :rtype: QWidget
    """
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(widget, 1)
    lab = QLabel(unit)
    lab.setStyleSheet("color: #666;")
    lab.setMinimumWidth(28)
    lay.addWidget(lab)
    return w


def fill_combo(combo: QComboBox, items: list[tuple[str, object, bool]], current: object) -> None:
    """Fill a combo with (text, data, enabled) entries and select current.

    :param combo: combo box to fill.
    :type combo: QComboBox
    :param items: (text, data, enabled) entries; disabled entries are greyed out.
    :type items: list[tuple[str, object, bool]]
    :param current: data of the entry to select (added as 'not available' if missing)
    :type current: object
    """
    # block signals so refilling does not trigger currentIndexChanged
    combo.blockSignals(True)
    combo.clear()
    for i, (text, data, enabled) in enumerate(items):
        combo.addItem(text, data)
        # grey out entries that cannot be used (e.g. channels not routed)
        if not enabled:
            item = combo.model().item(i)
            if item is not None:
                item.setForeground(QBrush(QColor("#999999")))
    idx = combo.findData(current)
    # keep an unknown current value selectable instead of silently changing it
    if idx < 0 and current is not None:
        combo.addItem(f"{current} (not available)", current)
        idx = combo.count() - 1
    combo.setCurrentIndex(max(idx, 0))
    combo.blockSignals(False)
