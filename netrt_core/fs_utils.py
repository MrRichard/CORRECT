"""Small filesystem helpers that behave the same on Linux and Windows."""

import logging
import os
import shutil

logger = logging.getLogger(__name__)


def link_or_copy(src, dst):
    """Make ``dst`` refer to ``src`` as cheaply as the platform allows.

    Tries a symlink first (Linux, or Windows with SeCreateSymbolicLinkPrivilege),
    then a hard link (NTFS, same volume, no privilege needed), then a full copy.
    Returns the method used: "symlink", "hardlink" or "copy".
    """
    try:
        os.symlink(src, dst)
        return "symlink"
    except (OSError, NotImplementedError, AttributeError):
        pass
    try:
        os.link(src, dst)
        return "hardlink"
    except (OSError, NotImplementedError, AttributeError):
        pass
    shutil.copy2(src, dst)
    return "copy"
