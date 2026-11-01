"""Shared test setup: every test starts with all chaos switches off and empty fake apps."""

import pytest

from mocks import chaos
from mocks.services import reset_data


@pytest.fixture(autouse=True)
def clean_world():
    chaos.reset()
    reset_data()
    yield
    chaos.reset()
    reset_data()
