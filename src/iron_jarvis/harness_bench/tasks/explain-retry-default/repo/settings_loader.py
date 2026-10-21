"""Every setting: the built-in defaults, then this site's overrides on top."""
import site_config
from defaults import DEFAULTS


def load():
    merged = dict(DEFAULTS)
    merged.update(site_config.OVERRIDES)
    return merged
