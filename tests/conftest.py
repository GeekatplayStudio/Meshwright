"""
Tests must never write into the real %LOCALAPPDATA%\\Meshwright.

That folder holds crash-recovery snapshots, which after a crash can be the only copy of
someone's unsaved work. Things in this code base create a session there the moment they are
imported or constructed — mcp_server builds a MeshService at import, and several tests build
one with autosave on — so every test run used to leave a stub behind, and a developer's folder
ended up holding a hundred and seventy of them, indistinguishable from real ones.

The redirect happens here, at import, rather than in a fixture: some modules create their
service while pytest is still collecting, before any fixture has run. Tests that set
LOCALAPPDATA themselves (the session-store tests) still do, and get this sandbox back afterwards.
"""
import os
import shutil
import tempfile

_SANDBOX = tempfile.mkdtemp(prefix="meshwright-tests-")
os.environ["LOCALAPPDATA"] = _SANDBOX


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_SANDBOX, ignore_errors=True)
