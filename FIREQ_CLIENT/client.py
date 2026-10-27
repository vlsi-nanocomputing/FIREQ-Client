"""Client library for the FIREQ server.

:class:`FireqClient` connects to the server over TCP, sends commands, runs experiments and stores the results
on disk. Every method blocks until the complete reply, returns a value or raises a :class:`FireqError`; the
library prints nothing and only logs. The interactive prompt is built on top of it in :mod:`FIREQ_CLIENT.cli`.
"""

from __future__ import annotations

import itertools
import logging
import os
import socket
import threading
import time
import warnings
from collections.abc import Callable, Iterator, Mapping
from queue import Empty
from typing import Any

import numpy as np
import pandas as pd

from .config import ExperimentConfig, load_config
from .errors import (
    RESET_HINT,
    ConfigurationError,
    ConnectionFailed,
    ConnectionLost,
    Desynchronized,
    FireqTimeout,
    ProtocolError,
    RunRejected,
    ServerError,
)
from .export import export
from .network import Message, ReceiveWorker, SendWorker
from .results import ExperimentResult, PointResult, ProgressEvent
from .storage import ExperimentWriter

DEFAULT_AUTH_TOKEN = "fireq"
#: name the client gives itself in the handshake; the server accepts any string
DEFAULT_CLIENT_NAME = f"fireq_client@{socket.gethostname()}"
DEFAULT_OUTPUT_DIR = "experiment_output"
#: socket timeout once connected; the workers retry on timeouts, so it only bounds how
#: long a blocked send/recv takes to notice a stop request
SOCKET_TIMEOUT = 5.0
#: how often a blocked wait checks whether the connection dropped, in seconds
POLL_INTERVAL = 1.0

ConfigLike = str | os.PathLike | Mapping[str, Any] | ExperimentConfig


class RunIterator(Iterator[PointResult]):
    """Iterator over the points of a running experiment; ``result`` is set once the run ends.

    Use it in a ``with`` block (or call :meth:`close`): leaving the loop before the end then closes the
    connection, which is the only way to stop a sweep with the v0 protocol; call
    :meth:`FireqClient.reconnect` to go on.
    """

    def __init__(self) -> None:
        """Initialize an iterator without generator, set by :meth:`FireqClient.run_iter`."""
        self._gen: Iterator[PointResult] | None = None
        #: the result of the run, partial if the iteration was interrupted
        self.result: ExperimentResult | None = None

    def __iter__(self) -> RunIterator:
        """Return the iterator itself."""
        return self

    def __next__(self) -> PointResult:
        """Return the next completed point."""
        return next(self._gen)

    def close(self) -> None:
        """Stop the iteration, closing the connection if the run is still going."""
        self._gen.close()

    def __enter__(self) -> RunIterator:
        """Return the iterator, closed on exit."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the iterator."""
        self.close()


