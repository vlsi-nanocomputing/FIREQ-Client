"""Command line entry point of the FIREQ client: options and prompts, then the interactive shell."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable, Sequence

from ..client import DEFAULT_AUTH_TOKEN, DEFAULT_OUTPUT_DIR, FireqClient
from .shell import InteractiveShell


def setup_logging(level: int) -> logging.Logger:
    """Configure logging for the client application.

    :param level: logging level.
    :type level: int
    :return: the application logger.
    :rtype: logging.Logger
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    return logging.getLogger(__name__)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line options; the missing ones are prompted.

    :param argv: arguments, defaults to ``sys.argv[1:]``.
    :type argv: Sequence[str] | None
    :return: the options.
    :rtype: argparse.Namespace
    """
    parser = argparse.ArgumentParser(prog="fireq-client", description="Interactive client for the FIREQ server.")
    parser.add_argument("--host", help="server IP or hostname")
    parser.add_argument("--port", type=int, help="server TCP port")
    parser.add_argument("--token-env", metavar="VAR", help="environment variable holding the auth token")
    parser.add_argument("--output-dir", help="directory where the experiment data is stored")
    parser.add_argument("--log-level", choices=["debug", "info"], help="logging level")
    parser.add_argument("--script", metavar="FILE", help="run the commands of FILE, one per line, then exit")
    return parser.parse_args(argv)


def _ask(ask: Callable[[str], str], prompt: str, default: str) -> str:
    """Prompt for a value, returning the default on an empty answer.

    :param ask: function prompting the user.
    :type ask: Callable[[str], str]
    :param prompt: the question.
    :type prompt: str
    :param default: the value of an empty answer.
    :type default: str
    :return: the answer.
    :rtype: str
    """
    return ask(f'# {prompt} (press Enter for "{default}")\n').strip() or default


def main(argv: Sequence[str] | None = None, ask: Callable[[str], str] = input) -> int:
    """Start the client: read the options, prompt for the missing ones, connect and run the shell.

    :param argv: command line arguments, defaults to ``sys.argv[1:]``.
    :type argv: Sequence[str] | None
    :param ask: function prompting the user.
    :type ask: Callable[[str], str]
    :return: the exit status.
    :rtype: int
    """
    args = parse_args(argv)
    logger = setup_logging(level=logging.INFO)
    logger.info("### FIREQ Client startup ###\n")

    log_level = args.log_level
    if log_level is None:
        log_level = ask("Input logging level: 'debug', 'info' (press enter for 'info')\n").strip().lower() or "info"
    if log_level == "debug":
        logging.getLogger().setLevel(logging.DEBUG)
    elif log_level != "info":
        logger.warning(f"Unknown logging level '{log_level}', using 'info'")

    host = args.host or _ask(ask, "Insert server IP", "0.0.0.0")
    port = args.port
    if port is None:
        answer = _ask(ask, "Insert server port", "5000")
        try:
            port = int(answer)
        except ValueError:
            logger.error(f"Invalid port number: {answer}")
            return 1
    if args.token_env:
        token = os.environ.get(args.token_env)
        if token is None:
            logger.error(f"Environment variable {args.token_env} is not set")
            return 1
    else:
        token = _ask(ask, "Insert auth token", DEFAULT_AUTH_TOKEN)
    output_dir = args.output_dir or _ask(ask, "Insert the output directory", DEFAULT_OUTPUT_DIR)

    client = FireqClient(host, port, token=token, output_dir=os.path.expanduser(output_dir))
    shell = InteractiveShell(client)
    if args.script is None:
        shell.run()
        return 0

    # unattended: no retry prompt, stop at the first failing command
    try:
        client.connect()
    except Exception as err:
        logger.error(f"Connection failed: {err}")
        return 1
    try:
        with open(args.script, encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.lower() in ("quit", "exit"):
                    break
                print(f"> {line}")
                if not shell.execute(line):
                    return 1
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
