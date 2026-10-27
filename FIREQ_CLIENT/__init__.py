"""FIREQ client package: the scriptable client library, with the interactive prompt in :mod:`FIREQ_CLIENT.cli`."""

import logging

from .client import Client, FireqClient, RunIterator
from .config import ExperimentConfig, load_config
from .errors import (
    ConfigurationError,
    ConnectionFailed,
    ConnectionLost,
    Desynchronized,
    FireqError,
    FireqTimeout,
    ProtocolError,
    RunFailed,
    RunRejected,
    ServerError,
)
from .export import export
from .results import ExperimentResult, PointResult, ProgressEvent, load_experiment

# the library only logs; the applications configure the handlers
logging.getLogger(__name__).addHandler(logging.NullHandler())


__all__ = [
    "Client",
    "ConfigurationError",
    "ConnectionFailed",
    "ConnectionLost",
    "Desynchronized",
    "ExperimentConfig",
    "ExperimentResult",
    "FireqClient",
    "FireqError",
    "FireqTimeout",
    "PointResult",
    "ProgressEvent",
    "ProtocolError",
    "RunFailed",
    "RunIterator",
    "RunRejected",
    "ServerError",
    "export",
    "load_config",
    "load_experiment",
]
