"""Command completion for the client REPL."""

import os
from collections.abc import Iterable

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import CompleteEvent, Completer, Completion, PathCompleter, WordCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory


# At class level or inside run, define a combined completer
class CommandCompleter(Completer):
    """Complete commands, and for 'run_yaml' complete file paths."""

    def __init__(self) -> None:
        """Initialize the command and path completers."""
        self.commands = WordCompleter(
            ['ping', 'run_yaml', 'reset_all', 'mts_sync', 'set_nyquist', 'trigger_manually', 'export', 'quit', 'exit'],
            ignore_case=True,
        )
        self.path_completer = PathCompleter(expanduser=True)

    def get_completions(self, document: Document, complete_event: CompleteEvent) -> Iterable[Completion]:
        """Yield command completions, or path completions for 'run_yaml'.

        :param document: the current prompt document.
        :type document: Document
        :param complete_event: the completion event.
        :type complete_event: CompleteEvent
        :return: the completion suggestions.
        :rtype: Iterable[Completion]
        """
        text = document.text_before_cursor.lstrip()
        if text.startswith("run_yaml "):
            # Use path completer for the part after command
            # We take the text after the first space and feed to path completer
            after = text[len("run_yaml "):]
            # Create a modified document for the path part
            path_doc = Document(after, len(after))
            yield from self.path_completer.get_completions(path_doc, complete_event)
        else:
            # Command completion for the whole line
            yield from self.commands.get_completions(document, complete_event)


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
