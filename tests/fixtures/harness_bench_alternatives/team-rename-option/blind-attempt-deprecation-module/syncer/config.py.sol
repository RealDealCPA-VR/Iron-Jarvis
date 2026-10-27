"""Settings: defaults, then a JSON file, then the environment."""

import json
import os
from dataclasses import dataclass, replace

from ._deprecation import warn_once
from .errors import ConfigError
from .retry import DEFAULT_RETRIES


@dataclass(frozen=True)
class Config:
    remote: str = ""
    retry_limit: int = DEFAULT_RETRIES
    timeout_s: float = 10.0
    workers: int = 2

    @property
    def max_retries(self):
        """Deprecated alias of ``retry_limit``."""
        warn_once("attribute", "the max_retries attribute is deprecated; use retry_limit")
        return self.retry_limit


#: The keys a config file may have, and their types.
KEYS = {"remote": str, "retry_limit": int, "timeout_s": float, "workers": int}
#: The environment variables, and the setting each one gives.
ENVIRONMENT = {
    "SYNCER_REMOTE": "remote",
    "SYNCER_RETRY_LIMIT": "retry_limit",
    "SYNCER_TIMEOUT_S": "timeout_s",
    "SYNCER_WORKERS": "workers",
}


#: The deprecated config key, and its new name.
OLD_KEY = "max_retries"
#: The deprecated environment variable, and its new name.
OLD_VARIABLE = "SYNCER_MAX_RETRIES"


def _typed(name, value, source):
    kind = KEYS[name]
    try:
        if kind is int and isinstance(value, bool):
            raise ValueError
        if kind is int and isinstance(value, float):
            raise ValueError
        result = kind(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{source}: {name} must be {kind.__name__}, not {value!r}") from None
    if kind in (int, float) and result < 0:
        raise ConfigError(f"{source}: {name} must be 0 or more")
    return result


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
        if OLD_KEY in data and "retry_limit" in data:
            raise ConfigError(f"{path}: max_retries and retry_limit were both given; use only retry_limit")
        for key, value in data.items():
            if key == OLD_KEY:
                warn_once("config key", "max_retries is deprecated; use retry_limit")
                key = "retry_limit"
            elif key not in KEYS:
                raise ConfigError(f"{path}: unknown setting {key!r}")
            values[key] = _typed(key, value, path)
    env = os.environ if environ is None else environ
    if OLD_VARIABLE in env and "SYNCER_RETRY_LIMIT" in env:
        raise ConfigError("SYNCER_MAX_RETRIES and SYNCER_RETRY_LIMIT were both given; use only SYNCER_RETRY_LIMIT")
    if OLD_VARIABLE in env:
        warn_once("environment variable", "SYNCER_MAX_RETRIES is deprecated; use SYNCER_RETRY_LIMIT")
        values["retry_limit"] = _typed("retry_limit", env[OLD_VARIABLE], OLD_VARIABLE)
    for var, name in ENVIRONMENT.items():
        if var in env:
            values[name] = _typed(name, env[var], var)
    return replace(Config(), **values)
