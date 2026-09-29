"""Look-ahead and recursive-analysis guards for signal and feature code (in the spirit of
freqtrade's ``lookahead-analysis`` and ``recursive-analysis``). Deterministic synthetic OHLC only.

Look-ahead: for many cut points t, a value computed from bars[:t+1] must be identical to the
value at t computed from the full series whose bars after t were randomly perturbed. Any
difference means the code read the future.

Recursive/warm-up: a recursive indicator's value at t depends on where its history starts. The
guards measure how many bars each indicator needs before a later start stops mattering and
assert the runtime's history windows are at least that long. Bars needed to converge within a
relative tolerance, measured on these series:

  indicator (seed)                  1e-3   1e-6   1e-9
  sleeves.wilder_atr n=14 (1st TR)   ~100   ~205   ~285   history_bars: default 450, config min 200
  paper_runtime._ema 26 (SMA seed)    ~60   ~145   ~235   FEATURE_HISTORY_BARS 15m/1h/4h: 600/400/300
  paper_runtime._ema 12 (SMA seed)    ~25    ~70   ~110

The snapshot lanes alone (64 x 15m, 48 x 1h, 36 x 4h) are shorter than the EMA26 warm-up, so any
caller of compute_features without archived history gets unconverged EMAs (about 1% off on the
4h EMA26). The 15m cycle passes the archive; see the PR for the callers that do not.
"""

from __future__ import annotations

import bisect
import math
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from crypto_eval.paper_contracts import PaperTradingError, iso_utc, parse_utc
from crypto_eval.paper_market import MarketDataError, MarketSnapshot
from crypto_eval.paper_runtime import FEATURE_HISTORY_BARS, PaperRuntime, PaperStore, _ema, compute_features
from crypto_eval.sleeves import (
    DEFAULT_SLEEVES,
    H4,
    daily_closes,
    donchian_signal,
    trail_stop,
    tsmom_weights,
    wilder_atr,
    xsmom_weights,
)

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
SECONDS = {"15m": 900, "1h": 3600, "4h": H4}
SNAPSHOT_BARS = {"15m": 64, "1h": 48, "4h": 36}  # what the Gate/fixture snapshot carries per lane


# ------------------------------------------------------------------ synthetic data
def ohlc_walk(count: int, *, seconds: int, seed: int, vol: float, start: datetime = START, drift: float = 0.0,
              price: float = 100.0) -> list[dict[str, Any]]:
    """Seeded geometric random walk with consistent OHLC and positive volume."""

    rng = random.Random(seed)
    out = []
    for i in range(count):
        opened = start + timedelta(seconds=seconds * i)
        o = price
        c = o * math.exp(drift + rng.gauss(0.0, vol))
        h = max(o, c) * (1 + abs(rng.gauss(0.0, vol / 2)))
        low = min(o, c) * (1 - abs(rng.gauss(0.0, vol / 2)))
        out.append({"open_time": iso_utc(opened), "close_time": iso_utc(opened + timedelta(seconds=seconds)),
                    "open": o, "high": h, "low": low, "close": c, "volume": 50.0 + 100.0 * rng.random()})
        price = c
    return out


def aggregate(bars: list[dict[str, Any]], factor: int) -> list[dict[str, Any]]:
    """Consistent higher-timeframe bars from ``factor`` consecutive lower-timeframe bars."""

    out = []
    for i in range(0, len(bars) - factor + 1, factor):
        group = bars[i:i + factor]
        out.append({"open_time": group[0]["open_time"], "close_time": group[-1]["close_time"], "open": group[0]["open"],
                    "high": max(b["high"] for b in group), "low": min(b["low"] for b in group), "close": group[-1]["close"],
                    "volume": sum(b["volume"] for b in group)})
    return out


