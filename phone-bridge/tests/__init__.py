"""Test package setup.

Point HOME at a throwaway directory *before* any project module is imported so
default paths (locations.db, nexus.db, config) never touch the real user data.
"""

import atexit
import os
import shutil
import tempfile

_TEST_HOME = tempfile.mkdtemp(prefix="nexus-test-home-")
os.environ["HOME"] = _TEST_HOME
os.environ.pop("PHONE_BRIDGE_CONFIG", None)
atexit.register(shutil.rmtree, _TEST_HOME, True)
