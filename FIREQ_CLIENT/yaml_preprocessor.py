"""Preprocessing of experiment YAML configuration files."""

from typing import Any, TypeAlias

import yaml

# A value coming from (or resolved in) a YAML file.
YamlValue: TypeAlias = str | int | float | bool | None | list["YamlValue"] | dict[str, "YamlValue"]


def resolve_placeholders(data: dict[str, Any]) -> dict[str, Any]:
    """Resolve the placeholders in a YAML config dict.

    Given a dict with keys 'preprocess', 'variables', 'sys_config',
    replace all "%variable_name" placeholders in sys_config (and anywhere else)
    with the actual values from 'preprocess', then return a dict containing
    only 'variables' and the resolved 'sys_config'.

    :param data: raw YAML configuration.
    :type data: dict[str, Any]
    :return: dict with the resolved 'sys_config' and the unchanged 'variables'.
    :rtype: dict[str, Any]
    """
    preprocess = data.get("preprocess", {})
    variables = data.get("variables", {})
    sys_config = data.get("sys_config", {})

    def resolve_value(value: YamlValue) -> YamlValue:
        """Recursively replace strings starting with '%' by preprocess values.

        :param value: value to resolve.
        :type value: YamlValue
        :return: the resolved value.
        :rtype: YamlValue
        """
        if isinstance(value, str) and value.startswith("%"):
            key = value[1:]  # strip the leading '%'
            if key in preprocess:
                return preprocess[key]
            # If not found, raise error
            else:
                raise ValueError(f"Undefined variable: {key}")
        elif isinstance(value, dict):
            # in case of dict, resolve for all key, value pairs
            return {k: resolve_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [resolve_value(item) for item in value]
        else:
            return value

    if preprocess:
        resolved_config = resolve_value(sys_config)
    else:
        resolved_config = sys_config

    return {
        "variables": variables,
        "sys_config": resolved_config,
    }


def load_and_resolve(yaml_path: str) -> dict[str, Any]:
    """Load a YAML file and resolve all '%...' placeholders.

    :param yaml_path: path to the YAML file.
    :type yaml_path: str
    :return: dict with the resolved configuration.
    :rtype: dict[str, Any]
    """
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return resolve_placeholders(data)
