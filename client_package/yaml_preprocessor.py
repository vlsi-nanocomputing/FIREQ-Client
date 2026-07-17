import yaml
from typing import Any, Dict

def resolve_placeholders(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Given a dict with keys 'preprocess', 'variables', 'sys_config',
    replace all "%variable_name" placeholders in sys_config (and anywhere else)
    with the actual values from 'preprocess', then return a dict containing
    only 'variables' and the resolved 'sys_config'.
    """
    preprocess = data.get("preprocess", {})
    variables = data.get("variables", {})
    sys_config = data.get("sys_config", {})

    def resolve_value(value: Any) -> Any:
        """Recursively replace strings starting with '%' by preprocess values."""
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


def load_and_resolve(yaml_path: str) -> Dict[str, Any]:
    """Load a YAML file and resolve all '%...' placeholders."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return resolve_placeholders(data)