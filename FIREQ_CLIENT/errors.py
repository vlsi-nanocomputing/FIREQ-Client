"""Exceptions raised by the FIREQ client library.

Every exception derives from :class:`FireqError`, so a script can catch them all at once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .results import ExperimentResult

#: a configuration that fails midway leaves the hardware partially configured (server protocol §3)
RESET_HINT = "Send reset_all before applying a configuration again"


class FireqError(Exception):
    """Base class of the client errors."""


class ConnectionFailed(FireqError):
    """The TCP connection could not be opened, or the server sent no handshake."""


class ConnectionLost(FireqError, ConnectionError):
    """The connection to the server dropped."""

    def __init__(self, message: str = "Connection to the server lost", *, likely_auth_failure: bool = False) -> None:
        """Initialize the error.

        :param message: error message.
        :type message: str
        :param likely_auth_failure: True when the server closed the connection right after the handshake,
            which is how the v0 server refuses a wrong token.
        :type likely_auth_failure: bool
        """
        if likely_auth_failure:
            message += " (the server closed the connection right after the handshake: check the token)"
        super().__init__(message)
        self.likely_auth_failure = likely_auth_failure


class FireqTimeout(FireqError, TimeoutError):
    """No reply within the timeout; the client is then desynchronized."""


class Desynchronized(FireqError):
    """A reply sequence was broken: the next reply may belong to an older command, call ``reconnect()``."""


class ProtocolError(FireqError, TypeError):
    """The server sent an unexpected packet (e.g. a server of another protocol version)."""


class ServerError(FireqError):
    """The server answered a command with an error packet."""

    def __init__(self, message: str | None) -> None:
        """Initialize the error.

        :param message: the error message sent by the server.
        :type message: str | None
        """
        super().__init__(message)
        self.message = message


class ConfigurationError(ServerError, ValueError):
    """A configuration was refused by the server, or is invalid before being sent."""

    hint = RESET_HINT


class RunRejected(ServerError):
    """``config_and_run`` was refused before the experiment header."""


class RunFailed(FireqError):
    """Raised by :meth:`ExperimentResult.raise_for_status` for an aborted run or a run with server errors."""

    def __init__(self, message: str, result: ExperimentResult) -> None:
        """Initialize the error.

        :param message: error message.
        :type message: str
        :param result: the failed result.
        :type result: ExperimentResult
        """
        super().__init__(message)
        self.result = result
