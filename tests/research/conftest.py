"""Fixtures for the research tests. The synthetic data builders live in helpers.py."""

import numpy as np
import pandas as pd
import pytest

from .helpers import make_games, make_matchup_tables


@pytest.fixture
def games() -> pd.DataFrame:
    return make_games()


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(0)


@pytest.fixture(scope="module")
def matchup_tables():
    return make_matchup_tables()
