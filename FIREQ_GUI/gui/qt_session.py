"""Runs :class:`FireqSession` in a worker thread and exposes Qt signals."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from ..core.session import FireqSession
from .qt import QObject, QThread, pyqtSignal, pyqtSlot


class _Worker(QObject):
    """Lives in the worker thread; all blocking network calls happen here."""

    # signals emitted from the worker thread, delivered to the GUI thread as queued calls
    log = pyqtSignal(str, str)
    serverMessage = pyqtSignal(dict)  # noqa: N815
    started = pyqtSignal(dict)
    package = pyqtSignal(str, object, str, int)
    point = pyqtSignal(object, object, object)
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(str, bool, str)
    connectedChanged = pyqtSignal(bool)  # noqa: N815
    busyChanged = pyqtSignal(bool)  # noqa: N815
    hardwareReceived = pyqtSignal(dict)  # noqa: N815

    def __init__(self) -> None:
        """Create the worker without a session."""
        super().__init__()
        self.session: FireqSession | None = None

    # listener interface ---------------------------------------------------
    def on_log(self, level: str, text: str) -> None:  # noqa: D102
        """Forward a log line (see :class:`~FIREQ_GUI.core.session.SessionListener`).

        :param level: log level.
        :type level: str
        :param text: message.
        :type text: str
        """
        self.log.emit(level, text)

    def on_server_message(self, header: dict) -> None:  # noqa: D102
        """Forward a server message.

        :param header: message header.
        :type header: dict
        """
        # copy the dict so the GUI never shares a mutable object with the worker thread
        self.serverMessage.emit(dict(header))

    def on_experiment_started(self, info: dict) -> None:  # noqa: D102
        """Forward the start of an experiment.

        :param info: run information.
        :type info: dict
        """
        self.started.emit(dict(info))

    def on_package(self, source: str, data: np.ndarray, output_type: str, shots: int) -> None:  # noqa: D102
        """Forward a DMA package.

        :param source: acquisition node.
        :type source: str
        :param data: complex samples.
        :type data: np.ndarray
        :param output_type: output type.
        :type output_type: str
        :param shots: shots in the package.
        :type shots: int
        """
        self.package.emit(source, data, output_type, shots)

    def on_point(self, index: tuple, data: dict, output_types: dict) -> None:  # noqa: D102
        """Forward a completed sweep point.

        :param index: point index.
        :type index: tuple
        :param data: data of each source.
        :type data: dict
        :param output_types: output type of each source.
        :type output_types: dict
        """
        self.point.emit(index, data, output_types)

    def on_progress(self, done: int, total: int) -> None:  # noqa: D102
        """Forward the progress.

        :param done: shots received.
        :type done: int
        :param total: shots expected.
        :type total: int
        """
        self.progress.emit(done, total)

    def on_experiment_finished(self, exp_dir: str, ok: bool, message: str) -> None:  # noqa: D102
        """Forward the end of an experiment.

        :param exp_dir: output folder.
        :type exp_dir: str
        :param ok: True if completed.
        :type ok: bool
        :param message: result.
        :type message: str
        """
        self.finished.emit(exp_dir, ok, message)

    def on_hardware(self, description: dict) -> None:  # noqa: D102
        """Forward a hardware description from the server.

        :param description: hardware description.
        :type description: dict
        """
        self.hardwareReceived.emit(dict(description))

    # commands ---------------------------------------------------------------
    def _guard(self, fn: Callable[..., object], *args: object) -> None:
        """Run a command if connected, reporting errors and busy state.

        :param fn: command to run.
        :type fn: Callable[..., object]
        :param args: its arguments.
        :type args: object
        """
        # commands need an open session
        if self.session is None or not self.session.connected:
            self.log.emit("error", "not connected to the server")
            return
        # busy is reported around every command so the GUI can disable its buttons
        self.busyChanged.emit(True)
        try:
            fn(*args)
        except Exception as e:  # noqa: BLE001
            self.log.emit("error", f"{getattr(fn, '__name__', 'command')}: {e}")
            # a socket error means the connection is gone: drop the session
            if isinstance(e, (ConnectionError, OSError)):
                self._drop()
        finally:
            self.busyChanged.emit(False)

    def _drop(self) -> None:
        """Close the session and report the disconnection."""
        if self.session is not None:
            self.session.close()
        self.session = None
        self.connectedChanged.emit(False)

    @pyqtSlot(str, int, str, str)
    def connect_to(self, host: str, port: int, token: str, output_root: str) -> None:
        """Open a connection.

        :param host: server address.
        :type host: str
        :param port: server port.
        :type port: int
        :param token: authentication token.
        :type token: str
        :param output_root: folder where experiments are saved.
        :type output_root: str
        """
        # close any previous session before opening a new one
        self._drop()
        self.busyChanged.emit(True)
        try:
            # the worker itself is the session listener, so callbacks become signals
            s = FireqSession(host, port, token, listener=self, output_root=output_root)
            s.open()
            self.session = s
            self.connectedChanged.emit(True)
        except Exception as e:  # noqa: BLE001
            self.log.emit("error", f"connection to {host}:{port} failed: {e}")
            self.connectedChanged.emit(False)
        finally:
            self.busyChanged.emit(False)

    @pyqtSlot()
    def disconnect_from(self) -> None:
        """Close the connection."""
        self._drop()
        self.log.emit("info", "disconnected")

    @pyqtSlot()
    def ping(self) -> None:  # noqa: D102
        """Ping the server."""
        self._guard(lambda: self.session.ping())

    @pyqtSlot()
    def reset_all(self) -> None:  # noqa: D102
        """Reset the server state."""
        self._guard(lambda: self.session.reset_all())

    @pyqtSlot()
    def mts_sync(self) -> None:  # noqa: D102
        """Request multi-tile synchronisation."""
        self._guard(lambda: self.session.mts_sync())

    @pyqtSlot()
    def request_hardware(self) -> None:  # noqa: D102
        """Ask the server for its hardware description."""
        self._guard(lambda: self.session.request_hardware())

    @pyqtSlot(int, int, int)
    def set_nyquist(self, tile: int, block: int, zone: int) -> None:  # noqa: D102
        """Set the Nyquist zone of a converter block.

        :param tile: converter tile.
        :type tile: int
        :param block: converter block.
        :type block: int
        :param zone: nyquist zone.
        :type zone: int
        """
        self._guard(lambda: self.session.set_nyquist(tile, block, zone))

    @pyqtSlot(str, str, bool, str)
    def run(self, yaml_path: str, name: str, reset_first: bool, output_root: str) -> None:
        """Run an experiment (blocking inside the worker thread).

        :param yaml_path: experiment YAML file.
        :type yaml_path: str
        :param name: experiment name.
        :type name: str
        :param reset_first: send reset_all before running.
        :type reset_first: bool
        :param output_root: folder where the experiment is saved.
        :type output_root: str
        """

        # run as one guarded command so reset and run share the busy state
        def go() -> None:
            """Reset (optionally) and run the experiment."""
            self.session.output_root = output_root
            if reset_first:
                self.session.reset_all()
            self.session.run_yaml_file(yaml_path, name)

        self._guard(go)


class SessionBridge(QObject):
    """GUI-side handle: request signals go to the worker thread (queued)."""

    # private request signals, connected to the worker slots (queued across threads)
    _connect = pyqtSignal(str, int, str, str)
    _disconnect = pyqtSignal()
    _ping = pyqtSignal()
    _reset = pyqtSignal()
    _mts = pyqtSignal()
    _nyquist = pyqtSignal(int, int, int)
    _hardware = pyqtSignal()
    _run = pyqtSignal(str, str, bool, str)

    def __init__(self, parent: QObject | None = None) -> None:
        """Start the worker thread.

        :param parent: parent widget.
        :type parent: QObject | None
        """
        super().__init__(parent)
        # the worker lives in its own thread; its slots run there, not in the GUI thread
        self._thread = QThread()
        self._thread.setObjectName("fireq-session")
        self.worker = _Worker()
        self.worker.moveToThread(self._thread)
        self._connect.connect(self.worker.connect_to)
        self._disconnect.connect(self.worker.disconnect_from)
        self._ping.connect(self.worker.ping)
        self._reset.connect(self.worker.reset_all)
        self._mts.connect(self.worker.mts_sync)
        self._nyquist.connect(self.worker.set_nyquist)
        self._hardware.connect(self.worker.request_hardware)
        self._run.connect(self.worker.run)
        # cached state, updated from worker signals, readable without crossing threads
        self.connected = False
        self.busy = False
        self.worker.connectedChanged.connect(self._set_connected)
        self.worker.busyChanged.connect(self._set_busy)
        self._thread.start()

    def _set_connected(self, v: bool) -> None:
        """Track the connection state.

        :param v: True when connected.
        :type v: bool
        """
        self.connected = v

    def _set_busy(self, v: bool) -> None:
        """Track the busy state.

        :param v: True while a command runs.
        :type v: bool
        """
        self.busy = v

    # public API ------------------------------------------------------------
    def connect_to(self, host: str, port: int, token: str, output_root: str) -> None:  # noqa: D102
        """Connect to a server (in the worker thread).

        :param host: server address.
        :type host: str
        :param port: server port.
        :type port: int
        :param token: authentication token.
        :type token: str
        :param output_root: folder where experiments are saved.
        :type output_root: str
        """
        self._connect.emit(host, port, token, output_root)

    def disconnect_from(self) -> None:  # noqa: D102
        """Stop any running experiment and disconnect."""
        # stop a running experiment first: the worker thread is blocked until it ends
        self.abort()
        self._disconnect.emit()

    def ping(self) -> None:  # noqa: D102
        """Ping the server."""
        self._ping.emit()

    def reset_all(self) -> None:  # noqa: D102
        """Reset the server state."""
        self._reset.emit()

    def mts_sync(self) -> None:  # noqa: D102
        """Request multi-tile synchronisation."""
        self._mts.emit()

    def set_nyquist(self, tile: int, block: int, zone: int) -> None:  # noqa: D102
        """Set the Nyquist zone of a converter block.

        :param tile: converter tile.
        :type tile: int
        :param block: converter block.
        :type block: int
        :param zone: nyquist zone.
        :type zone: int
        """
        self._nyquist.emit(tile, block, zone)

    def request_hardware(self) -> None:  # noqa: D102
        """Ask the server for its hardware description."""
        self._hardware.emit()

    def run(self, yaml_path: str, name: str, reset_first: bool, output_root: str) -> None:  # noqa: D102
        """Run an experiment (in the worker thread).

        :param yaml_path: experiment YAML file.
        :type yaml_path: str
        :param name: experiment name.
        :type name: str
        :param reset_first: send reset_all before running.
        :type reset_first: bool
        :param output_root: folder where the experiment is saved.
        :type output_root: str
        """
        self._run.emit(yaml_path, name, reset_first, output_root)

    def abort(self) -> None:
        """Stop the running experiment (called from the GUI thread, thread-safe)."""
        # called directly (not via a signal) since the worker is busy in run_yaml_file
        s = self.worker.session
        if s is not None and s.busy:
            s.abort()

    def shutdown(self) -> None:
        """Stop the worker thread."""
        self.abort()
        # close the socket so a blocked call returns, then stop the thread event loop
        s = self.worker.session
        if s is not None:
            s.close()
        self._thread.quit()
        self._thread.wait(3000)
