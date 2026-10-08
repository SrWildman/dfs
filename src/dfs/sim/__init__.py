"""Lineup simulator: how a lineup's points are distributed, given how its players move together.

`dfs.model.distribution` gives each player's own outcome distribution. This package adds the dependence
between players (empirical correlations of normal scores, `correlation.py`), a Gaussian-copula simulator over
lineups (`simulate.py`) and a back-test against history (`backtest.py`). No sheet, no credentials.
"""
