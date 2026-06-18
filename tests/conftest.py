"""Shared pytest fixtures.

Stub for chunk 0.1 (scaffold only). Fixtures (synthetic RawCell grids, stub
LLM/engines, golden loaders) are added alongside the chunks that need them.
"""

import pytest


def pytest_sessionfinish(session, exitstatus):
    """Treat "no tests collected" (exit 5) as success.

    The scaffold ships before the first test (chunk 0.2), so ``pytest -q`` would
    otherwise exit 5 and turn CI red. Once real tests exist this is a no-op.
    """
    if exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED:
        session.exitstatus = pytest.ExitCode.OK
