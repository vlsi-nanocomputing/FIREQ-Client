"""Tests for the resolution of the '%' preprocess tokens."""

import pytest

from FIREQ_CLIENT.yaml_preprocessor import resolve_placeholders


def _resolve(sys_config, variables=None, preprocess=None):
    data = {"sys_config": sys_config, "variables": variables or {}}
    if preprocess is not None:
        data["preprocess"] = preprocess
    return resolve_placeholders(data)


def test_whole_token_keeps_the_type():
    pre = {"n": 3, "f": 1.5, "lst": [1, 2]}
    out = _resolve({"a": "%n", "b": "%f", "c": "%lst"}, preprocess=pre)["sys_config"]
    assert out == {"a": 3, "b": 1.5, "c": [1, 2]}


def test_tokens_in_expression():
    pre = {"var1": 1000, "var2": 500}
    out = _resolve({"x": "#%var1 - %var2 + 100"}, preprocess=pre)["sys_config"]
    assert out == {"x": "#1000 - 500 + 100"}


def test_expression_without_spaces():
    out = _resolve({"x": "#%a*%b"}, preprocess={"a": 2, "b": 0.5})["sys_config"]
    assert out == {"x": "#2*0.5"}


def test_longest_name_is_matched():
    out = _resolve({"x": "#%var10+%var1"}, preprocess={"var1": 1, "var10": 10})["sys_config"]
    assert out == {"x": "#10+1"}


def test_sweep_reference_and_modulo_untouched():
    out = _resolve({"x": "#freq", "y": "#a % 2", "z": 5}, preprocess={"a": 1})["sys_config"]
    assert out == {"x": "#freq", "y": "#a % 2", "z": 5}


def test_nested_dicts_and_lists():
    out = _resolve({"n": {"l": ["%a", "#%a + 1"]}}, preprocess={"a": 7})["sys_config"]
    assert out == {"n": {"l": [7, "#7 + 1"]}}


def test_variables_are_resolved():
    out = _resolve({}, variables={"freq": {"start": "%f0", "stop": "#%f0 + 10"}}, preprocess={"f0": 100})
    assert out["variables"] == {"freq": {"start": 100, "stop": "#100 + 10"}}


@pytest.mark.parametrize("preprocess", [None, {}, {"other": 1}])
def test_undefined_variable_raises(preprocess):
    with pytest.raises(ValueError, match="Undefined variable: missing"):
        _resolve({"x": "#%missing + 1"}, preprocess=preprocess)
    with pytest.raises(ValueError, match="Undefined variable: missing"):
        _resolve({"x": "%missing"}, preprocess=preprocess)


def test_list_in_expression_raises():
    with pytest.raises(ValueError, match="cannot be used in an expression"):
        _resolve({"x": "#%lst + 1"}, preprocess={"lst": [1, 2]})


def test_null_sections():
    assert resolve_placeholders({"preprocess": None, "variables": None, "sys_config": {"a": 1}}) == {
        "variables": {},
        "sys_config": {"a": 1},
    }
