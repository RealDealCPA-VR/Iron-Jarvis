"""Settings: defaults, then a JSON file, then the environment."""

import json
import os
from dataclasses import dataclass, replace

from . import _deprecated
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
        _deprecated.warn_once("attribute", "Config.max_retries", "Config.retry_limit")
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
#: Deprecated in 2.4.0 (RENAME.md): the old config key and environment variable.
OLD_KEY, NEW_KEY = "max_retries", "retry_limit"
OLD_VAR, NEW_VAR = "SYNCER_MAX_RETRIES", "SYNCER_RETRY_LIMIT"


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
        if OLD_KEY in data:
            if NEW_KEY in data:
                raise ConfigError(f"{path}: give {NEW_KEY} or the deprecated {OLD_KEY}, not both")
            _deprecated.warn_once("config key", f'the config key "{OLD_KEY}"', f'"{NEW_KEY}"')
            data = {(NEW_KEY if key == OLD_KEY else key): value for key, value in data.items()}
        for key, value in data.items():
            if key not in KEYS:
                raise ConfigError(f"{path}: unknown setting {key!r}")
            values[key] = _typed(key, value, path)
    env = os.environ if environ is None else environ
    if OLD_VAR in env:
        if NEW_VAR in env:
            raise ConfigError(f"give {NEW_VAR} or the deprecated {OLD_VAR}, not both")
        _deprecated.warn_once("environment", OLD_VAR, NEW_VAR)
        values[NEW_KEY] = _typed(NEW_KEY, env[OLD_VAR], OLD_VAR)
    for var, name in ENVIRONMENT.items():
        if var in env:
            values[name] = _typed(name, env[var], var)
    return replace(Config(), **values)