def perturb_after(bars: list[dict[str, Any]], cutoff: str, seed: int) -> list[dict[str, Any]]:
    """Copy of ``bars`` with every bar closing after ``cutoff`` randomly and violently changed
    (crash or spike, volume blown up). Bars at or before the cutoff are untouched."""

    rng = random.Random(seed)
    limit = parse_utc(cutoff, "cutoff")
    out = []
    for bar in bars:
        if parse_utc(bar["close_time"], "bar.close_time") <= limit:
            out.append(dict(bar))
            continue
        scale = rng.choice((0.35, 2.8)) * (1 + rng.uniform(-0.1, 0.1))
        o, c = bar["open"] * scale, bar["close"] * scale * (1 + rng.uniform(-0.2, 0.2))
        out.append({**bar, "open": o, "close": c, "high": max(o, c, bar["high"] * scale) * 1.05,
                    "low": min(o, c, bar["low"] * scale) * 0.95, "volume": bar["volume"] * rng.uniform(0.05, 20.0)})
    return out


def closed_through(bars: list[dict[str, Any]], cutoff: str) -> list[dict[str, Any]]:
    limit = parse_utc(cutoff, "cutoff")
    return [b for b in bars if parse_utc(b["close_time"], "bar.close_time") <= limit]


def lookahead_violations(fn: Callable[[list[dict[str, Any]]], Any], bars: list[dict[str, Any]], cuts: list[int],
                         seed: int = 7) -> list[int]:
    """Cut points t where ``fn`` evaluated at t differs between bars[:t+1] and the full series
    whose future (after bar t) was perturbed. ``fn`` receives a series and the index t and
    returns the value "at t" for that series."""

    bad = []
    for t in cuts:
        future = perturb_after(bars, bars[t]["close_time"], seed + t)
        if fn(bars[:t + 1], t) != fn(future, t):
            bad.append(t)
    return bad


def warmup_lengths(last_value: Callable[[list[Any]], float], values: list[Any], tolerances: tuple[float, ...],
                   max_window: int) -> dict[float, int]:
    """For each relative tolerance, the smallest history length L such that every start giving at
    least L bars (up to ``max_window``) is within tolerance of the value from the full history."""

    reference = last_value(values)
    need = {tol: 1 for tol in tolerances}
    for window in range(max_window, 0, -1):
        try:
            value = last_value(values[-window:])
        except PaperTradingError:
            error = math.inf
        else:
            error = abs(value - reference) / abs(reference)
        for tol in tolerances:
            if need[tol] == 1 and error >= tol:
                need[tol] = window + 1
        if all(v > 1 for v in need.values()):
            break
    return need


def snapshot_at(lanes: dict[str, list[dict[str, Any]]], cutoff: str, symbol: str = "SYNUSDT") -> MarketSnapshot:
    """A snapshot as the runtime would see it at ``cutoff``: the last closed bars of each lane only."""

    closed = {interval: closed_through(lane, cutoff)[-SNAPSHOT_BARS[interval]:] for interval, lane in lanes.items()}
    at = parse_utc(cutoff, "cutoff")
    last = closed["15m"][-1]["close"]
    one_minute = [{"open_time": iso_utc(at - timedelta(minutes=1)), "close_time": cutoff, "open": last, "high": last,
                   "low": last, "close": last, "volume": 1.0}]
    return MarketSnapshot(symbol=symbol, as_of=cutoff, data_cutoff=cutoff, data_origin="FIXTURE", candles_15m=closed["15m"],
                          candles_1h=closed["1h"], candles_4h=closed["4h"], candles_1m=one_minute, funding_rate=None,
                          funding_observed_at=None, open_interest_change_1h=None, open_interest_observed_at=None,
                          spread_bps=None).validate()


def multi_timeframe(seed: int, bars_4h: int) -> dict[str, list[dict[str, Any]]]:
    base = ohlc_walk(bars_4h * 16, seconds=900, seed=seed, vol=0.004)
    return {"15m": base, "1h": aggregate(base, 4), "4h": aggregate(base, 16)}