class FireqClient:
    """Scriptable client for the FIREQ server.

    Use it as a context manager to connect and close::

        with FireqClient("rfsoc.lab", 5000, output_dir="data") as fq:
            result = fq.run("Rabi.yaml", preprocess={"gain_r": 0.25})
    """

    def __init__(
        self,
        host: str,
        port: int = 5000,
        *,
        token: str = DEFAULT_AUTH_TOKEN,
        client_name: str = DEFAULT_CLIENT_NAME,
        output_dir: str | os.PathLike = DEFAULT_OUTPUT_DIR,
        connect_timeout: float = 5.0,
        command_timeout: float | None = 30.0,
        autoconnect: bool = False,
    ) -> None:
        """Initialize the client with the server address.

        :param host: server hostname or IP address.
        :type host: str
        :param port: server TCP port.
        :type port: int
        :param token: token sent to the server in the handshake.
        :type token: str
        :param client_name: name sent to the server in the handshake.
        :type client_name: str
        :param output_dir: directory where the experiment data is stored.
        :type output_dir: str | os.PathLike
        :param connect_timeout: how long to wait for the TCP connection and for the handshake, in seconds.
        :type connect_timeout: float
        :param command_timeout: how long to wait for the reply of a command and for the experiment header,
            in seconds; None waits forever.
        :type command_timeout: float | None
        :param autoconnect: connect right away.
        :type autoconnect: bool
        """
        self.host = host
        self.port = port
        self.auth_token = token
        self.client_name = client_name
        self.output_dir = output_dir
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self.log = logging.getLogger(__name__)
        self.sock: socket.socket | None = None
        self.reader: ReceiveWorker | None = None
        self.sender: SendWorker | None = None
        self._desynchronized = False
        # one command at a time: there is a single server session
        self._lock = threading.RLock()
        if autoconnect:
            self.connect()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def connect(self, verify: bool = True) -> None:
        """Open the connection and perform the handshake. Does nothing when already connected.

        :param verify: send a ping after the handshake. The v0 server never confirms the handshake and
            refuses a wrong token by closing the connection, so this is the only way to report it here.
        :type verify: bool
        :raises ConnectionFailed: if the connection, the handshake or the verification fails.
        """
        with self._lock:
            if self.connected:
                return
            self._disconnect()
            try:
                self._connect(self.connect_timeout)
            except OSError as err:
                raise ConnectionFailed(f"Cannot connect to {self.host}:{self.port}: {err}") from err
            try:
                self._do_handshake()
                if verify:
                    self.ping()
            except FireqTimeout:
                self._disconnect()
                raise ConnectionFailed(
                    f"No handshake within {self.connect_timeout} s, another client may be connected"
                ) from None
            except ConnectionLost as err:
                self._disconnect()
                # the v0 server refuses a wrong token by closing the connection after the handshake
                lost = ConnectionLost("The server closed the connection", likely_auth_failure=verify)
                raise ConnectionFailed(str(lost)) from err
            except BaseException:
                self._disconnect()
                raise
            self.log.info(f"Connected to {self.host}:{self.port}")

    def close(self) -> None:
        """Close the connection. Safe to call several times."""
        self._disconnect()
        self.reader = self.sender = self.sock = None

    def reconnect(self) -> None:
        """Close and open the connection again, e.g. after :class:`ConnectionLost` or :class:`Desynchronized`."""
        with self._lock:
            self.close()
            self.connect()

    @property
    def connected(self) -> bool:
        """Tell whether the client holds a working connection.

        :return: True when connected.
        :rtype: bool
        """
        return self.reader is not None and self.sender is not None and not self._connection_lost()

    def __enter__(self) -> FireqClient:
        """Connect, if not connected yet.

        :return: the client.
        :rtype: FireqClient
        """
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the connection."""
        self.close()

    def start_interactive(self, *, close_on_exit: bool | None = None) -> None:
        """Run the interactive prompt on this client. Returns on ``quit``, ``exit`` or Ctrl-D.

        :param close_on_exit: close the connection when the prompt ends; None closes it only if the
            prompt opened it, so a script gets its connected client back.
        :type close_on_exit: bool | None
        """
        from .cli.shell import InteractiveShell  # noqa: PLC0415 - prompt_toolkit and tqdm are optional

        InteractiveShell(self).run(close_on_exit=close_on_exit)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    def ping(self) -> float:
        """Check that the session works.

        :return: the round-trip time, in seconds.
        :rtype: float
        """
        with self._lock:
            start = time.perf_counter()
            self._command({"cmd": "ping"})
            return time.perf_counter() - start

    def reset_all(self) -> None:
        """Reset the state of the server."""
        with self._lock:
            self._command({"cmd": "reset_all"})

    def mts_sync(self) -> None:
        """Synchronize the DAC tiles (multi-tile sync)."""
        with self._lock:
            self._command({"cmd": "mts_sync"})

    def set_nyquist(self, tile: int, block: int, zone: int) -> None:
        """Set the Nyquist zone of a converter block.

        :param tile: tile index.
        :type tile: int
        :param block: block index.
        :type block: int
        :param zone: zone index.
        :type zone: int
        """
        with self._lock:
            self._command({"cmd": "set_nyquist", "tile": tile, "block_id": block, "zone": zone})

    def trigger_manually(self, ip_name: str) -> None:
        """Trigger an IP manually, if it supports it.

        :param ip_name: name of the IP to trigger.
        :type ip_name: str
        """
        with self._lock:
            self._command({"cmd": "trigger_manually", "ip_name": ip_name})

    def apply_configuration(
        self,
        config: ConfigLike,
        *,
        preprocess: Mapping[str, Any] | None = None,
        variables: Mapping[str, Any] | None = None,
    ) -> list[str]:
        """Apply a configuration without running it.

        :param config: a YAML path, a dict shaped like the YAML or an :class:`ExperimentConfig`.
        :type config: ConfigLike
        :param preprocess: values merged over the ``preprocess`` block.
        :type preprocess: Mapping[str, Any] | None
        :param variables: sweep variables replacing or extending the ``variables`` block.
        :type variables: Mapping[str, Any] | None
        :raises ConfigurationError: if the configuration is invalid or the server refuses it.
        :return: the warnings of the server, e.g. that sweep expressions were not applied.
        :rtype: list[str]
        """
        cfg = load_config(config, preprocess=preprocess, variables=variables)
        with self._lock:
            self._check_usable()
            self._send(
                {"cmd": "apply_configuration", "system": cfg.sys_config, "variables": cfg.variables}
            )
            server_warnings: list[str] = []
            try:
                resp = self._next_reply(self.command_timeout, server_warnings)
            except ServerError as err:
                raise ConfigurationError(f"{err}. {RESET_HINT}") from None
            if not self._is_status(resp, "ok"):
                raise ProtocolError(f"Unexpected reply to apply_configuration: {resp.header}")
            # sweep expressions are not applied by apply_configuration, and the server says so in a
            # warning that follows the acknowledgement
            if self._has_sweep_expressions(cfg.sys_config):
                warning = self._wait_for_message(self.command_timeout)
                if warning.header.get("type") == "warning":
                    self.log.warning(f"Server warning: {warning.header.get('msg')}")
                    server_warnings.append(warning.header.get("msg"))
                else:
                    self.log.warning(f"Expected a warning about the sweep expressions, got: {warning.header}")
            return server_warnings

    def run(
        self,
        config: ConfigLike,
        *,
        name: str | None = None,
        preprocess: Mapping[str, Any] | None = None,
        variables: Mapping[str, Any] | None = None,
        save: bool = True,
        keep_in_memory: bool = False,
        reset_before: bool = False,
        progress: Callable[[ProgressEvent], None] | None = None,
        timeout: float | None = None,
    ) -> ExperimentResult:
        """Run an experiment and wait for its end.

        A run that started returns its result, also when the server aborted it (see
        :meth:`ExperimentResult.raise_for_status`). On Ctrl-C the connection is closed, which stops the
        sweep, and the ``KeyboardInterrupt`` carries the partial result as ``err.result``.

        :param config: a YAML path, a dict shaped like the YAML or an :class:`ExperimentConfig`.
        :type config: ConfigLike
        :param name: experiment name, used for the output directory; defaults to the file name.
        :type name: str | None
        :param preprocess: values merged over the ``preprocess`` block.
        :type preprocess: Mapping[str, Any] | None
        :param variables: sweep variables replacing or extending the ``variables`` block.
        :type variables: Mapping[str, Any] | None
        :param save: write the data in the output directory.
        :type save: bool
        :param keep_in_memory: keep the data in the result, so ``dataframe()`` does not read the disk.
        :type keep_in_memory: bool
        :param reset_before: send ``reset_all`` first; the v0 server needs it to run a configuration again.
        :type reset_before: bool
        :param progress: called once per completed point.
        :type progress: Callable[[ProgressEvent], None] | None
        :param timeout: longest gap between two packets of the run, in seconds; None waits forever.
        :type timeout: float | None
        :raises ConfigurationError: if the configuration is invalid.
        :raises RunRejected: if the server refuses the run before it starts.
        :raises ConnectionLost: if the connection drops.
        :return: the result of the run.
        :rtype: ExperimentResult
        """
        iterator = self.run_iter(
            config,
            name=name,
            preprocess=preprocess,
            variables=variables,
            save=save,
            keep_in_memory=keep_in_memory,
            reset_before=reset_before,
            progress=progress,
            timeout=timeout,
        )
        for _ in iterator:
            pass
        return iterator.result

    def run_iter(
        self,
        config: ConfigLike,
        *,
        name: str | None = None,
        preprocess: Mapping[str, Any] | None = None,
        variables: Mapping[str, Any] | None = None,
        save: bool = True,
        keep_in_memory: bool = False,
        reset_before: bool = False,
        progress: Callable[[ProgressEvent], None] | None = None,
        timeout: float | None = None,
    ) -> RunIterator:
        """Run an experiment, yielding each point as soon as it completes.

        Takes the same arguments as :meth:`run`. The configuration is checked right away; the run starts
        with the first ``next()``. The result is in the iterator's ``result`` once the loop ends.

        :return: the iterator over the completed points.
        :rtype: RunIterator
        """
        cfg = load_config(config, preprocess=preprocess, variables=variables)
        iterator = RunIterator()
        iterator._gen = self._run_points(
            cfg, name or cfg.name or "experiment", save, keep_in_memory, reset_before, progress, timeout, iterator
        )
        return iterator

    def export(self, from_dir: str | os.PathLike, to_dir: str | os.PathLike) -> None:
        """Export an experiment (or a tree of experiments) to another directory.

        :param from_dir: source directory.
        :type from_dir: str | os.PathLike
        :param to_dir: destination directory.
        :type to_dir: str | os.PathLike
        """
        export(from_dir, to_dir)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------
    def _run_points(
        self,
        cfg: ExperimentConfig,
        name: str,
        save: bool,
        keep_in_memory: bool,
        reset_before: bool,
        progress: Callable[[ProgressEvent], None] | None,
        timeout: float | None,
        holder: RunIterator,
    ) -> Iterator[PointResult]:
        """Run an experiment and yield its points; the result is stored in ``holder``.

        :param cfg: resolved configuration.
        :type cfg: ExperimentConfig
        :param name: experiment name.
        :type name: str
        :param save: write the data on disk.
        :type save: bool
        :param keep_in_memory: keep the data in the result.
        :type keep_in_memory: bool
        :param reset_before: send reset_all first.
        :type reset_before: bool
        :param progress: progress callback.
        :type progress: Callable[[ProgressEvent], None] | None
        :param timeout: longest gap between two packets of the run.
        :type timeout: float | None
        :param holder: the iterator that receives the result.
        :type holder: RunIterator
        :return: the completed points.
        :rtype: Iterator[PointResult]
        """
        with self._lock:
            if reset_before:
                self.reset_all()
            self._check_usable()
            self._send({"cmd": "config_and_run", "system": cfg.sys_config, "variables": cfg.variables})
            header = self._wait_for_experiment_header()

            # the var order is from outer to inner; a single run has neither
            var_order = header.get("variable_order", ["none"])
            var_values = header.get("variable_values", {"none": [0]})
            # only the variables in var_order are swept, variable_values can also hold the unused ones
            total = 1
            for var in var_order:
                total *= len(var_values[var])
            points = itertools.product(*[range(len(var_values[v])) for v in var_order])

            run: dict[str, Any] = {
                "name": name,
                "directory": None,
                "variable_order": var_order,
                "variable_values": var_values,
                "points_expected": total,
                "points_completed": 0,
                "aborted": True,  # until the footer says otherwise
                "server_errors": [],
                "warnings": [],
                "runtimes_ns": [],
                "run_time_ns": None,
                "sources": [],
            }
            frames: dict[str, list[pd.DataFrame]] | None = {} if keep_in_memory else None

            def finish() -> ExperimentResult:
                """Store the result in the holder and return it."""
                holder.result = ExperimentResult(**run, _frames=frames)
                return holder.result

            try:
                writer = None
                if save:
                    writer = ExperimentWriter(self.output_dir, name)
                    run["directory"] = writer.directory
                    summary = {
                        "experiment_name": name,
                        "full_experiment_header": header,
                        "var_order": var_order,
                        "var_values": var_values,
                        "expected_iterations": total,
                        "config": cfg.as_dict(),
                    }
                    writer.save_dict(summary, "experiment_summary")

                fetched_shots = 0
                iteration_shots = 0
                packages: dict[str, list[np.ndarray]] = {}
                indices = next(points, ())
                while True:
                    message = self._wait_for_message(timeout)
                    kind = message.header.get("type")
                    if kind == "warning":
                        self.log.warning(f"Server warning: {message.header.get('msg')}")
                        run["warnings"].append(message.header.get("msg"))

                    elif kind == "error":
                        # the run failed: the server follows with the footer, flagged as aborted
                        self.log.error(f"Server error during the run: {message.header.get('msg')}")
                        run["server_errors"].append(message.header.get("msg"))

                    elif kind == "dma_package":
                        packages.setdefault(message.header.get("source"), []).append(self._decode_package(message))
                        fetched_shots += message.header.get("shots")
                        iteration_shots += message.header.get("shots")

                    elif self._is_status(message, "iteration_ended"):
                        data = {}
                        if iteration_shots > 0:
                            for source, package_list in packages.items():
                                if not package_list:
                                    continue
                                df = self._make_df_from_shots(
                                    np.concatenate(package_list), iteration_shots // len(packages)
                                )
                                source_dir = source.replace("/", "")
                                data[source_dir] = df
                                if source_dir not in run["sources"]:
                                    run["sources"].append(source_dir)
                                if writer is not None:
                                    writer.save_point(source_dir, indices, df)
                                if frames is not None:
                                    frames.setdefault(source_dir, []).append(df)
                                package_list.clear()
                        iteration_time = message.header.get("time")
                        run["runtimes_ns"].append(iteration_time)
                        point_index = run["points_completed"]
                        run["points_completed"] += 1
                        iteration_shots = 0
                        coordinates = {var: var_values[var][i] for var, i in zip(var_order, indices, strict=True)}
                        if progress is not None:
                            progress(ProgressEvent(point_index, total, coordinates, iteration_time))
                        yield PointResult(point_index, indices, coordinates, data)
                        indices = next(points, indices)

                    elif self._is_status(message, "experiment_footer"):
                        run["run_time_ns"] = message.header.get("run_time")
                        run["aborted"] = bool(message.header.get("aborted", False))
                        self.log.info(
                            f"Experiment ended, fetched {run['points_completed']}/{total} iterations, "
                            f"{fetched_shots} total shots"
                        )
                        break
                    else:
                        raise ProtocolError(f"Unexpected message from the server while running: {message.header}")
            except (KeyboardInterrupt, GeneratorExit) as err:
                # v0 has no cancel: closing the connection makes the server stop before the next point
                self.log.warning("Run interrupted, closing the connection (reconnect() to go on)")
                self.close()
                if isinstance(err, KeyboardInterrupt):
                    err.result = finish()
                else:
                    finish()
                raise
            except (ConnectionLost, FireqTimeout):
                finish()
                raise
            except Exception:
                # the server keeps streaming the rest of the experiment: read it out, so that the next
                # command does not take it as its own reply
                self._skip_to_footer(timeout)
                raise

            if writer is not None:
                writer.save_dict(
                    {"runtimes": run["runtimes_ns"], "run_time": run["run_time_ns"], "aborted": run["aborted"]},
                    "runtimes",
                )
            if run["aborted"]:
                self.log.warning(f"The server aborted the run, the data in {run['directory']} is partial")
            return finish()

    def _wait_for_experiment_header(self) -> dict:
        """Read the acknowledgement of config_and_run and the experiment header.

        The server acknowledges the configuration with "status ok" and then sends the experiment header;
        an error before the header means the run was rejected (no header, no footer follow).

        :raises RunRejected: if the server answers with an error.
        :raises ProtocolError: on an unexpected reply.
        :return: the header.
        :rtype: dict
        """
        acknowledged = False
        while True:
            try:
                message = self._next_reply(self.command_timeout)
            except ServerError as err:
                raise RunRejected(f"Run rejected: {err}. {RESET_HINT}") from None
            if not acknowledged and self._is_status(message, "ok"):
                acknowledged = True
            elif self._is_status(message, "experiment_header"):
                return message.header
            else:
                raise ProtocolError(f"Expected the experiment header from the server, got: {message.header}")

    def _skip_to_footer(self, timeout: float | None) -> None:
        """Discard the messages of the running experiment up to and including its footer.

        The server always sends the footer after the experiment header, also when the run fails.

        :param timeout: longest gap between two packets.
        :type timeout: float | None
        """
        self.log.warning("Discarding the rest of the experiment until the server ends it")
        while not self._is_status(self._wait_for_message(timeout), "experiment_footer"):
            pass

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    def _connect(self, timeout: float = 1.0) -> None:
        """Open the TCP connection and start the network workers.

        :param timeout: timeout of the connection attempt, in seconds.
        :type timeout: float
        """
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)
        # the connection timeout would otherwise stay on the socket and interrupt large sends
        self.sock.settimeout(SOCKET_TIMEOUT)
        self.reader = ReceiveWorker(self.sock, logger=self.log.getChild("reader"))
        self.sender = SendWorker(self.sock, logger=self.log.getChild("sender"))
        self.reader.start()
        self.sender.start()
        self._desynchronized = False

    def _disconnect(self) -> None:
        """Shut down the reader, the sender and close the socket."""
        workers = [worker for worker in (self.reader, self.sender) if worker]
        # flag the workers first, so the shutdown below is not reported as a lost connection
        for worker in workers:
            worker.stop(timeout=0)
        if self.sock is not None:
            # wake up a reader blocked in recv() now, instead of after the socket timeout
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for worker in workers:
            worker.stop()
        # close the socket
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass

    def _do_handshake(self) -> None:
        """Perform the protocol handshake with the server.

        :raises ProtocolError: if the first message is not the handshake.
        """
        handshake = self._wait_for_message(self.connect_timeout)
        if handshake.header.get("type") != "handshake":
            raise ProtocolError(f"Expected handshake, got {handshake.header}")
        ack = {"type": "handshake_ack", "token": self.auth_token, "client_name": self.client_name}
        self.sender.send(Message(header=ack))
        self.log.info("Handshake completed.")

    def _connection_lost(self) -> bool:
        """Tell whether one of the network workers ended because of a connection error.

        :return: True if the connection is lost.
        :rtype: bool
        """
        return bool(
            (self.reader is not None and self.reader.failed.is_set())
            or (self.sender is not None and self.sender.failed.is_set())
        )

    def _check_usable(self) -> None:
        """Raise if no command can be sent.

        :raises ConnectionLost: if the client is not connected.
        :raises Desynchronized: if a previous command timed out.
        """
        if self.reader is None or self.sender is None:
            raise ConnectionLost("Not connected, call connect()")
        if self._connection_lost():
            raise ConnectionLost()
        if self._desynchronized:
            raise Desynchronized("A previous command timed out, call reconnect()")

    def _drain_queue(self) -> None:
        """Discard the messages left in the reader queue, e.g. by an aborted command."""
        while True:
            try:
                stale = self.reader._queue.get_nowait()
            except Empty:
                return
            self.log.debug(f"Discarded stale message: {stale.header}")

    def _send(self, header: dict) -> None:
        """Send a header-only message.

        :param header: the header to send.
        :type header: dict
        """
        self.sender.send(Message(header=header))

    def _command(self, header: dict) -> Message:
        """Send a command and return its reply.

        :param header: the command.
        :type header: dict
        :return: the reply.
        :rtype: Message
        """
        self._check_usable()
        self._send(header)
        reply = self._next_reply(self.command_timeout)
        self.log.debug(f"{header['cmd']} reply: {reply.header}")
        return reply

    def _wait_for_message(self, timeout: float | None = None) -> Message:
        """Read the queue until a message is received.

        :param timeout: how long to wait, in seconds; None waits forever.
        :type timeout: float | None
        :raises ConnectionLost: if the connection is lost while waiting.
        :raises FireqTimeout: if nothing arrives in time; the client is then desynchronized.
        :return: message received from the server
        :rtype: Message
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            wait = POLL_INTERVAL if deadline is None else min(POLL_INTERVAL, max(0.0, deadline - time.monotonic()))
            try:
                return self.reader._queue.get(timeout=wait)
            except Empty:
                # only checked once the queue is empty, so messages received before the drop are still served
                if self._connection_lost():
                    raise ConnectionLost() from None
                if deadline is not None and time.monotonic() >= deadline:
                    # v0 has no request ids: a late reply would be read by the next command
                    self._desynchronized = True
                    raise FireqTimeout(f"No reply from the server within {timeout} s") from None

    def _next_reply(self, timeout: float | None = None, server_warnings: list[str] | None = None) -> Message:
        """Wait for the next message that is not a warning; warnings are logged.

        :param timeout: how long to wait, in seconds; None waits forever.
        :type timeout: float | None
        :param server_warnings: list that collects the warnings, if given.
        :type server_warnings: list[str] | None
        :raises ServerError: if the server answers with an error packet.
        :return: the reply.
        :rtype: Message
        """
        while True:
            message = self._wait_for_message(timeout)
            kind = message.header.get("type")
            if kind == "warning":
                self.log.warning(f"Server warning: {message.header.get('msg')}")
                if server_warnings is not None:
                    server_warnings.append(message.header.get("msg"))
            elif kind == "error":
                raise ServerError(message.header.get("msg"))
            else:
                return message

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _make_df_from_shots(array: np.ndarray, shots: int) -> pd.DataFrame:
        """Turn the shot array into a DataFrame indexed by shot and time.

        The array must be 1-D. The resulting dataframe will be multi-index and include a
        shot and time column. The time column will be fixed to 0 if the input array length is equal
        to the input number of shots.

        :param array: shot samples.
        :type array: np.ndarray
        :param shots: number of shots in the array.
        :type shots: int
        :return: DataFrame with "shot" and "time" index levels and a "value" column.
        :rtype: pd.DataFrame
        """
        total = len(array)
        points_per_shot = total // shots
        if total % shots != 0:
            raise ValueError(f"Array length ({total}) is not divisible by shots ({shots})")
        data_2d = array.reshape(shots, points_per_shot)

        n_times = data_2d.shape[1]
        index = pd.MultiIndex.from_product(
            [range(shots), range(n_times)],
            names=["shot", "time"],
        )
        return pd.DataFrame({"value": data_2d.ravel()}, index=index)

    @staticmethod
    def _is_status(message: Message, msg: str) -> bool:
        """Tell whether a message is the status packet with the given msg.

        :param message: message received from the server.
        :type message: Message
        :param msg: expected value of the msg field.
        :type msg: str
        :return: True if the message is that status packet.
        :rtype: bool
        """
        return message.header.get("type") == "status" and message.header.get("msg") == msg

    @classmethod
    def _has_sweep_expressions(cls, value: object) -> bool:
        """Tell whether a configuration holds sweep expressions, with the server's rule.

        A sweep expression is the string value of a ``$`` key that starts with "#". Values under
        ``_`` keys never count, nor do the items of a list value of a ``$`` key; node maps and
        lists of children are searched recursively.

        :param value: configuration, or part of it.
        :type value: object
        :return: True if a sweep expression is found.
        :rtype: bool
        """
        if isinstance(value, list):
            return any(cls._has_sweep_expressions(item) for item in value)
        if not isinstance(value, dict):
            return False
        for raw_key, item in value.items():
            key = str(raw_key)  # YAML allows non-string keys
            if key.startswith("$"):
                if isinstance(item, str) and item.startswith("#"):
                    return True
            elif not key.startswith("_") and cls._has_sweep_expressions(item):
                return True
        return False

    @staticmethod
    def _decode_package(package: Message) -> np.ndarray:
        """Decode the payload of a DMA package into complex IQ samples.

        :param package: the DMA package to decode.
        :type package: Message
        :return: complex IQ samples.
        :rtype: np.ndarray
        """
        # get dtype, sent over msgpack so it is made of lists
        dt = np.dtype([(name, fmt) for name, fmt in package.header.get("format")])
        arr = np.frombuffer(package.data, dtype=dt)
        return arr["real"] + 1.0j * arr["imag"]


