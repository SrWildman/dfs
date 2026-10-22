"""`dfs backtest`'s settings: what a build run is told, and the registry other sessions plug into.

A settings file is TOML; every key is optional and a missing one is today's default (what a plain
`dfs lineups suggest` does):

    name = "ownership uncertainty on"      # label in the report (default: the file name)
    pool = "auto"                          # "auto": Sam's saved pool where there is one (`pool_store`), else
                                           # the constructed one; "constructed": always the constructed pool

    [builds]
    cash = 3                               # cash builds (`--cash`)
    gpp = 12                               # GPP builds; leave out for every QB stack, two each (`--gpp`)
    rank = "top1"                          # or "share" (`--rank`)
    seconds = 600                          # search budget per week (the live default is 120)

    [rules]                                # the rules box, one line per label, as he would type it
    adjust = "Nacua +2, Kyren -15%"        # applied: your frame (`suggest_state.frame_from_rules`)
    ownership = "Kyren 40%"                # applied: your frame
    never = "Bijan Robinson"               # applied: the player leaves the pool (`Never use`)
    # Always use / Max exposure / Stack rules / Builds wanted / Build rules need the iterate path, which
    # reads a built tab: the backtest names them as "not applied" rather than guess

    [flags]                                # named switches other sessions register (below)
    ownership_uncertainty = true
    portfolio = { objective = "robust", wide = 8 }

**The registry.** A flag is a `Flag`: a name, one line of doc, and any of four optional hooks, registered
with `register(Flag(...))` from the module that implements it (session H registers `ownership_mean` and
`ownership_uncertainty`, session I `portfolio`) so `dfs backtest` never needs editing for a new switch:

- `prepare(ctx, value) -> ctx`: change what the builder is given (the projections, the specs, the lines or the
  simulated field in `ctx.lines`), e.g. swap in a field that draws ownership per simulation;
- `build(ctx, value, inner) -> builds`: replace or wrap the builder; `inner(ctx)` is today's builder, so a
  flag can take its candidates and choose a different set;
- `assign(builds, contest, entries, value) -> builds`: which of the week's builds go into one contest Sam
  entered (`entries` of them); today's is the first `entries` of the contest's kind, in build order;
- `choose(ctx, builds, contests, value) -> {contest id: [lineups]}`: the week's entries chosen as one set,
  across every contest at once (`contests`: the week's `WeekContest`s, what Sam knew before the lock: the
  payout table and his entry count, never the standings), each lineup a list of player names (a build's or
  any other legal lineup). It runs once per week right after the builds, on the same context, and is saved
  with them; a contest it leaves out keeps `assign` / today's.

Flags apply in registration order; a flag set to `false` is off. A module that registers flags is named in
`PLUGIN_MODULES` (imported when a settings file names a flag nothing has registered yet; one that is not on
this branch is skipped). The backtest rebuilds the specs and lines from `ctx.edge` after the `prepare` hooks
only if a hook returned a context without them (`ctx.lines is None`), so a hook may change the projection
and let the backtest derive the rest.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from dfs import lineup_suggest as ls
from dfs import suggest_state

# Modules that call `register` at import. Sessions H and I add theirs here (one line each).
PLUGIN_MODULES: tuple[str, ...] = ("dfs.sim.own_error_flag", "dfs.sim.portfolio_flag")

# The live builder stops its search after 120 s, so runs of one slate differ (two runs agree on 13 of 14
# builds, and only 5 of 13 match a search that finishes; under load, more). A backtest compares settings, so
# its default lets the search finish: the same builds every run (about 5 minutes a week).
# `seconds = 120` mimics the live clock.
SEARCH_SECONDS = 600.0
POOL_CHOICES = ("auto", "constructed")
RULE_LABELS_APPLIED = ("Adjust", "Ownership", "Never use")


@dataclass
class BuildContext:
    """Everything one week's builder run is given. `lines` is `sim_inputs.SimLines` (None: derive from
    `edge`)."""

    week: int
    edge: pd.DataFrame
    tags: dict[str, str]
    now: datetime
    cap: int
    metric: str
    settings: Settings
    typed_cash: Any = None  # sim_inputs.CashLine: the season median before the week
    specs: dict | None = None
    lines: Any = None
    n_sims: int = 5000
    notes: list[str] = field(default_factory=list)


Prepare = Callable[[BuildContext, Any], BuildContext]
Build = Callable[[BuildContext, Any, Callable[[BuildContext], list[ls.Build]]], list[ls.Build]]
Assign = Callable[[list[ls.Build], Any, int, Any], list[ls.Build]]


@dataclass(frozen=True)
class WeekContest:
    """One contest Sam entered that week as a `choose` hook sees it: what he knew before the lock."""

    contest_id: str
    name: str  # the series
    fmt: str  # "cash" / "gpp"
    kind: Any  # sim.payouts.ContestKind: size, payout table, fee
    entries: int  # how many entries he made in it


Choose = Callable[[BuildContext, list[ls.Build], list[WeekContest], Any], dict[str, list[list[str]]]]


@dataclass(frozen=True)
class Flag:
    name: str
    doc: str
    prepare: Prepare | None = None
    build: Build | None = None
    assign: Assign | None = None
    choose: Choose | None = None
    version: str = "1"  # bump when the implementation changes: it is part of the cache key


FLAGS: dict[str, Flag] = {}


def register(flag: Flag) -> Flag:
    """Add (or replace) a flag by name. Returns it, so a module can `FLAG = register(Flag(...))`."""
    FLAGS[flag.name] = flag
    return flag


def load_plugins() -> list[str]:
    """Import `PLUGIN_MODULES`; returns the ones that are not on this branch."""
    missing = []
    for name in PLUGIN_MODULES:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError:
            missing.append(name)
    return missing


@dataclass(frozen=True)
class Settings:
    name: str = "today"
    pool: str = "auto"
    cash: int = ls.DEFAULT_CASH_BUILDS
    gpp: int | None = None
    rank: str = ls.DEFAULT_RANK
    seconds: float = SEARCH_SECONDS
    rules: Mapping[str, str] = field(default_factory=dict)
    flags: Mapping[str, Any] = field(default_factory=dict)

    def active_flags(self) -> list[Flag]:
        """The registered flags switched on, in registration order."""
        on = {k for k, v in self.flags.items() if v not in (False, None)}
        return [f for f in FLAGS.values() if f.name in on]

    def fingerprint(self, code: str = "") -> str:
        """A short hash of everything that changes the builds: the settings (not their label), the active
        flags' versions and `code` (the repository state: `backtest.code_stamp`)."""
        body = {
            "pool": self.pool,
            "cash": self.cash,
            "gpp": self.gpp,
            "rank": self.rank,
            "seconds": self.seconds,
            "rules": {k.casefold(): v.strip() for k, v in sorted(self.rules.items()) if v.strip()},
            "flags": {k: self.flags[k] for k in sorted(self.flags) if self.flags[k] not in (False, None)},
            "flag_versions": {f.name: f.version for f in self.active_flags()},
            "code": code,
        }
        return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:12]

    def describe(self) -> list[str]:
        gpp = "every QB stack" if self.gpp is None else str(self.gpp)
        out = [f"cash {self.cash}, GPP {gpp}, rank {self.rank}, {self.seconds:g} s a week, pool {self.pool}"]
        out += [f"rules: {k}: {v}" for k, v in self.rules.items() if v.strip()]
        out += [f"flag {k} = {v}" for k, v in self.flags.items() if v not in (False, None)]
        return out

    def unsupported_rules(self) -> list[str]:
        """Rules lines the backtest cannot apply (they need a built tab to iterate on)."""
        applied = {x.casefold() for x in RULE_LABELS_APPLIED}
        return [k for k, v in self.rules.items() if v.strip() and k.casefold() not in applied]


