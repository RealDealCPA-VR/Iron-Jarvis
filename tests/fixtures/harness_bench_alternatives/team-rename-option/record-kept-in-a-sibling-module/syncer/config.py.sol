"""Settings: defaults, then a JSON file, then the environment."""

import json
import os
from dataclasses import dataclass, replace

from syncer_compat import deprecated, old_attribute
from .errors import ConfigError
from .retry import DEFAULT_RETRIES


@dataclass(frozen=True)
class Config:
    remote: str = ""
    retry_limit: int = DEFAULT_RETRIES
    timeout_s: float = 10.0
    workers: int = 2

    __getattr__ = old_attribute


#: The keys a config file may have, and their types.
KEYS = {"remote": str, "retry_limit": int, "timeout_s": float, "workers": int}
#: The environment variables, and the setting each one gives.
ENVIRONMENT = {
    "SYNCER_REMOTE": "remote",
    "SYNCER_RETRY_LIMIT": "retry_limit",
    "SYNCER_TIMEOUT_S": "timeout_s",
    "SYNCER_WORKERS": "workers",
}
#: Names deprecated in 2.4.0: old -> new.
RENAMED_KEYS = {"max_retries": "retry_limit"}
RENAMED_VARIABLES = {"SYNCER_MAX_RETRIES": "SYNCER_RETRY_LIMIT"}


def _typed(name, value, source):
    kind = KEYS[name]
    try:
        if kind is int and isinstance(value, (bool, float)):
            raise ValueError
        result = kind(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{source}: {name} must be {kind.__name__}, not {value!r}") from None
    if kind in (int, float) and result < 0:
        raise ConfigError(f"{source}: {name} must be 0 or more")
    return result


def _renamed(mapping, given, where):
    """``given`` with every old name replaced by its new one (warning once each)."""
    out = dict(given)
    for old, new in mapping.items():
        if old in out:
            if new in out:
                raise ConfigError(f"{where}: {old} is the old name of {new}; give only {new}")
            deprecated(f"{where} {old}", f"{old} is deprecated, use {new}")
            out[new] = out.pop(old)
    return out


def load_config(path=None, environ=None):
    """The settings: the defaults, then the JSON object in the file at
    ``path`` (when given), then the environment (``environ``, default
    ``os.environ``). ``ConfigError`` for a key the file may not have, or a
    value of the wrong type."""
    values = {}
    if path is not None:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"{path}: {exc}") from None
        if not isinstance(data, dict):
            raise ConfigError(f"{path}: the settings must be a JSON object")
        for key, value in _renamed(RENAMED_KEYS, data, "config file").items():
            if key not in KEYS:
                raise ConfigError(f"{path}: unknown setting {key!r}")
            values[key] = _typed(key, value, path)
    env = os.environ if environ is None else environ
    relevant = {var: env[var] for var in (*ENVIRONMENT, *RENAMED_VARIABLES) if var in env}
    for var, value in _renamed(RENAMED_VARIABLES, relevant, "environment").items():
        name = ENVIRONMENT[var]
        values[name] = _typed(name, value, var)
    return replace(Config(), **values)