class Client(FireqClient):
    """Deprecated: use :class:`FireqClient`, and ``start_interactive()`` instead of ``start()``."""

    def __init__(
        self,
        host: str,
        port: int,
        auth_token: str = DEFAULT_AUTH_TOKEN,
        output_dir: str = DEFAULT_OUTPUT_DIR,
        client_name: str = DEFAULT_CLIENT_NAME,
    ) -> None:
        """Initialize the client with the arguments of the old constructor.

        :param host: server hostname or IP address.
        :type host: str
        :param port: server TCP port.
        :type port: int
        :param auth_token: token sent to the server in the handshake.
        :type auth_token: str
        :param output_dir: directory where the experiment data is stored.
        :type output_dir: str
        :param client_name: name sent to the server in the handshake.
        :type client_name: str
        """
        warnings.warn("Client is deprecated, use FireqClient", DeprecationWarning, stacklevel=2)
        super().__init__(host, port, token=auth_token, output_dir=output_dir, client_name=client_name)

    def start(self) -> None:
        """Connect and run the interactive prompt (deprecated alias of ``start_interactive``)."""
        warnings.warn("Client.start() is deprecated, use start_interactive()", DeprecationWarning, stacklevel=2)
        self.start_interactive()


__all__ = ["Client", "FireqClient", "RunIterator", "ServerError"]