# ------------------------------------------------------------------ harness self-check
class HarnessTests(unittest.TestCase):
    def test_the_harness_catches_a_centred_average_that_reads_the_future(self):
        bars = ohlc_walk(200, seconds=H4, seed=1, vol=0.01)

        def centred(series, t):  # classic leak: a window centred on t uses bars t+1..t+2
            window = series[max(0, t - 2):t + 3]
            return sum(b["close"] for b in window) / len(window)

        def trailing(series, t):
            window = series[max(0, t - 4):t + 1]
            return sum(b["close"] for b in window) / len(window)

        cuts = list(range(10, 190, 9))
        self.assertEqual(lookahead_violations(centred, bars, cuts), cuts)
        self.assertEqual(lookahead_violations(trailing, bars, cuts), [])

    def test_perturbation_changes_every_future_bar_and_nothing_before(self):
        bars = ohlc_walk(50, seconds=H4, seed=2, vol=0.01)
        future = perturb_after(bars, bars[20]["close_time"], 3)
        self.assertEqual(future[:21], bars[:21])
        self.assertTrue(all(a["close"] != b["close"] for a, b in zip(future[21:], bars[21:])))


# ------------------------------------------------------------------ sleeves: look-ahead
class SleevesLookaheadTests(unittest.TestCase):
    def setUp(self):
        self.bars = ohlc_walk(700, seconds=H4, seed=11, vol=0.02, drift=0.001)
        self.cuts = list(range(60, 690, 13))

    def test_wilder_atr_at_t_ignores_every_later_bar(self):
        self.assertEqual(lookahead_violations(lambda s, t: wilder_atr(s)[t], self.bars, self.cuts), [])
        for t in self.cuts:  # the value at t from the full series equals the one from the prefix
            self.assertEqual(wilder_atr(self.bars)[t], wilder_atr(self.bars[:t + 1])[-1])

    def test_donchian_signal_at_t_ignores_every_later_bar(self):
        params = DEFAULT_SLEEVES["donchian"]
        # The engine calls it on the lane cut at the boundary; evaluate the same way on both series.
        fn = lambda s, t: donchian_signal(closed_through(s, s[t]["close_time"]), params)  # noqa: E731
        self.assertEqual(lookahead_violations(fn, self.bars, self.cuts), [])
        fired = [t for t in range(60, 700) if donchian_signal(self.bars[:t + 1], params)]
        self.assertTrue(fired, "the synthetic series must produce breakouts for this guard to mean anything")

    def test_donchian_channel_excludes_the_signal_bar(self):
        # A breakout is judged against the prior n bars (the freqtrade shift(1) rule): making the
        # signal bar's own high/low extreme must not move the channel it is compared with.
        params = DEFAULT_SLEEVES["donchian"]
        n = int(params["n"])
        checked = 0
        for t in range(60, 700):
            signal = donchian_signal(self.bars[:t + 1], params)
            if not signal:
                continue
            bar = dict(self.bars[t])
            bar["high"], bar["low"] = bar["high"] * 1.5, bar["low"] * 0.5
            again = donchian_signal(self.bars[:t] + [bar], params)
            self.assertIsNotNone(again)
            self.assertEqual(again["side"], signal["side"])
            prior = self.bars[t - n:t]
            if signal["side"] == "long":
                self.assertGreater(signal["close"], max(b["high"] for b in prior))
            else:
                self.assertLess(signal["close"], min(b["low"] for b in prior))
            checked += 1
        self.assertGreater(checked, 5)

    def test_trailing_stop_walk_forward_ignores_later_closes(self):
        def path(series, t):
            atr = wilder_atr(series[:61])[-1]
            best = float(series[60]["close"])
            stop = best - 2 * atr
            for bar in series[61:t + 1]:
                best, stop = trail_stop("long", stop, best, float(bar["close"]), atr, 6.0)
            return best, stop

        self.assertEqual(lookahead_violations(path, self.bars, self.cuts[1:]), [])

    def test_daily_close_is_emitted_only_after_its_midnight_bar_has_closed(self):
        close_at = {b["close_time"]: b["close"] for b in self.bars}
        for t in range(0, len(self.bars), 5):
            cut = parse_utc(self.bars[t]["close_time"], "cut")
            closes = daily_closes(self.bars[:t + 1])
            for day, value in closes.items():
                closing = datetime.fromtimestamp((day + 1) * 86400, timezone.utc)  # 00:00 UTC after the day
                self.assertLessEqual(closing, cut)
                self.assertEqual(value, close_at[iso_utc(closing)])
            if cut.hour != 0:  # an intraday cut never yields the still-open day
                self.assertNotIn(int(cut.timestamp()) // 86400, closes)
        self.assertEqual(lookahead_violations(lambda s, t: daily_closes(closed_through(s, s[t]["close_time"])), self.bars,
                                              self.cuts), [])

    def test_engine_day_index_uses_only_closes_through_the_boundary(self):
        # tick(): lane cut at the 4h boundary, day = boundary // 86400 - 1 ("latest fully closed day").
        for t in range(24, len(self.bars)):
            boundary = parse_utc(self.bars[t]["close_time"], "boundary")
            boundary_ts = int(boundary.timestamp())
            closes = daily_closes(closed_through(self.bars, self.bars[t]["close_time"]))
            day = boundary_ts // 86400 - 1
            self.assertEqual(max(closes), day)
            self.assertLessEqual((day + 1) * 86400, boundary_ts)


class MomentumLookaheadTests(unittest.TestCase):
    def setUp(self):
        self.symbols = [f"S{i}USDT" for i in range(7)]
        self.lanes = {s: ohlc_walk(900, seconds=H4, seed=100 + i, vol=0.015, drift=0.0004 * (i - 3))
                      for i, s in enumerate(self.symbols)}
        self.days = sorted(daily_closes(self.lanes[self.symbols[0]]))[70:140:5]

    def _closes(self, lanes):
        return {s: daily_closes(lane) for s, lane in lanes.items()}

    def _future_perturbed(self, day, seed):
        cutoff = iso_utc(datetime.fromtimestamp((day + 1) * 86400, timezone.utc))
        return {s: perturb_after(lane, cutoff, seed + i) for i, (s, lane) in enumerate(self.lanes.items())}

    def test_tsmom_and_xsmom_on_day_d_use_closes_through_d_only(self):
        base = self._closes(self.lanes)
        nonzero = {"tsmom": 0, "xsmom": 0}
        for day in self.days:
            perturbed = self._closes(self._future_perturbed(day, day))
            self.assertNotEqual(perturbed, base)
            truncated = {s: {d: v for d, v in c.items() if d <= day} for s, c in base.items()}
            for name, fn in (("tsmom", tsmom_weights), ("xsmom", xsmom_weights)):
                params = DEFAULT_SLEEVES[name]
                expected = fn(base, day, params, 3.0)
                nonzero[name] += bool(expected)
                self.assertEqual(fn(perturbed, day, params, 3.0), expected, (name, day))
                self.assertEqual(fn(truncated, day, params, 3.0), expected, (name, day))
        self.assertTrue(all(nonzero.values()), nonzero)

    def test_momentum_does_read_day_d_itself(self):
        # Teeth: day d's own close is part of the signal, so the guard above is not vacuous.
        base = self._closes(self.lanes)
        day = self.days[3]
        for name, fn in (("tsmom", tsmom_weights), ("xsmom", xsmom_weights)):
            moved = {s: dict(c) for s, c in base.items()}
            for i, s in enumerate(self.symbols):
                moved[s][day] *= 0.5 if i % 2 else 1.8
            self.assertNotEqual(fn(moved, day, DEFAULT_SLEEVES[name], 3.0), fn(base, day, DEFAULT_SLEEVES[name], 3.0), name)


# ------------------------------------------------------------------ sleeves: warm-up / recursive
class SleevesWarmupTests(unittest.TestCase):
    def test_wilder_atr_converges_well_inside_history_bars(self):
        history = int(DEFAULT_SLEEVES["history_bars"])
        worst = {1e-3: 0, 1e-6: 0, 1e-9: 0}
        for seed, vol in ((21, 0.01), (22, 0.02), (23, 0.035), (24, 0.05)):
            bars = ohlc_walk(3 * history, seconds=H4, seed=seed, vol=vol)
            need = warmup_lengths(lambda s: wilder_atr(s)[-1], bars, tuple(worst), history)
            worst = {tol: max(worst[tol], need[tol]) for tol in worst}
        self.assertLessEqual(worst[1e-9], history, worst)
        # The analytic decay of the seed error agrees: (13/14)^(history-1) is negligible.
        self.assertLess((13 / 14) ** (history - 1), 1e-12)

    def test_atr_at_t_does_not_depend_on_where_the_fetched_window_starts(self):
        # tick() re-fetches the last history_bars bars each boundary, so the window start slides.
        history = int(DEFAULT_SLEEVES["history_bars"])
        bars = ohlc_walk(3 * history, seconds=H4, seed=31, vol=0.03)
        for t in range(2 * history, 3 * history, 37):
            ref = wilder_atr(bars[:t + 1])[-1]
            for extra in (0, 1, 6, 50):
                value = wilder_atr(bars[t + 1 - history - extra:t + 1])[-1]
                self.assertAlmostEqual(value, ref, delta=ref * 1e-9)

    def test_default_history_covers_the_momentum_lookbacks(self):
        # history_bars 4h bars → (history_bars * 4 / 24) daily closes; look_days and vol_days need
        # the close from look_days ago plus the day itself.
        history_days = int(DEFAULT_SLEEVES["history_bars"]) * H4 // 86400
        for name in ("tsmom", "xsmom"):
            params = DEFAULT_SLEEVES[name]
            self.assertGreaterEqual(history_days, int(params["look_days"]) + 1, name)
            self.assertGreaterEqual(history_days, int(params["vol_days"]) + 1, name)
        lane = ohlc_walk(3000, seconds=H4, seed=41, vol=0.02)
        for t in range(800, 3000, 97):  # the fetched window always carries day and day - look_days
            window = lane[t + 1 - int(DEFAULT_SLEEVES["history_bars"]):t + 1]
            closes = daily_closes(window)
            day = int(parse_utc(window[-1]["close_time"], "b").timestamp()) // 86400 - 1
            for name in ("tsmom", "xsmom"):
                self.assertIn(day - int(DEFAULT_SLEEVES[name]["look_days"]), closes)
                self.assertIn(day, closes)


# ------------------------------------------------------------------ runtime features: look-ahead
class FeatureLookaheadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lanes = multi_timeframe(seed=51, bars_4h=420)
        base = cls.lanes["15m"]
        # Cuts on and off the 1h/4h boundaries, so in-progress higher-timeframe bars exist.
        cls.cuts = [i for i in range(len(base) - 16 * 12, len(base) - 20, 37)] + [16 * 360 - 1, 16 * 380 + 5, 16 * 400 + 3]

    def _features(self, lanes, cutoff, history):
        return compute_features(snapshot_at(lanes, cutoff), historical_bars=history)

    def test_features_ignore_archived_bars_after_the_snapshot_cutoff(self):
        gates = set()
        for t in self.cuts:
            cutoff = self.lanes["15m"][t]["close_time"]
            prefix = {i: closed_through(lane, cutoff) for i, lane in self.lanes.items()}
            future = {i: perturb_after(lane, cutoff, t + k) for k, (i, lane) in enumerate(self.lanes.items())}
            self.assertTrue(any(parse_utc(b["close_time"], "b") > parse_utc(cutoff, "c") for b in future["4h"]))
            expected = self._features(self.lanes, cutoff, prefix)
            leaked = self._features(future, cutoff, future)
            self.assertEqual(leaked, expected, t)
            gates.add((expected["quant_regime"], expected["quant_direction"]))
        self.assertGreater(len(gates), 1, "cuts should cover more than one regime")

    def test_breakout_and_volume_windows_exclude_the_signal_bar(self):
        for t in self.cuts[:8]:
            cutoff = self.lanes["15m"][t]["close_time"]
            prefix = {i: closed_through(lane, cutoff) for i, lane in self.lanes.items()}
            features = self._features(self.lanes, cutoff, prefix)
            prior = prefix["15m"][-21:-1]
            self.assertEqual(features["previous_20_bar_high"], max(b["high"] for b in prior))
            self.assertEqual(features["previous_20_bar_low"], min(b["low"] for b in prior))
            self.assertEqual(features["last_bar_close_time"], cutoff)

    def test_snapshot_with_a_bar_closing_after_its_cutoff_is_rejected(self):
        cutoff = self.lanes["15m"][16 * 400 + 3]["close_time"]
        good = snapshot_at(self.lanes, cutoff)
        in_progress = [b for b in self.lanes["4h"] if b["open_time"] == good.candles_4h[-1]["close_time"]][0]
        bad = MarketSnapshot(**{**good.__dict__, "candles_4h": good.candles_4h[1:] + [in_progress]})
        with self.assertRaises(MarketDataError):
            compute_features(bad)


# ------------------------------------------------------------------ runtime features: warm-up / recursive
class FeatureWarmupTests(unittest.TestCase):
    def test_ema_warmup_fits_inside_feature_history_bars(self):
        vols = {"15m": 0.004, "1h": 0.008, "4h": 0.016}
        for interval, window in FEATURE_HISTORY_BARS.items():
            worst = {1e-6: 0, 1e-9: 0}
            for seed in range(3):
                closes = [b["close"] for b in ohlc_walk(3 * window, seconds=SECONDS[interval], seed=60 + seed, vol=vols[interval])]
                for period in (12, 26):
                    need = warmup_lengths(lambda v: _ema(v, period), closes, tuple(worst), window)
                    worst = {tol: max(worst[tol], need[tol]) for tol in worst}
            self.assertLessEqual(worst[1e-9], window, (interval, worst))

    def test_bounded_history_matches_deep_history_at_every_cut(self):
        lanes = multi_timeframe(seed=71, bars_4h=2 * FEATURE_HISTORY_BARS["4h"] + 40)
        base = lanes["15m"]
        for t in range(len(base) - 16 * 10, len(base), 29):
            cutoff = base[t]["close_time"]
            deep = {i: closed_through(lane, cutoff) for i, lane in lanes.items()}
            bounded = {i: lane[-FEATURE_HISTORY_BARS[i]:] for i, lane in deep.items()}
            a = compute_features(snapshot_at(lanes, cutoff), historical_bars=deep)
            b = compute_features(snapshot_at(lanes, cutoff), historical_bars=bounded)
            for key in ("ema12", "ema26", "ema12_1h", "ema26_1h", "ema12_4h", "ema26_4h", "atr", "rsi14", "signal_strength"):
                self.assertAlmostEqual(a[key], b[key], delta=abs(a[key]) * 1e-9 + 1e-12, msg=(t, key))
            for key in ("quant_direction", "quant_regime", "signal_trigger", "gate_eligible",
                        "previous_20_bar_high", "previous_20_bar_low", "volume_zscore"):
                self.assertEqual(a[key], b[key], (t, key))


# ------------------------------------------------------------------ sleeves engine end to end
class _Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class _Provider:
    """4h history provider. With ``leak_bars`` it misbehaves: it also returns the in-progress bar
    and later bars (violently perturbed), so the engine's own boundary cut is what is tested."""

    provider_id = "synthetic-lookahead"
    data_origin = "FIXTURE"

    def __init__(self, lanes, leak_bars=0):
        self.lanes = lanes
        self.leak_bars = leak_bars
        self.close_ts = {s: [int(parse_utc(b["close_time"], "b").timestamp()) for b in lane] for s, lane in lanes.items()}

    def validate_symbols(self, symbols):
        return {"supported": list(symbols), "excluded": []}

    def fetch_snapshot(self, symbol, as_of):
        raise MarketDataError("no snapshot")

    def fetch_history(self, symbol, interval, *, bars, as_of):
        i = bisect.bisect_right(self.close_ts[symbol], int(as_of.timestamp()))
        honest = self.lanes[symbol][max(0, i - bars):i]
        if not self.leak_bars:
            return honest
        future = perturb_after(self.lanes[symbol], honest[-1]["close_time"], i)[i:i + self.leak_bars]
        return honest + future

    def fetch_monitor_bars(self, symbol, after, as_of):
        return []

    def fetch_funding_rate(self, symbol, as_of):
        return 0.0001


class SleevesEngineLookaheadTests(unittest.TestCase):
    def _run(self, provider, boundaries):
        clock = _Clock(boundaries[0])
        store = PaperStore(Path(tempfile.mkdtemp()) / "l.sqlite3")
        self.addCleanup(store.close)
        runtime = PaperRuntime(store, clock=clock, market_provider=provider)
        config = dict(store.experiment()["config"])
        config.update({"strategy_engine": "sleeves_v1", "starting_balance_usdt": 300.0, "evaluation_arms": ["quant"],
                       "primary_arm": "quant", "jev_enabled": False, "gpt_escalation_enabled": False,
                       "sleeves": {"universe": list(provider.lanes), "xsmom": {"k": 1}}})
        store.save_experiment(config)
        runtime.start()
        for boundary in boundaries:
            clock.value = boundary + timedelta(seconds=61)
            runtime.sleeves.tick(clock.value)
        decisions = store._query("SELECT boundary, sleeve, symbol, action, detail_json FROM sleeve_decisions "
                                 "ORDER BY boundary, sleeve, symbol, action")
        ticks = store._query("SELECT boundary, status, summary_json FROM sleeve_ticks ORDER BY boundary")
        positions = sorted((p["cohort"], p["symbol"], p["side"], p["quantity"], p["entry_price"], p["stop_price"])
                           for p in store.open_positions(store.experiment()["experiment_id"]))
        return [tuple(r) for r in decisions], [tuple(r) for r in ticks], positions

    def test_tick_decisions_are_identical_when_the_provider_leaks_future_bars(self):
        history = int(DEFAULT_SLEEVES["history_bars"])
        first = START + timedelta(hours=4 * history)
        lanes = {f"{c}USDT": ohlc_walk(history + 60, seconds=H4, seed=80 + i, vol=0.012, drift=d)
                 for i, (c, d) in enumerate((("A", 0.004), ("B", -0.004), ("C", 0.0), ("D", 0.0015)))}
        boundaries = [first + timedelta(hours=4 * k) for k in range(0, 42)]
        honest = self._run(_Provider(lanes), boundaries)
        leaky_provider = _Provider(lanes, leak_bars=6)
        leaked = self._run(leaky_provider, boundaries)
        self.assertTrue(honest[0], "the guard needs sleeve decisions to compare")
        self.assertTrue({r[1] for r in honest[0]} >= {"donchian", "tsmom", "xsmom"}, honest[0])
        self.assertEqual(leaked, honest)
        # Teeth: the leaky provider really does hand the engine different, later bars.
        raw = leaky_provider.fetch_history("AUSDT", "4h", bars=history, as_of=boundaries[5] + timedelta(seconds=61))
        self.assertGreater(parse_utc(raw[-1]["close_time"], "b"), boundaries[5])
        self.assertNotEqual(raw[-1]["close"], lanes["AUSDT"][history + 5]["close"])


if __name__ == "__main__":
    unittest.main()
