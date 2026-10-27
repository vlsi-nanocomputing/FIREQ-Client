"""Interactive prompt on top of :class:`FIREQ_CLIENT.FireqClient`."""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable, Iterable
from typing import Any

import yaml
from tqdm import tqdm

from ..client import FireqClient
from ..errors import ConnectionLost, Desynchronized
from ..results import ExperimentResult, ProgressEvent
from .completer import make_prompt_session

#: the plot commands, mapped to the name of the FIREQ_PLOTTER function they call
PLOT_COMMANDS = {"plot_2d": "plot_2d", "plot_3d_heat": "plot_3d_heatmap", "plot_iq": "plot_iq"}

USAGE = {
    "config_yaml": "config_yaml <yaml_file> [name=value ...]",
    "run_yaml": "run_yaml <yaml_file> [name=value ...]",
    "trigger_manually": "trigger_manually <generator_IP_name>",
    "set_nyquist": "set_nyquist <tile> <block> <zone>",
    "export": "export <from_directory> <to_directory>",
}
MIN_ARGS = {"config_yaml": 1, "run_yaml": 1, "trigger_manually": 1, "set_nyquist": 3, "export": 2}


def parse_overrides(tokens: Iterable[str]) -> dict[str, Any]:
    """Parse ``name=value`` preprocess overrides; values are read as YAML, so ``0.25`` is a float.

    :param tokens: the ``name=value`` tokens.
    :type tokens: Iterable[str]
    :raises ValueError: if a token has no ``=``.
    :return: the overrides.
    :rtype: dict[str, Any]
    """
    overrides = {}
    for token in tokens:
        name, sep, value = token.partition("=")
        if not sep or not name:
            raise ValueError(f"Expected name=value, got {token!r}")
        overrides[name] = yaml.safe_load(value)
    return overrides


def format_result(result: ExperimentResult) -> str:
    """Summarize a run in one line.

    :param result: the run.
    :type result: ExperimentResult
    :return: the summary.
    :rtype: str
    """
    status = "aborted" if result.aborted else "completed"
    errors = f", errors: {'; '.join(result.server_errors)}" if result.server_errors else ""
    where = f", saved in {result.directory}" if result.directory else ""
    return (
        f"{result.name}: {status}, {result.points_completed}/{result.points_expected} points, "
        f"sources {result.sources}{where}{errors}"
    )


class TqdmProgress:
    """Progress callback drawing a tqdm bar."""

    def __init__(self) -> None:
        """Initialize without a bar, created at the first event."""
        self.bar: tqdm | None = None

    def __call__(self, event: ProgressEvent) -> None:
        """Advance the bar by one point.

        :param event: the progress event.
        :type event: ProgressEvent
        """
        if self.bar is None:
            self.bar = tqdm(total=event.points_total, desc="Fetching")
        self.bar.update(1)

    def close(self) -> None:
        """Close the bar, if any."""
        if self.bar is not None:
            self.bar.close()


class InteractiveShell:
    """The client REPL: each command is a library call plus printing."""

    def __init__(self, client: FireqClient) -> None:
        """Initialize the shell on a client.

        :param client: the client to drive, connected or not.
        :type client: FireqClient
        """
        self.client = client
        self.last: ExperimentResult | None = None

    def connect_with_retry(self, ask: Callable[[str], str] = input) -> bool:
        """Connect the client, asking whether to retry on failure.

        :param ask: function prompting the user.
        :type ask: Callable[[str], str]
        :return: True once connected, False if the user gave up.
        :rtype: bool
        """
        while True:
            try:
                self.client.connect()
                return True
            except Exception as err:
                print(f"Connection failed: {type(err).__name__}: {err}")
                if ask(" retry? [y/n] ").strip().lower() != "y":
                    return False

    def run(self, close_on_exit: bool | None = None) -> None:
        """Run the prompt until ``quit``, ``exit`` or Ctrl-D.

        :param close_on_exit: close the connection at the end; None closes it only if this shell opened it.
        :type close_on_exit: bool | None
        """
        opened = False
        if not self.client.connected:
            if not self.connect_with_retry():
                return
            opened = True
        if close_on_exit is None:
            close_on_exit = opened
        try:
            session = make_prompt_session()
            print(f"Experiment data is saved in {os.path.abspath(self.client.output_dir)}")
            print("Connected. Type commands, 'quit' to exit.")
            while True:
                cmd = session.prompt("> ").strip()
                if cmd.lower() in ("quit", "exit"):
                    break
                self.execute(cmd)
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            if close_on_exit:
                self.client.close()
                print("Disconnected.")

    def execute(self, cmd: str) -> bool:
        """Run one command line, printing its outcome.

        :param cmd: command line entered by the user.
        :type cmd: str
        :return: False if the command failed.
        :rtype: bool
        """
        if not cmd:
            return True
        try:
            self.dispatch(cmd)
            return True
        except KeyboardInterrupt as err:
            partial = getattr(err, "result", None)
            if partial is not None:
                self.last = partial
            print("Interrupted, the connection is closed: type 'reconnect' to go on.")
        except (ConnectionLost, Desynchronized) as err:
            print(f"{err}. Type 'reconnect' to go on.")
        except Exception as err:
            print(f"{cmd.split(maxsplit=1)[0]} failed: {type(err).__name__}: {err}")
        return False

    def dispatch(self, cmd: str) -> None:
        """Run one command line, raising on failure.

        :param cmd: command line entered by the user.
        :type cmd: str
        """
        command, *args = shlex.split(cmd)
        if len(args) < MIN_ARGS.get(command, 0):
            print(f"Usage: {USAGE[command]}")
            return
        client = self.client
        if command not in ("reconnect", "last", "export", *PLOT_COMMANDS) and client.connected:
            # replies left over by an aborted command must not be mistaken for this command's reply
            client._drain_queue()

        if command == "ping":
            print(f"Ping: {client.ping() * 1000:.1f} ms")
        elif command == "config_yaml":
            client.apply_configuration(args[0], preprocess=parse_overrides(args[1:]))
            print("Configuration applied.")
        elif command == "run_yaml":
            progress = TqdmProgress()
            try:
                self.last = client.run(args[0], preprocess=parse_overrides(args[1:]), progress=progress)
            finally:
                progress.close()
            print(format_result(self.last))
        elif command in ("reset_all", "mts_sync"):
            getattr(client, command)()
            print(f"{command}: ok")
        elif command == "trigger_manually":
            client.trigger_manually(args[0])
            print(f"{command}: ok")
        elif command == "set_nyquist":
            client.set_nyquist(int(args[0]), int(args[1]), int(args[2]))
            print(f"{command}: ok")
        elif command == "export":
            client.export(args[0], args[1])
        elif command == "reconnect":
            client.reconnect()
            print("Reconnected.")
        elif command == "last":
            print(format_result(self.last) if self.last else "No run yet.")
        elif command in PLOT_COMMANDS:
            self._plot(command, args)
        else:
            print(f"Unknown command: {command}")

    def _plot(self, command: str, args: list[str]) -> None:
        """Plot the last run, or the given exported directories.

        :param command: the plot command.
        :type command: str
        :param args: ``last`` or experiment directories.
        :type args: list[str]
        """
        try:
            import FIREQ_PLOTTER  # noqa: PLC0415 - matplotlib is an optional dependency
        except ImportError:
            print("The plot commands need matplotlib: pip install fireq-client[plot]")
            return
        if args in ([], ["last"]):
            if self.last is None:
                print("No run yet.")
                return
            data: object = self.last
        else:
            data = args
        getattr(FIREQ_PLOTTER, PLOT_COMMANDS[command])(data)
