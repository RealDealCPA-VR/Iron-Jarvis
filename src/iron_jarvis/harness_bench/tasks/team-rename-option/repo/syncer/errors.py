"""The errors syncer raises."""


class SyncError(Exception):
    """Something went wrong while syncing."""


class TransientError(SyncError):
    """A failure that is worth trying again (a timeout, a dropped connection)."""


class ConfigError(SyncError):
    """Settings that cannot be used."""
