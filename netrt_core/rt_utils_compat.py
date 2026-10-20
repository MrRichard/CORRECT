"""
Compatibility shim for rt-utils <= 1.2.7.

rt-utils 1.2.7 ships rt_utils/rtstruct_builder.py without "import warnings",
so the warn_only path in validate_contour_image_in_series_data() raises

    NameError: name 'warnings' is not defined

instead of emitting the warning and letting processing continue. The bug was
fixed upstream in qurit/rt-utils commit b70ad40b (2023-01-25) but has never
been released, so every published version still carries it.

Injecting the stdlib warnings module into the library namespace restores the
intended behaviour without changing or upgrading the pinned dependency.
"""

import logging
import warnings

logger = logging.getLogger(__name__)


def patch_rt_utils_warnings():
    """
    Inject the stdlib `warnings` module into rt_utils.rtstruct_builder.

    Idempotent: the patch is applied at most once per process, and the
    informational log line is emitted only when the patch is actually applied.

    Returns:
        bool: True if the patch was applied by this call, False if the module
              was already fine (e.g. a future rt-utils release that imports
              warnings itself).
    """
    try:
        from rt_utils import rtstruct_builder
    except ImportError:
        # rt-utils is not installed; nothing to patch.
        return False

    if hasattr(rtstruct_builder, "warnings"):
        return False

    rtstruct_builder.warnings = warnings
    logger.warning(
        "Patched rt-utils <= 1.2.7: 'import warnings' is missing from "
        "rt_utils.rtstruct_builder, which made its warn_only path raise "
        "NameError instead of warning (upstream fix b70ad40b is unreleased)."
    )
    return True


def patch_rt_utils_compat_bootstrap():
    """
    Package-level entry point: apply the shim, logging failures at debug level
    so a missing or broken rt-utils install can never stop the application
    from starting.
    """
    try:
        patch_rt_utils_warnings()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"rt-utils compat shim not applied: {exc}")
