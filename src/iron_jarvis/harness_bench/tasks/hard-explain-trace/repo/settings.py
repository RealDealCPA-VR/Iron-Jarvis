"""Shop settings."""

ACTIVE_PROFILE = "retail"

# Rules switched off for every profile.
DISABLED = {"coupon"}

WHOLESALE_MODE = False

if WHOLESALE_MODE:
    ACTIVE_PROFILE = "wholesale"
