"""Backward compatibility for release 2.4.0: the names max_retries had."""

import functools
import warnings

_already = {}


def deprecated(old_name, message):
    """Warns about ``old_name`` the first time only (this process)."""
    if _already.get(old_name):
        return
    _already[old_name] = True
    warnings.warn(message, category=DeprecationWarning, stacklevel=3)


def accepts_max_retries(function):
    """Lets ``function`` (which takes ``retry_limit=``) also take the deprecated
    ``max_retries=``."""

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        if "max_retries" in kwargs:
            if "retry_limit" in kwargs:
                raise TypeError(f"{function.__qualname__}() got both retry_limit and max_retries")
            deprecated("keyword max_retries", "the keyword max_retries is deprecated, pass retry_limit instead")
            kwargs["retry_limit"] = kwargs.pop("max_retries")
        return function(*args, **kwargs)

    return wrapper


def old_attribute(obj, name):
    """``__getattr__`` for classes whose ``max_retries`` became ``retry_limit``."""
    if name == "max_retries":
        deprecated("attribute max_retries", f"{type(obj).__name__}.max_retries is deprecated, read retry_limit")
        return obj.retry_limit
    raise AttributeError(f"{type(obj).__name__!r} object has no attribute {name!r}")
