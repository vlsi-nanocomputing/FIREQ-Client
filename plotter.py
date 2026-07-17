import os
import shlex
from client_package.plotting.plot_2d import _plot_2d, _plot_3d_heatmap, _plot_iq, export, _plot_spectr
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import Completer, Completion, PathCompleter, WordCompleter
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory

# ... existing imports ...
from prompt_toolkit.completion import Completer, WordCompleter, PathCompleter, Completion
from prompt_toolkit.document import Document

class CommandCompleter(Completer):
    """Complete commands and command‑specific arguments."""

    def __init__(self):
        # ── basic completers you can reuse ──────────────────
        self.command_completer = WordCompleter(
            ['plot_2d', 'plot_iq', 'plot_3d_heat', 'quit', 'exit'],
            ignore_case=True,
        )
        self.file_completer = PathCompleter(expanduser=True)
        self.dir_completer = PathCompleter(expanduser=True, only_directories=True)
        # example: a simple options completer
        self.plot_2d_opt_completer = WordCompleter(['','ri', 'r', 'i'], ignore_case=True)
        self.plot_3dheat_opt_completer = WordCompleter(['','p', 'r', 'i'], ignore_case=True)
        self.save_completer = WordCompleter(['','save'], ignore_case=True)

        # ── define what arguments each command expects ──────
        self.argument_specs = {
            'plot_2d':      [self.dir_completer, self.plot_2d_opt_completer, self.save_completer], 
            'plot_3d_heat': [self.dir_completer, self.plot_3dheat_opt_completer, self.save_completer],  
            'plot_iq':      [self.dir_completer, self.dir_completer, self.save_completer],
            'plot_spectr':      [self.dir_completer, self.dir_completer, self.save_completer],  
            'export':      [self.dir_completer, self.dir_completer], 
            # add more commands as needed
        }
        commands = [cmd for cmd in self.argument_specs.keys()]
        self.command_completer = WordCompleter(
            commands,
            ignore_case=True,
        )

    def get_completions(self, document, complete_event):
        # text before the cursor
        text_before = document.text_before_cursor
        # text split
        cmd_tokens = text_before.lstrip().split()

        # the cmd tokens is empty -> no command typed yet
        if not cmd_tokens:
            yield from self.command_completer.get_completions(document, complete_event)
            return

        # Determine which argument we are completing:
        if text_before.endswith(' '):
            arg_index = len(cmd_tokens) - 1   # e.g. tokens = ["cmd", "first"] → arg_index=1 (second arg)
        else:
            arg_index = len(cmd_tokens) - 2   # e.g. tokens = ["cmd", "fir"] → arg_index=0 (first arg)
        
        # still completing the command
        if arg_index < 0:
            yield from self.command_completer.get_completions(document, complete_event)
            return
        
        # define the command and the spec for the command
        command = cmd_tokens[0]
        spec = self.argument_specs.get(command)
        if not spec:
            return

        # Make sure we don't go out of range (use the last completer if too many args)
        if arg_index >= len(spec):
            completer = spec[-1]
        else:
            completer = spec[arg_index]

        # feed to the completer the string up to the last whitespace
        idx = text_before.rfind(' ')
        current_text = text_before[idx+1:]

        # Create a micro‑document for the isolated argument
        arg_doc = Document(current_text, len(current_text))

        # Delegate to the appropriate completer
        yield from completer.get_completions(arg_doc, complete_event)

if __name__ == "__main__":
    history_file = os.path.expanduser("~/.plotter_history")
    completer = PromptSession(
            history=FileHistory(history_file),
            completer=CommandCompleter(),
            auto_suggest=AutoSuggestFromHistory(),
        )
    while True:
        try:
            cmd = completer.prompt("> ").strip()
            if not cmd:
                continue
        except KeyboardInterrupt:
            cmd = 'quit'
        
        # if the command is quit or exit, break the loop and exit the script
        if cmd.lower() in ('quit', 'exit'):
            break

        # parse the command
        cmd_parts = shlex.split(cmd)
        command = cmd_parts[0]
        if command == "plot_2d":
            exp_dir = cmd_parts[1] if len(cmd_parts) >=2 else ''
            plot_opt = cmd_parts[2] if len(cmd_parts) >=3 else ''
            save_opt = cmd_parts[3] if len(cmd_parts) >=4 else ''
            if not exp_dir:
                print("No experiment directory defined")
            save_opt = True if save_opt == 'save' else False
            if plot_opt == "ri":
                _plot_2d(cmd_parts[1], plot_magnitude=False, plot_imag=True, plot_real=True, save=save_opt)
            elif plot_opt == "r":
                _plot_2d(cmd_parts[1], plot_magnitude=False, plot_imag=False, plot_real=True, save=save_opt)
            elif plot_opt == "i":
                _plot_2d(cmd_parts[1], plot_magnitude=False, plot_imag=True, plot_real=False, save=save_opt)
            else:
                _plot_2d(cmd_parts[1], save=save_opt)
        elif command == "plot_3d_heat":
            exp_dir = cmd_parts[1] if len(cmd_parts) >=2 else ''
            plot_opt = cmd_parts[2] if len(cmd_parts) >=3 else ''
            save_opt = cmd_parts[3] if len(cmd_parts) >=4 else ''
            save_opt = True if save_opt == 'save' else False
            if not exp_dir:
                print("No experiment directory defined")
            if plot_opt == "p":
                _plot_3d_heatmap(cmd_parts[1], plot_magnitude=False, plot_phase=True,save=save_opt)
            elif plot_opt == "r":
                _plot_3d_heatmap(cmd_parts[1], plot_magnitude=False, plot_imag=False, plot_real=True,save=save_opt)
            elif plot_opt == "i":
                _plot_3d_heatmap(cmd_parts[1], plot_magnitude=False, plot_imag=True, plot_real=False,save=save_opt)
            else:
                _plot_3d_heatmap(cmd_parts[1],save=save_opt)
        elif command == "plot_iq":
            exp_dir_0 = cmd_parts[1] if len(cmd_parts) >=2 else ''
            exp_dir_1 = cmd_parts[2] if len(cmd_parts) >=3 else ''
            save_opt = cmd_parts[3] if len(cmd_parts) >=4 else ''
            save_opt = True if save_opt == 'save' else False
            if len(cmd_parts) < 3:
                print("command must contain two experiment directories")
            else:
                _plot_iq(exp_dir_0, exp_dir_1, save_opt)
        elif command == "plot_spectr":
            exp_dir_0 = cmd_parts[1] if len(cmd_parts) >=2 else ''
            exp_dir_1 = cmd_parts[2] if len(cmd_parts) >=3 else ''
            save_opt = cmd_parts[3] if len(cmd_parts) >=4 else ''
            save_opt = True if save_opt == 'save' else False
            if len(cmd_parts) < 3:
                print("command must contain two experiment directories")
            else:
                _plot_spectr(exp_dir_0, exp_dir_1, save_opt)
        elif command == "export":
            fromdir = cmd_parts[1] if len(cmd_parts) >=2 else ''
            todir = cmd_parts[2] if len(cmd_parts) >=3 else ''
            if len(cmd_parts) < 2:
                print("command must contain a from and to directory")
            else:
                export(fromdir, todir)
        else:
            print(f"Unknown command: {command}")

