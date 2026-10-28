#!/usr/bin/env python3

"""CORRECT Core Package.

This package contains the core modules for the CORRECT application,
refactored for improved modularity and maintainability.
"""

__version__ = "0.3.0"

# rt-utils <= 1.2.7 is missing "import warnings" in rt_utils/rtstruct_builder.py,
# which makes its warn_only path raise NameError instead of warning. Applied here
# so every rt_utils call site in the package is covered.
from .rt_utils_compat import patch_rt_utils_compat_bootstrap  # noqa: E402

patch_rt_utils_compat_bootstrap()

