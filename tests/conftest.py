"""Wires in the standard HA custom-component test harness."""
from __future__ import annotations

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(scope="session", autouse=True)
def _warm_up_pycares_shutdown_thread():
    """aiodns' pycares backend starts one process-wide daemon thread
    (pycares._shutdown_manager) the moment any DNS resolver is
    constructed, and it's designed to outlive individual resolvers for
    the whole process rather than ever stopping. Whichever test happens
    to be first to trigger that (e.g. the first one to exercise a real
    aiohttp session) gets blamed for a "lingering thread" by
    pytest-homeassistant-custom-component's strict per-test cleanup
    check - even though the thread has nothing to do with that
    particular test. Triggering it here, once, before any test's own
    thread snapshot is taken, means it's always already-there rather
    than "new" for whichever test happens to go first.
    """
    try:
        import pycares

        pycares.Channel()
    except ImportError:
        pass
