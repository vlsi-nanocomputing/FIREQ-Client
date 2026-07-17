import os

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import Completer, Completion, PathCompleter, WordCompleter
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory

# At class level or inside run, define a combined completer
class CommandCompleter(Completer):
    """Complete commands, and for 'run_yaml' complete file paths."""
    def __init__(self):
        self.commands = WordCompleter(['ping', 'run_yaml', 'reset_all', 'mts_sync', 'quit', 'exit'], ignore_case=True)
        self.path_completer = PathCompleter(expanduser=True)

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor.lstrip()
        if text.startswith("run_yaml "):
            # Use path completer for the part after command
            # We take the text after the first space and feed to path completer
            after = text[len("run_yaml "):]
            # Create a modified document for the path part
            from prompt_toolkit.document import Document
            path_doc = Document(after, len(after))
            yield from self.path_completer.get_completions(path_doc, complete_event)
        else:
            # Command completion for the whole line
            yield from self.commands.get_completions(document, complete_event)

def make_prompt_session():
    history_file = os.path.expanduser("~/.fireq_client_history")
    return PromptSession(
            history=FileHistory(history_file),
            completer=CommandCompleter(),
            auto_suggest=AutoSuggestFromHistory(),
        )