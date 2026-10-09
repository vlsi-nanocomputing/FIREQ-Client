"""Application entry point."""

from __future__ import annotations

import logging
import sys

from .qt import QApplication, QFont


def main(argv: list[str] | None = None) -> int:
    """Start the FIREQ GUI.

    :param argv: command line (None = ``sys.argv``); an optional YAML file is imported at start.
    :type argv: list[str] | None
    :return: the application exit code.
    :rtype: int
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("FIREQ GUI")
    app.setOrganizationName("vlsi-nanocomputing")
    # fusion renders the same on every platform
    app.setStyle("Fusion")
    font = QFont(app.font())
    font.setPointSize(max(font.pointSize(), 10))
    app.setFont(font)
    app.setStyleSheet(
        """
        QGroupBox { font-weight: 600; border: 1px solid #d5d9e0; border-radius: 6px; margin-top: 10px; padding-top: 6px; }
        QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #2b3a55; }
        QTableWidget { gridline-color: #e3e6eb; }
        QDockWidget::title { padding: 4px; background: #eef1f5; }
        QPushButton { padding: 3px 10px; }
        """
    )
    # imported late so the QApplication exists before any widget module loads
    from .main_window import MainWindow

    win = MainWindow()
    win.show()
    # an optional YAML passed on the command line is imported at start
    if len(app.arguments()) > 1:
        path = app.arguments()[1]
        if path.endswith((".yaml", ".yml")):
            win._import(path)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
