"""GUI-facing wrapper around :class:`FIREQ_CLIENT.client.Client`.

It reuses the client's network workers, message protocol, decoding and on-disk
output format (``experiment_output/<name>/experiment_<ts>/data_*.pkl`` ...), and adds:

* timeouts and an abort flag, so a GUI thread is never blocked forever;
* a listener with callbacks for every DMA package and every completed sweep point,
  so measured data can be displayed live;
* support for several acquisition sources in the same experiment (frequency
  multiplexed readout): data of every source is collected until the server reports
  the end of the shot block; the first source is saved with the standard file names,
  the others with a ``__<source>`` suffix.

The class is Qt-free; :mod:`FIREQ_GUI.gui.qt_session` runs it in a ``QThread``.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd

from FIREQ_CLIENT.client import CLIENT_NAME, Client
from FIREQ_CLIENT.network import Message
from FIREQ_CLIENT.yaml_preprocessor import load_and_resolve


class SessionListener(Protocol):
    """Callbacks emitted by :class:`FireqSession` (called from the worker thread)."""

    def on_log(self, level: str, text: str) -> None:
        """Receive a log line.

        :param level: ``info``, ``warning`` or ``error``.
        :type level: str
        :param text: message.
        :type text: str
        """
        ...

    def on_server_message(self, header: dict) -> None:
        """Receive a server message that is not data.

        :param header: message header.
        :type header: dict
        """
        ...

    def on_experiment_started(self, info: dict) -> None:
        """Called when an experiment starts.

        :param info: fields of :class:`RunInfo`.
        :type info: dict
        """
        ...

    def on_package(self, source: str, data: np.ndarray, output_type: str, shots: int) -> None:
        """Receive one DMA package.

        :param source: acquisition node that produced it.
        :type source: str
        :param data: complex samples.
        :type data: np.ndarray
        :param output_type: output type of the source.
        :type output_type: str
        :param shots: shots contained in the package.
        :type shots: int
        """
        ...

    def on_point(self, index: tuple[int, ...], data: dict[str, np.ndarray], output_types: dict[str, str]) -> None:
        """Called when a sweep point is complete.

        :param index: index of the point for each variable (``()`` without sweep)
        :type index: tuple[int, ...]
        :param data: per source: all shots (accumulated) or the shot-averaged trace.
        :type data: dict[str, np.ndarray]
        :param output_types: output type of each source.
        :type output_types: dict[str, str]
        """
        ...

    def on_progress(self, done: int, total: int) -> None:
        """Report the progress.

        :param done: shots received.
        :type done: int
        :param total: shots expected.
        :type total: int
        """
        ...

    def on_experiment_finished(self, exp_dir: str, ok: bool, message: str) -> None:
        """Called when the experiment ends.

        :param exp_dir: output folder ('' if none)
        :type exp_dir: str
        :param ok: True if completed.
        :type ok: bool
        :param message: ``completed``, ``stopped`` or the error.
        :type message: str
        """
        ...

    def on_hardware(self, description: dict) -> None:
        """Receive the hardware description sent by the server.

        :param description: fields of a boards.json entry.
        :type description: dict
        """
        ...


class NullListener:
    """Listener that only logs."""

    def __getattr__(self, name: str):  # noqa: ANN204
        """Return a no-op for every callback.

        :param name: callback name.
        :type name: str
        :return: a function that ignores its arguments.
        :rtype: Callable[..., None]
        """
        return lambda *a, **k: None


class Aborted(Exception):
    """Raised when the user stops the experiment."""


@dataclass
class RunInfo:
    """Description of the running experiment, sent to the listener at start.

    :param name: experiment name.
    :type name: str
    :param yaml_path: YAML file.
    :type yaml_path: str
    :param variables: sweep variables.
    :type variables: dict
    :param var_order: variable order used by the server.
    :type var_order: list[str]
    :param total_points: number of sweep points.
    :type total_points: int
    :param shots: shots per point.
    :type shots: int
    :param sources: acquisition nodes.
    :type sources: list[str]
    :param output_types: output type of each source.
    :type output_types: dict[str, str]
    """

    name: str
    yaml_path: str
    variables: dict
    var_order: list[str]
    total_points: int
    shots: int
    sources: list[str]
    output_types: dict[str, str]


class FireqSession(Client):
    """Client subclass driven by a GUI."""

    def __init__(
        self,
        host: str,
        port: int,
        token: str = "fireq",
        listener: SessionListener | None = None,
        output_root: str = "experiment_output",
        timeout: float = 30.0,
    ) -> None:
        """Create a session (not connected yet).

        :param host: server address.
        :type host: str
        :param port: server TCP port.
        :type port: int
        :param token: authentication token.
        :type token: str
        :param listener: receiver of the callbacks (None = ignore them)
        :type listener: SessionListener | None
        :param output_root: folder where experiments are saved.
        :type output_root: str
        :param timeout: seconds to wait for command replies.
        :type timeout: float
        """
        super().__init__(host, port)
        self.token = token
        self.listener: SessionListener = listener or NullListener()
        self.output_root = output_root
        self.timeout = timeout
        self._abort = threading.Event()
        self.connected = False
        self.busy = False
        self.hardware: dict | None = None

    # ------------------------------------------------------------------ connection
    def open(self) -> None:
        """Connect and perform the handshake."""
        self.connect()
        try:
            # the server speaks first: wait for its handshake before authenticating
            hs = self._get(timeout=self.timeout)
            if hs.header.get("type") != "handshake":
                raise ConnectionError(f"expected handshake, got {hs.header}")
            # optional: a server that describes its hardware sends it with the handshake
            hw = hs.header.get("hardware")
            # authenticate; the client name lets the server tell GUI sessions from the CLI
            self.sender.send(Message(header={"type": "handshake_ack", "token": self.token, "client_name": f"{CLIENT_NAME}_gui"}))
        except Exception:
            # do not leave a half-open socket behind if the handshake fails
            self.close()
            raise
        self.connected = True
        self._log("info", f"connected to {self.host}:{self.port}")
        # forward the hardware only once the connection is marked open
        if isinstance(hw, dict):
            self.hardware = hw
            self._log("info", "hardware description received with the handshake")
            self.listener.on_hardware(hw)

    def close(self) -> None:
        """Disconnect."""
        if self.sock is not None:
            # let the sender flush queued commands (e.g. set_nyquist) before closing the socket
            deadline = time.monotonic() + 1.0
            while self.sender is not None and self.sender._queue.unfinished_tasks and time.monotonic() < deadline:
                time.sleep(0.01)
            # disconnect may fail if the reader already saw the socket close
            try:
                self.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self.connected = False
        # forget the socket so a later open() reconnects from scratch
        self.sock = None

    # ------------------------------------------------------------------ simple commands
    def request_hardware(self) -> dict | None:
        """Ask the server for its hardware description (``{"cmd": "get_hardware"}``).

        Expected reply: ``{"type": "hardware", "hardware": {...}}`` with the fields of a
        boards.json entry. Servers that do not implement it answer with an error: None.

        :return: the description, or None if the server does not provide it.
        :rtype: dict | None
        """
        # drop leftovers of a previous run so the reply matched below is ours
        self._drain()
        self.sender.send(Message(header={"cmd": "get_hardware"}))
        # an error reply means this server does not implement the command
        resp = self._reply(lambda h: h.get("type") in ("hardware", "error"))
        if resp.header.get("type") != "hardware":
            self._log("warning", f"the server does not provide a hardware description: {resp.header.get('msg', resp.header)}")
            return None
        hw = resp.header.get("hardware") or {}
        self.hardware = hw
        self.listener.on_hardware(hw)
        return hw

    def ping(self) -> dict:
        """Ping the server and return the response header.

        :return: the response header.
        :rtype: dict
        """
        self._drain()
        self.sender.send(Message(header={"cmd": "ping", "session_id": "gui"}))
        # pong replies carry a 'resp' field instead of 'type'
        resp = self._reply(lambda h: "resp" in h)
        self._log("info", f"ping: {resp.header}")
        return resp.header

    def reset_all(self) -> dict:
        """Reset the server state.

        :return: the response header.
        :rtype: dict
        """
        self._drain()
        self.sender.send(Message(header={"cmd": "reset_all", "session_id": "gui"}))
        resp = self._reply(lambda h: h.get("type") in ("status", "error"))
        self._log("info", f"reset_all: {resp.header}")
        return resp.header

    def mts_sync(self) -> None:
        """Request multi-tile synchronisation."""
        self._mts_sync()
        self._log("info", "mts_sync sent")

    def set_nyquist(self, tile: int, block: int, zone: int) -> None:
        """Set the Nyquist zone of a converter block.

        :param tile: converter tile.
        :type tile: int
        :param block: converter block.
        :type block: int
        :param zone: nyquist zone.
        :type zone: int
        """
        self._set_nyquist(tile, block, zone)
        self._log("info", f"set_nyquist {tile} {block} {zone} sent")

    def trigger_manually(self, generator: str = "/axisGeneratorIP_0") -> None:
        """Send a manual trigger request.

        :param generator: generator node path.
        :type generator: str
        """
        # a header with only the generator path is a manual trigger request
        self.sender.send(Message(header={"generator": generator}))

    def abort(self) -> None:
        """Ask the server to stop and unblock the waiting loops (thread-safe)."""
        # set the flag first so local waits stop even if the server never answers
        self._abort.set()
        if self.sender is not None:
            self.sender.send(Message(header={"cmd": "abort"}))

    # ------------------------------------------------------------------ experiment
    def run_yaml_file(self, yaml_path: str, name: str | None = None) -> str:
        """Run an experiment described by a YAML file; return the output directory.

        :param yaml_path: experiment YAML file.
        :type yaml_path: str
        :param name: experiment name (None = file name)
        :type name: str | None
        :return: the output folder ('' if the run failed before creating it)
        :rtype: str
        """
        config = load_and_resolve(yaml_path)
        name = name or os.path.splitext(os.path.basename(yaml_path))[0]
        sysc = config["sys_config"]
        # every acquisition node in sys_config is a data source
        sources = [k for k in sysc if k.startswith("/axisAcquisitionIP")]
        output_types = {k: sysc[k].get("$output_type", "accumulated") for k in sources}
        self._abort.clear()
        self.busy = True
        exp_dir = ""
        # discard messages of an aborted previous run before starting
        self._drain()
        try:
            # same command for single and swept runs; a sweep adds a header and one block per point
            self.sender.send(Message(header={"cmd": "config_and_run", "system": sysc, "variables": config["variables"]}))
            if config.get("variables"):
                exp_dir = self._run_sweep(config, name, yaml_path, sources, output_types)
            else:
                exp_dir = self._run_single(config, name, yaml_path, sources, output_types)
            self.listener.on_experiment_finished(exp_dir, True, "completed")
        # user stop: reported as not ok, but not as an error
        except Aborted:
            self._log("warning", "experiment stopped")
            self.listener.on_experiment_finished(exp_dir, False, "stopped")
        except Exception as e:  # noqa: BLE001
            self._log("error", f"experiment failed: {e}")
            self.listener.on_experiment_finished(exp_dir, False, str(e))
        finally:
            # always release the busy flag so the GUI can start the next run
            self.busy = False
            self._abort.clear()
        return exp_dir

    def _run_single(self, config: dict, name: str, path: str, sources: list[str], otypes: dict[str, str]) -> str:
        """Run an experiment without sweep variables.

        :param config: resolved configuration.
        :type config: dict
        :param name: experiment name.
        :type name: str
        :param path: YAML file.
        :type path: str
        :param sources: acquisition nodes.
        :type sources: list[str]
        :param otypes: output type of each source.
        :type otypes: dict[str, str]
        :return: the output folder.
        :rtype: str
        """
        shots = int(config["sys_config"]["$shots"])
        info = RunInfo(name, path, {}, [], 1, shots, sources, otypes)
        self.listener.on_experiment_started(info.__dict__)
        # the server sends 'experiment started' before the first DMA package
        self._wait_status()
        data, end = self._collect_block(shots, otypes, done=0, total=shots)
        exp_dir = self._make_experiment_folder(name, config)
        # the first acquisition node is primary and keeps the CLI file names
        primary, frames = self._frames(data, otypes, shots)
        if primary is None:
            raise RuntimeError("no data received")
        self._save_experiment(frames[primary], exp_dir, end)
        for src, df in frames.items():
            if src != primary:
                # extra sources (frequency multiplexing) get a __<source> suffix
                df.to_pickle(os.path.join(exp_dir, f"data__{_clean(src)}.pkl"))
        # a single run is reported as one point with an empty index
        self.listener.on_point((), {s: _summary(a, otypes.get(s), shots) for s, a in data.items()}, otypes)
        return exp_dir

    def _run_sweep(self, config: dict, name: str, path: str, sources: list[str], otypes: dict[str, str]) -> str:
        """Run a swept experiment, saving one file per sweep point.

        :param config: resolved configuration.
        :type config: dict
        :param name: experiment name.
        :type name: str
        :param path: YAML file.
        :type path: str
        :param sources: acquisition nodes.
        :type sources: list[str]
        :param otypes: output type of each source.
        :type otypes: dict[str, str]
        :return: the output folder.
        :rtype: str
        """
        sysc = config["sys_config"]
        shots = int(sysc["$shots"])
        specs = config["variables"]
        # points per variable, used for the total and to wrap the indices
        counts = {v: _count(d) for v, d in specs.items()}
        total_points = int(np.prod(list(counts.values()))) if counts else 1
        # the server announces the order in which it nests the variables
        var_order = self._wait_order()
        if sorted(var_order) != sorted(counts):
            self._log("warning", f"server variable order {var_order} differs from {list(counts)}")
            # trust the server order but keep only variables we know about
            var_order = [v for v in var_order if v in counts] or list(counts)
        info = RunInfo(name, path, specs, var_order, total_points, shots, sources, otypes)
        self.listener.on_experiment_started(info.__dict__)
        exp_dir = self._make_experiment_folder(name, config)
        # save configuration and variable order up front, as the CLI client does
        self._save_dict(config, exp_dir, "configuration")
        self._save_dict({i: n for i, n in enumerate(var_order)}, exp_dir, "var_order")
        # multi-index of the current point, one entry per variable
        idx = [0] * len(var_order)
        for point in range(total_points):
            # each point is preceded by its own 'experiment started' status
            self._wait_status()
            data, end = self._collect_block(shots, otypes, done=point * shots, total=total_points * shots)
            # file names encode the point index, e.g. data_2_0
            fname = "_".join(["data"] + [str(i) for i in idx])
            primary, frames = self._frames(data, otypes, shots)
            if primary is not None:
                self._save_dataframe(frames[primary], exp_dir, fname)
                for src, df in frames.items():
                    if src != primary:
                        self._save_dataframe(df, exp_dir, f"{fname}__{_clean(src)}")
            # keep the end status of each point (it carries the measured time)
            self._save_dict(end.header, exp_dir, "_".join(["exp"] + [str(i) for i in idx]))
            self.listener.on_point(tuple(idx), {s: _summary(a, otypes.get(s), shots) for s, a in data.items()}, otypes)
            # advance the indices (last variable fastest, as in Client._fetch_with_variables)
            for k in reversed(range(len(idx))):
                idx[k] += 1
                if idx[k] < counts[var_order[k]]:
                    break
                idx[k] = 0
        # after the last point the server sends a final 'sweep ended' status
        end = self._wait_status()
        self._save_dict(end.header, exp_dir, "end_message")
        return exp_dir

    # ------------------------------------------------------------------ protocol helpers
    def _get(self, timeout: float | None = None) -> Message:
        """Get the next message, honouring abort and timeout.

        :param timeout: seconds to wait (None = forever)
        :type timeout: float | None
        :return: the message.
        :rtype: Message
        :raises Aborted: if the user stopped the experiment.
        :raises ConnectionError: if the connection was closed.
        :raises TimeoutError: if nothing arrives in time.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        # poll in short slices so abort and a closed connection are noticed quickly
        while True:
            if self._abort.is_set():
                raise Aborted()
            # the reader thread ends when the socket closes; drain its queue before giving up
            if not self.reader._thread.is_alive() and self.reader.message_queue.empty():
                raise ConnectionError("connection closed by the server")
            try:
                return self.reader.get_message(timeout=0.2)
            except queue.Empty:
                if deadline is not None and time.monotonic() > deadline:
                    raise TimeoutError("no answer from the server") from None

    def _reply(self, match: Callable[[dict], bool]) -> Message:
        """Wait for the reply to a command, skipping stale experiment messages.

        :param match: predicate on the header that identifies the reply.
        :type match: Callable[[dict], bool]
        :return: the reply.
        :rtype: Message
        """
        # one overall deadline for the reply, not one per message
        deadline = time.monotonic() + self.timeout
        while True:
            msg = self._get(timeout=max(deadline - time.monotonic(), 0.01))
            if match(msg.header):
                return msg
            # unrelated messages (e.g. late data) still reach the listener
            self.listener.on_server_message(msg.header)

    def _next_typed(self, wanted: tuple[str, ...]) -> Message:
        """Read until a message of the wanted type; warnings are logged, errors raised.

        :param wanted: accepted values of the ``type`` header field.
        :type wanted: tuple[str, ...]
        :return: the message.
        :rtype: Message
        :raises RuntimeError: if the server reports an error.
        """
        while True:
            # no timeout while an experiment runs: a point may take arbitrarily long
            msg = self._get(timeout=None if self.busy else self.timeout)
            typeh = msg.header.get("type")
            if typeh in wanted:
                return msg
            if typeh == "warning":
                self._log("warning", f"server: {msg.header.get('msg')}")
                self.listener.on_server_message(msg.header)
            elif typeh == "error":
                raise RuntimeError(f"server: {msg.header.get('msg')}")
            else:
                self.listener.on_server_message(msg.header)

    def _wait_status(self) -> Message:
        """Wait for the next ``status`` message.

        :return: the status message.
        :rtype: Message
        """
        msg = self._next_typed(("status",))
        self.listener.on_server_message(msg.header)
        return msg

    def _wait_order(self) -> list[str]:
        """Wait for the ``sweep_experiment_header`` message.

        :return: the order of the sweep variables used by the server.
        :rtype: list[str]
        """
        return list(self._next_typed(("sweep_experiment_header",)).header.get("variables_order", []))

    def _collect_block(self, shots: int, otypes: dict[str, str], done: int, total: int) -> tuple[dict[str, np.ndarray], Message]:
        """Collect DMA packages until the 'experiment ended' status.

        :param shots: shots per point.
        :type shots: int
        :param otypes: output type of each source.
        :type otypes: dict[str, str]
        :param done: shots already received (for the progress)
        :type done: int
        :param total: shots expected in the whole experiment.
        :type total: int
        :return: the samples of each source and the end status message.
        :rtype: tuple[dict[str, np.ndarray], Message]
        """
        chunks: dict[str, list[np.ndarray]] = {}
        counted: dict[str, int] = {}
        while True:
            msg = self._next_typed(("dma_package", "status"))
            # a status message closes the shot block of this point
            if msg.header.get("type") == "status":
                self.listener.on_server_message(msg.header)
                data = {s: np.concatenate(c) for s, c in chunks.items()}
                return data, msg
            src = msg.header.get("source", "?")
            n = int(msg.header.get("shots", 0))
            arr = self._decode_package(msg)
            chunks.setdefault(src, []).append(arr)
            # progress follows the most advanced source, capped at the shots of this point
            counted[src] = counted.get(src, 0) + n
            self.listener.on_package(src, arr, otypes.get(src, "accumulated"), n)
            self.listener.on_progress(done + min(max(counted.values()), shots), total)

    def _frames(self, data: dict[str, np.ndarray], otypes: dict[str, str], shots: int) -> tuple[str | None, dict[str, pd.DataFrame]]:
        """Turn the samples of each source into the client DataFrame format.

        :param data: samples of each source.
        :type data: dict[str, np.ndarray]
        :param otypes: output type of each source.
        :type otypes: dict[str, str]
        :param shots: shots per point.
        :type shots: int
        :return: the primary source (first acquisition node) and the DataFrame of each source.
        :rtype: tuple[str | None, dict[str, pd.DataFrame]]
        """
        frames = {}
        for src, arr in data.items():
            try:
                frames[src] = self._make_df_from_shots(arr, otypes.get(src, "accumulated"), shots)
            # a source with an unexpected sample count is skipped, not fatal
            except ValueError as e:
                self._log("warning", f"{src}: {e}")
        # primary = first node in YAML order, then any source the YAML did not list
        ordered = [s for s in otypes if s in frames] + [s for s in frames if s not in otypes]
        return (ordered[0] if ordered else None), frames

    def _make_experiment_folder(self, experiment_name: str, config: dict) -> str:
        """Same layout as the CLI client, rooted at ``output_root``.

        :param experiment_name: experiment name.
        :type experiment_name: str
        :param config: configuration saved as ``config.json``.
        :type config: dict
        :return: the new folder ``<output_root>/<name>/experiment_<timestamp>``.
        :rtype: str
        """
        import json

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        dir_name = os.path.join(self.output_root, experiment_name, f"experiment_{timestamp}")
        # two runs in the same second get a numeric suffix instead of overwriting
        n = 1
        while os.path.exists(dir_name):
            dir_name = os.path.join(self.output_root, experiment_name, f"experiment_{timestamp}_{n}")
            n += 1
        os.makedirs(dir_name)
        with open(os.path.join(dir_name, "config.json"), "w") as f:
            json.dump(config, f, indent=2, default=str)
        return dir_name

    def _drain(self) -> None:
        """Discard queued messages (left over from a stopped experiment), forwarding them to the listener."""
        q = self.reader.message_queue
        while not q.empty():
            try:
                self.listener.on_server_message(q.get_nowait().header)
            except queue.Empty:
                break

    def _log(self, level: str, text: str) -> None:
        """Log a message and forward it to the listener.

        :param level: ``info``, ``warning`` or ``error``.
        :type level: str
        :param text: message.
        :type text: str
        """
        getattr(self.log, level if level != "warning" else "warning", self.log.info)(text)
        self.listener.on_log(level, text)