class SettingsError(ValueError):
    pass


_BUILD_KEYS = {"cash", "gpp", "rank", "seconds"}
_TOP_KEYS = {"name", "pool", "builds", "rules", "flags"}


def from_dict(data: Mapping[str, Any], default_name: str = "today") -> Settings:
    """`Settings` from a parsed settings file; unknown keys are errors (a typo must not silently run
    defaults)."""
    unknown = set(data) - _TOP_KEYS
    if unknown:
        raise SettingsError(f"unknown settings key(s) {sorted(unknown)}; allowed: {sorted(_TOP_KEYS)}")
    builds = dict(data.get("builds", {}))
    bad = set(builds) - _BUILD_KEYS
    if bad:
        raise SettingsError(f"unknown [builds] key(s) {sorted(bad)}; allowed: {sorted(_BUILD_KEYS)}")
    pool = str(data.get("pool", "auto"))
    if pool not in POOL_CHOICES:
        raise SettingsError(f"pool must be one of {POOL_CHOICES}, not {pool!r}")
    rank = str(builds.get("rank", ls.DEFAULT_RANK))
    if rank not in ls.RANKS:
        raise SettingsError(f"rank must be one of {ls.RANKS}, not {rank!r}")
    rules = {str(k): str(v) for k, v in dict(data.get("rules", {})).items()}
    labels = {label.casefold(): label for label in suggest_state.RULE_FIELDS}
    labels.update(
        {attr: label for label, attr in suggest_state.RULE_FIELDS.items()}
    )  # `never` for "Never use"
    for key in list(rules):
        match = labels.get(key.casefold()) or labels.get(key.casefold().replace("_", " "))
        if match is None:
            raise SettingsError(f"unknown rules line {key!r}; the rules box has {sorted(labels.values())}")
        rules[match] = rules.pop(key)
    flags = dict(data.get("flags", {}))
    gpp = builds.get("gpp")
    return Settings(
        name=str(data.get("name", default_name)),
        pool=pool,
        cash=int(builds.get("cash", ls.DEFAULT_CASH_BUILDS)),
        gpp=None if gpp is None else int(gpp),
        rank=rank,
        seconds=float(builds.get("seconds", SEARCH_SECONDS)),
        rules=rules,
        flags=flags,
    )


def load(path: str | Path | None) -> Settings:
    """Read a settings file (None: today's defaults) and check its flags are registered."""
    if path is None:
        return Settings()
    p = Path(path)
    try:
        data = tomllib.loads(p.read_text())
    except OSError as e:
        raise SettingsError(f"cannot read {p}: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise SettingsError(f"{p} is not valid TOML: {e}") from e
    settings = from_dict(data, default_name=p.stem)
    check_flags(settings)
    return settings


def check_flags(settings: Settings) -> None:
    """Every flag the settings switch on must be registered (importing `PLUGIN_MODULES` first)."""
    wanted = [k for k, v in settings.flags.items() if v not in (False, None)]
    if any(k not in FLAGS for k in wanted):
        load_plugins()
    unknown = [k for k in wanted if k not in FLAGS]
    if unknown:
        have = ", ".join(sorted(FLAGS)) or "none registered on this branch"
        raise SettingsError(f"unknown flag(s) {unknown}; registered: {have}")


def with_flags(settings: Settings, **flags: Any) -> Settings:
    return replace(settings, flags={**settings.flags, **flags})
