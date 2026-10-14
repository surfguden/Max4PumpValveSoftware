import pytest


@pytest.fixture(autouse=True)
def allow_explicit_test_simulations(monkeypatch):
    """Offline tests explicitly opt in; normal application runs reject simulation."""
    monkeypatch.setenv('PUMPVALVE_TEST_SIMULATION', '1')
