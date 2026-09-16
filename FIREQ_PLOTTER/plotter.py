"""Interactive plotter for FIREQ experiment data."""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import CompleteEvent, Completer, Completion, PathCompleter, WordCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory

from .plotting import _plot_2d, _plot_3d_heatmap, _plot_iq_curve
from .plotting.common import load_and_plot

#: the plotting commands of the REPL, mapped to the function that runs them.
PLOT_COMMANDS: dict[str, Callable[..., object]] = {
    "plot_2d": _plot_2d,
    "plot_3d_heat": _plot_3d_heatmap,
    "plot_iq_curve": _plot_iq_curve,
}


class CommandCompleter(Completer):
    """Complete commands and command-specific arguments."""

    def __init__(self) -> None:
        """Initialize the completers for commands and their arguments."""
        self.dir_completer = PathCompleter(expanduser=True, only_directories=True)

        # every plotting command takes a series of experiment directories
        self.argument_specs = {command: [self.dir_completer] for command in PLOT_COMMANDS}
        commands = [*self.argument_specs, "quit", "exit"]
        self.command_completer = WordCompleter(
            commands,
            ignore_case=True,
        )

    def get_completions(self, document: Document, complete_event: CompleteEvent) -> Iterable[Completion]:
        """Yield completions for the command or for the argument being typed.

        :param document: the current prompt document.
        :type document: Document
        :param complete_event: the completion event.
        :type complete_event: CompleteEvent
        :return: the completion suggestions.
        :rtype: Iterable[Completion]
        """
        # text before the cursor
        text_before = document.text_before_cursor
        # text split
        cmd_tokens = text_before.lstrip().split()

        # the cmd tokens is empty -> no command typed yet
        if not cmd_tokens:
            yield from self.command_completer.get_completions(document, complete_event)
            return

        # Determine which argument we are completing:
        if text_before.endswith(" "):
            arg_index = len(cmd_tokens) - 1  # e.g. tokens = ["cmd", "first"] → arg_index=1 (second arg)
        else:
            arg_index = len(cmd_tokens) - 2  # e.g. tokens = ["cmd", "fir"] → arg_index=0 (first arg)

        # still completing the command
        if arg_index < 0:
            yield from self.command_completer.get_completions(document, complete_event)
            return

        # define the command and the spec for the command
        command = cmd_tokens[0]
        spec = self.argument_specs.get(command)
        if not spec:
            return

        # The arguments are a series of directories, the completer is the
        # same for every argument position.
        completer = spec[-1] if arg_index >= len(spec) else spec[arg_index]

        # feed to the completer the string up to the last whitespace
        idx = text_before.rfind(" ")
        current_text = text_before[idx + 1 :]

        # Create a micro-document for the isolated argument
        arg_doc = Document(current_text, len(current_text))

        # Delegate to the appropriate completer
        yield from completer.get_completions(arg_doc, complete_event)


def parse_experiment_dirs(tokens: Sequence[str]) -> list[Path]:
    """Return the experiment directories given on the command line.

    :param tokens: the command line arguments, following the command name.
    :type tokens: Sequence[str]
    :raises ValueError: if no directory is given, or one of the tokens does
        not point at a directory.
    :return: the experiment directories.
    :rtype: list[Path]
    """
    if not tokens:
        raise ValueError("usage: <command> <experiment_dir> [<experiment_dir> ...]")

    exp_dirs = []
    for token in tokens:
        path = Path(token).expanduser()
        if not path.is_dir():
            raise ValueError(f"not a directory: {path}")
        exp_dirs.append(path)

    return exp_dirs


def main() -> None:
    """Run the interactive plotter REPL."""
    history_file = os.path.expanduser("~/.plotter_history")
    session = PromptSession(
        history=FileHistory(history_file),
        completer=CommandCompleter(),
        auto_suggest=AutoSuggestFromHistory(),
    )
    while True:
        try:
            command_line = session.prompt("> ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not command_line:
            continue

        # if the command is quit or exit, break the loop and exit the script
        if command_line.lower() in ("quit", "exit"):
            break

        # parse the command
        try:
            tokens = shlex.split(command_line)
        except ValueError as err:
            print(f"cannot parse the command: {err}")
            continue

        if not tokens:
            continue

        command, *arguments = tokens
        plotting_func = PLOT_COMMANDS.get(command)
        if plotting_func is None:
            print(f"Unknown command: {command}")
            continue

        try:
            exp_dirs = parse_experiment_dirs(arguments)
            plotted = load_and_plot(exp_dirs, plotting_func)
        except KeyboardInterrupt:
            print("plot interrupted")
            continue
        except Exception as err:
            print(f"{command} failed: {type(err).__name__}: {err}")
            continue

        print(f"plotted {len(plotted)} acquisition IP(s): {', '.join(plotted)}")


if __name__ == "__main__":
    main()
