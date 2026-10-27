"""Experiment configurations: loading, overrides and local validation."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigurationError
from .yaml_preprocessor import resolve_placeholders

# msgpack integers are at most 64 bit
_INT_MIN = -(2**63)
_UINT_MAX = 2**64 - 1


@dataclass(frozen=True)
class ExperimentConfig:
    """A resolved configuration, ready to be sent to the server."""

    sys_config: dict[str, Any]
    variables: dict[str, Any]
    #: default experiment name: the file name without extension, None for a dict
    name: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the configuration in the shape saved in ``experiment_summary.yaml``.

        :return: the ``variables`` and ``sys_config`` keys.
        :rtype: dict[str, Any]
        """
        return {"variables": self.variables, "sys_config": self.sys_config}


def load_config(
    config: str | os.PathLike | Mapping[str, Any] | ExperimentConfig,
    *,
    preprocess: Mapping[str, Any] | None = None,
    variables: Mapping[str, Any] | None = None,
) -> ExperimentConfig:
    """Load, override, resolve and validate an experiment configuration.

    :param config: a YAML path, a dict shaped like the YAML (``preprocess``, ``variables``, ``sys_config``)
        or an already loaded configuration.
    :type config: str | os.PathLike | Mapping[str, Any] | ExperimentConfig
    :param preprocess: values merged over the ``preprocess`` block before the ``%`` substitution.
    :type preprocess: Mapping[str, Any] | None
    :param variables: sweep variables replacing or extending the ``variables`` block.
    :type variables: Mapping[str, Any] | None
    :raises ConfigurationError: if the configuration is unreadable, has no ``sys_config``, misses a
        preprocess value or holds values the server cannot receive.
    :return: the resolved configuration.
    :rtype: ExperimentConfig
    """
    if isinstance(config, ExperimentConfig):
        if preprocess:
            raise ConfigurationError("preprocess overrides need the raw configuration, not an ExperimentConfig")
        resolved = config.as_dict()
        name = config.name
    else:
        name = None
        if isinstance(config, Mapping):
            data = dict(config)
        else:
            name = Path(config).stem
            try:
                with open(config, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
            except (OSError, yaml.YAMLError) as err:
                raise ConfigurationError(f"Cannot read {config}: {err}") from err
            if not isinstance(data, dict):
                raise ConfigurationError(f"Invalid YAML file {config}: expected a mapping at the top level")
        if preprocess:
            data["preprocess"] = {**(data.get("preprocess") or {}), **preprocess}
        try:
            resolved = resolve_placeholders(data)
        except ValueError as err:
            raise ConfigurationError(str(err)) from err

    if variables:
        resolved["variables"] = {**resolved["variables"], **variables}
    # ``variables`` may be empty (a single run), an empty ``sys_config`` would be applied as an empty system tree
    if not resolved.get("sys_config"):
        raise ConfigurationError(f"Invalid configuration {name or '(dict)'}: missing or empty 'sys_config'")
    _check_encodable(resolved, "config")
    return ExperimentConfig(sys_config=resolved["sys_config"], variables=resolved["variables"], name=name)


def _check_encodable(value: object, path: str) -> None:
    """Check that a value can be sent to the server with msgpack.

    A bad value would otherwise hang the v0 server or close the session.

    :param value: value to check.
    :type value: object
    :param path: location of the value, used in the error message.
    :type path: str
    :raises ConfigurationError: on a non-string map key or a value msgpack cannot encode.
    """
    if isinstance(value, bool) or value is None or isinstance(value, float | str):
        return
    if isinstance(value, int):
        if not _INT_MIN <= value <= _UINT_MAX:
            raise ConfigurationError(f"{path}: integer {value} does not fit in 64 bits")
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _check_encodable(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ConfigurationError(f"{path}: key {key!r} is a {type(key).__name__}, keys must be strings")
            _check_encodable(item, f"{path}.{key}")
        return
    raise ConfigurationError(f"{path}: value {value!r} of type {type(value).__name__} cannot be sent to the server")
