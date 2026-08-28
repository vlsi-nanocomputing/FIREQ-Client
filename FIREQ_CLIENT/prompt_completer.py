"""Command completion for the client REPL."""

import os
from collections.abc import Iterable

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import CompleteEvent, Completer, Completion, PathCompleter, WordCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory


class CommandCompleter(Completer):
    """Complete commands and command-specific arguments."""

    def __init__(self) -> None:
        """Initialize the completers for commands and their arguments."""
        # YAML files for run_yaml, directories for export
        self.yaml_completer = PathCompleter(
            expanduser=True,
            file_filter=lambda path: os.path.isdir(path) or path.endswith((".yaml", ".yml")),
        )
        self.dir_completer = PathCompleter(expanduser=True, only_directories=True)

        # Define what arguments each command expects (command -> per-arg completers).
        # Commands not listed here (ping, set_nyquist, quit, ...) get no suggestions
        # in argument position. Note: commands are matched case-sensitively, exactly
        # like _dispatch_command.
        self.argument_specs = {
            "run_yaml": [self.yaml_completer],
            "export": [self.dir_completer, self.dir_completer],
        }
        commands = [
            'ping', 'run_yaml', 'reset_all', 'mts_sync', 'set_nyquist',
            'trigger_manually', 'export', 'quit', 'exit',
        ]
        self.command_completer = WordCompleter(commands)

    def get_completions(self, document: Document, complete_event: CompleteEvent) -> Iterable[Completion]:
        """Yield completions for the command or for the argument being typed.

        :param document: the current prompt document.
        :type document: Document
        :param complete_event: the completion event.
        :type complete_event: CompleteEvent
        :return: the completion suggestions.
        :rtype: Iterable[Completion]
        """
        text_before = document.text_before_cursor
        cmd_tokens = text_before.lstrip().split()

        # No command typed yet -> command completion
        if not cmd_tokens:
            yield from self.command_completer.get_completions(document, complete_event)
            return

        # Which argument are we completing?
        if text_before[-1].isspace():
            arg_index = len(cmd_tokens) - 1  # cursor at the start of a new argument
        else:
            arg_index = len(cmd_tokens) - 2  # cursor inside the last token

        # Still completing the command word
        if arg_index < 0:
            yield from self.command_completer.get_completions(document, complete_event)
            return

        # Command-specific argument completers
        command = cmd_tokens[0]
        spec = self.argument_specs.get(command)
        if not spec:
            return

        # Clamp out-of-range args to the last completer
        completer = spec[arg_index] if arg_index < len(spec) else spec[-1]

        # Micro-document for the argument being typed (empty right after a space)
        current_text = "" if text_before[-1].isspace() else cmd_tokens[-1]
        arg_doc = Document(current_text, len(current_text))
        yield from completer.get_completions(arg_doc, complete_event)


def make_prompt_session() -> PromptSession[str]:
    """Create a PromptSession with history, completion and auto-suggestion.

    :return: the configured prompt session.
    :rtype: PromptSession[str]
    """
    history_file = os.path.expanduser("~/.fireq_client_history")
    return PromptSession(
        history=FileHistory(history_file),
        completer=CommandCompleter(),
        auto_suggest=AutoSuggestFromHistory(),
    )