def _count(spec: dict) -> int:
    """Return the number of points of a sweep variable description.

    :param spec: variable description.
    :type spec: dict
    :return: the number of points.
    :rtype: int
    """
    mode = spec.get("mode", "lin")
    if mode == "list":
        return len(spec.get("values", []))
    if mode == "const":
        return 1
    return int(spec.get("num", 1))


def _clean(src: str) -> str:
    """Turn a node path into a file-name suffix (``/axisAcquisitionIP_1`` -> ``axisAcquisitionIP_1``).

    :param src: node path.
    :type src: str
    :return: the suffix.
    :rtype: str
    """
    return src.strip("/").replace("/", "_")


def _summary(arr: np.ndarray, output_type: str | None, shots: int) -> np.ndarray:
    """Per-point summary for live plots: shot-averaged trace (raw/decimated) or all shots (accumulated).

    :param arr: samples of one source for one point.
    :type arr: np.ndarray
    :param output_type: output type of the source.
    :type output_type: str | None
    :param shots: shots per point.
    :type shots: int
    :return: the shot-averaged trace (raw/decimated) or the samples (accumulated)
    :rtype: np.ndarray
    """
    # raw/decimated: average shots into one trace; accumulated keeps every IQ point
    if output_type in ("raw", "decimated") and shots > 0 and len(arr) % shots == 0:
        return arr.reshape(shots, -1).mean(axis=0)
    return arr


# library module: leave handler configuration to the application
logging.getLogger(__name__).addHandler(logging.NullHandler())
