"""Preprocessing of experiment YAML configuration files."""

import re
from typing import Any, TypeAlias

import yaml

# A value coming from (or resolved in) a YAML file.
YamlValue: TypeAlias = str | int | float | bool | None | list["YamlValue"] | dict[str, "YamlValue"]

# A preprocess token: '%' followed by an identifier. The match is greedy, so '%var10' is never
# read as '%var1' followed by '0', and a '%' not followed by an identifier (e.g. modulo) is kept.
_TOKEN = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)")


def resolve_placeholders(data: dict[str, Any]) -> dict[str, Any]:
    """Resolve the placeholders in a YAML config dict.

    Given a dict with keys 'preprocess', 'variables', 'sys_config', replace every
    "%variable_name" token in the values of 'sys_config' and 'variables' with the value
    from 'preprocess', then return a dict containing only the resolved 'variables' and
    'sys_config'.

    A value that is exactly one token ("%gain") is replaced by the preprocess value with
    its type. A token inside a longer string, e.g. the expression "#%var1 - %var2 + 100",
    is replaced by the text of the value ("#1000 - 500 + 100"), the rest of the string is
    kept as written.

    :param data: raw YAML configuration.
    :type data: dict[str, Any]
    :raises ValueError: if a token names a variable missing from 'preprocess', or a list
        or dict value is used inside a longer string.
    :return: dict with the resolved 'sys_config' and 'variables'.
    :rtype: dict[str, Any]
    """
    preprocess = data.get("preprocess") or {}
    variables = data.get("variables") or {}
    sys_config = data.get("sys_config") or {}

    def lookup(key: str) -> YamlValue:
        """Return the preprocess value of a token.

        :param key: token name, without the leading '%'.
        :type key: str
        :raises ValueError: if the name is not defined in 'preprocess'.
        :return: the preprocess value.
        :rtype: YamlValue
        """
        if key not in preprocess:
            raise ValueError(f"Undefined variable: {key}")
        return preprocess[key]

    def substitute(match: re.Match[str]) -> str:
        """Return the text replacing a token embedded in a longer string.

        :param match: the token match.
        :type match: re.Match[str]
        :raises ValueError: if the preprocess value is a list or a dict.
        :return: the text of the preprocess value.
        :rtype: str
        """
        replacement = lookup(match.group(1))
        if isinstance(replacement, list | dict):
            raise ValueError(
                f"Variable {match.group(1)} is a {type(replacement).__name__}, it cannot be used in an expression"
            )
        return str(replacement)

    def resolve_value(value: YamlValue) -> YamlValue:
        """Recursively replace the '%' tokens by preprocess values.

        :param value: value to resolve.
        :type value: YamlValue
        :return: the resolved value.
        :rtype: YamlValue
        """
        if isinstance(value, str):
            whole = _TOKEN.fullmatch(value)
            if whole:
                # the whole value is a token: keep the type of the preprocess value
                return lookup(whole.group(1))
            return _TOKEN.sub(substitute, value)
        elif isinstance(value, dict):
            # in case of dict, resolve for all key, value pairs
            return {k: resolve_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [resolve_value(item) for item in value]
        else:
            return value

    return {
        "variables": resolve_value(variables),
        "sys_config": resolve_value(sys_config),
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
    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML file {yaml_path}: expected a mapping at the top level")
    return resolve_placeholders(data)
