"""Spot AI Co-Trader: signal math, ladder, CDC, backtests, AI triggers/caching, journal, endpoints.

Offline and fake-only: synthetic candles, fixture Jev/GPT adapters, faked HTTP transports. No network
and no paid calls.
"""

from __future__ import annotations

import importlib.util
import json
import math
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from crypto_eval.market_catalog import GateSpotMarketDataProvider
from crypto_eval.paper_ai import (
    SPOT_REVIEW_RESPONSE_SCHEMA,
    AIProviderError,
    FixtureGPTProvider,
    FixtureJevProvider,
    JevAdapter,
    ResponsesAdapter,
    build_spot_jev_questions,
    jev_score_unit,
    parse_jev_response,
    parse_spot_review,
    spot_briefing_response_schema,
)
from crypto_eval.paper_contracts import PaperTradingError, default_experiment_config
from crypto_eval.paper_runtime import PaperRuntime, PaperScheduler, PaperStore
from crypto_eval.paper_server import PaperHTTPServer, PaperRequestHandler
from crypto_eval.secret_store import CredentialResolver, MemorySecretStore
from crypto_eval.spot_cotrader import (
    DAY,
    LEDGER_SCOPE,
    SWITCH_COST,
    CoTraderConfirmationRequired,
    CoTraderScheduler,
    GateSpotDailySource,
    SpotCoTrader,
    backtest_coin,
    ladder_action,
    ladder_states,
    ladder_transitions,
    ladder_weights,
    market_regime,
    rule_action,
    rule_backtest,
    scorecard_rows,
    signal_rows,
    sizing_weights,
    state_events,
)

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("validate_repo", ROOT / "scripts" / "validate_repo.py")
validate_repo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate_repo)
SCHEMA_PATH = ROOT / "schemas" / "spot-cotrader.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text())

NOW = datetime(2026, 9, 29, 13, 30, tzinfo=timezone.utc)
BOUNDARY = int(NOW.timestamp()) - int(NOW.timestamp()) % DAY
SENTINEL = "UNIT_COTRADER_SECRET_SENTINEL_NOT_A_REAL_KEY"
UNIVERSE = ["BTC", "ETH", "NEAR", "SEI", "SUI", "AVAX", "ENA"]


def candles_from(closes: list[float], *, end_close_ts: int = BOUNDARY) -> list[dict]:
    """Daily candles whose last bar closes at ``end_close_ts``; open = previous close."""

    start = end_close_ts - len(closes) * DAY
    out = []
    for i, close in enumerate(closes):
        open_ = closes[i - 1] if i else close
        out.append({"t": start + i * DAY, "o": open_, "h": max(open_, close) * 1.01, "l": min(open_, close) * 0.99,
                    "c": close, "v": 1000.0 + i})
    return out


def wave(n: int, phase: float = 0.0) -> list[float]:
    return [100 * (1 + 0.3 * math.sin(i / 30 + phase)) * (1 + 0.001 * i) for i in range(n)]


def conforms(value, schema_ref: str | None = None) -> list[str]:
    schema = {"$ref": schema_ref} if schema_ref else SCHEMA
    errors: list[str] = []
    validate_repo.validate_schema(json.loads(json.dumps(value)), schema, SCHEMA_PATH, SCHEMA, "value", errors)
    return errors


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeSource:
    """Synthetic daily candles and tickers; ``leak`` also returns the still-open current bar."""

    data_origin = "FIXTURE"

    def __init__(self, series: dict[str, list[dict]], *, leak: bool = False) -> None:
        self.series = series
        self.leak = leak
        self.calls: list[tuple[str, int]] = []

    def daily_candles(self, pair, *, start_ts, now):
        self.calls.append((pair, start_ts))
        base = pair.split("_")[0]
        cutoff = now.timestamp()
        return [c for c in self.series.get(base, []) if c["t"] >= start_ts and (self.leak or c["t"] + DAY <= cutoff)]

    def tickers(self):
        return {f"{base}_USDT": {"price": rows[-1]["c"] * 1.01, "change_24h": 0.01} for base, rows in self.series.items()}


class CountingGPT:
    def __init__(self) -> None:
        self.fixture = FixtureGPTProvider()
        self.reviews: list[dict] = []
        self.briefings: list[dict] = []

    def generate_spot_review(self, context):
        self.reviews.append(context)
        return self.fixture.generate_spot_review(context)

    def generate_spot_briefing(self, context):
        self.briefings.append(context)
        return self.fixture.generate_spot_briefing(context)


class CountingJev:
    def __init__(self, force: str | None = None) -> None:
        self.fixture = FixtureJevProvider()
        self.states: list[dict] = []
        self.force = force

    def evaluate_spot(self, state):
        self.states.append(state)
        vector = self.fixture.evaluate_spot(state)
        if self.force:
            vector["answers"]["rule_agreement"]["value"] = self.force
        return vector


def default_series(n: int = 500) -> dict[str, list[dict]]:
    return {base: candles_from(wave(n, phase=k * 0.7)) for k, base in enumerate(UNIVERSE)}


class CoTraderCase(unittest.TestCase):
    def make(self, series=None, *, gpt=None, jev=None, clock=None, store=None, **kwargs):
        self.store = store or PaperStore(":memory:")
        self.addCleanup(self.store.close)
        self.clock = clock or FixedClock(NOW)
        self.gpt = gpt or CountingGPT()
        self.jev = jev or CountingJev()
        self.runtime = PaperRuntime(self.store, gpt_provider=self.gpt, jev_provider=self.jev, clock=self.clock)
        self.source = FakeSource(series or default_series())
        kwargs.setdefault("allow_fixture_ai", True)
        self.service = SpotCoTrader(self.runtime, source=self.source, **kwargs)
        return self.service


# ---------------------------------------------------------------------------
# signal math
# ---------------------------------------------------------------------------


class SignalMathTests(unittest.TestCase):
    def test_signal_values_match_the_definitions(self):
        closes = [100 + i + 5 * math.sin(i) for i in range(130)]
        rows = signal_rows(candles_from(closes))
        i = 120
        row = rows[i]
        self.assertAlmostEqual(row["ret_60d"], closes[i] / closes[i - 60] - 1)
        sma = sum(closes[i - 99 : i + 1]) / 100
        self.assertAlmostEqual(row["sma100"], sma)
        self.assertAlmostEqual(row["dist_sma100"], closes[i] / sma - 1)
        rets = [closes[j] / closes[j - 1] - 1 for j in range(i - 19, i + 1)]
        mean = sum(rets) / 20
        self.assertAlmostEqual(row["vol_20d"], math.sqrt(sum((r - mean) ** 2 for r in rets) / 19))
        self.assertAlmostEqual(row["trend_line_next"], max(closes[i - 59], sum(closes[i - 98 : i + 1]) / 99))
        self.assertEqual(row["close_ts"], row["t"] + DAY)
        # incomplete windows are None, never zero-filled
        self.assertIsNone(rows[50]["sma100"])
        self.assertIsNone(rows[50]["state"])
        self.assertIsNone(rows[10]["vol_20d"])

    def test_states_and_events_hold_watch_cash(self):
        closes = [100.0] * 100 + [100 + i for i in range(1, 61)] + [160 - 3 * i for i in range(1, 41)]
        rows = signal_rows(candles_from(closes))
        states = [row["state"] for row in rows]
        self.assertEqual(states[159], "HOLD")
        self.assertIn("WATCH", states[160:])
        self.assertEqual(states[-1], "CASH")
        events = state_events(rows)
        self.assertEqual([(e["from"], e["to"]) for e in events][-2:], [("HOLD", "WATCH"), ("WATCH", "CASH")])
        for event in events:
            self.assertTrue(event["at"].endswith("T00:00:00Z"))

    def test_trend_line_next_encodes_tomorrows_rule_exactly(self):
        for closes in (wave(300), [100 + i * 0.5 for i in range(200)], [200 - i * 0.3 for i in range(200)]):
            candles = candles_from(closes)
            level = signal_rows(candles)[-1]["trend_line_next"]
            for factor, expect_hold in ((1 + 1e-6, True), (1 - 1e-6, False), (1.0, False)):
                nxt = dict(candles[-1])
                nxt.update({"t": candles[-1]["t"] + DAY, "c": level * factor, "o": candles[-1]["c"]})
                nxt["h"], nxt["l"] = max(nxt["o"], nxt["c"]) * 1.01, min(nxt["o"], nxt["c"]) * 0.99
                state = signal_rows(candles + [nxt])[-1]["state"]
                self.assertEqual(state == "HOLD", expect_hold, (factor, state))

    def test_future_bars_never_change_past_signals(self):
        candles = candles_from(wave(400))
        full = signal_rows(candles)
        for k in (150, 260, 399):
            partial = signal_rows(candles[:k])
            keys = ("ret_60d", "sma100", "vol_20d", "state", "trend_line_next", "ladder", "cdc", "ema20", "add_above")
            self.assertEqual({key: partial[-1][key] for key in keys}, {key: full[k - 1][key] for key in keys})

    def test_data_gap_nulls_the_windows_that_cross_it(self):
        candles = candles_from(wave(200))
        gapped = candles[:150] + candles[151:]
        rows = signal_rows(gapped)
        self.assertIsNone(rows[160]["sma100"])
        self.assertIsNone(rows[160]["state"])

    def test_regime_labels_and_breadth(self):
        def states(n_hold, total):
            return {f"C{i}": ("HOLD" if i < n_hold else "CASH") for i in range(total)}

        self.assertEqual(market_regime(states(4, 7), "HOLD")["label"], "MIXED")
        self.assertAlmostEqual(market_regime(states(4, 7), "HOLD")["breadth"], 0.5714)
        self.assertEqual(market_regime(states(3, 5), "HOLD")["label"], "RISK_ON")  # 0.6
        self.assertEqual(market_regime(states(1, 5), None)["label"], "RISK_OFF")  # 0.2
        self.assertEqual(market_regime(states(5, 7), None)["label"], "RISK_ON")
        with_unknown = {**states(1, 2), "X": None}
        self.assertEqual(market_regime(with_unknown, None)["total"], 2)
        self.assertIsNone(market_regime({"X": None}, None)["label"])


class BacktestTests(unittest.TestCase):
    @staticmethod
    def rows(closes, states):
        start = BOUNDARY - len(closes) * DAY
        return [{"t": start + i * DAY, "close_ts": start + (i + 1) * DAY, "close": c, "state": s}
                for i, (c, s) in enumerate(zip(closes, states))]

    def test_rule_backtest_fees_equity_and_trades(self):
        closes = [100.0, 110.0, 121.0] + [110.0] * 58
        states = ["HOLD", "HOLD", "CASH"] + ["CASH"] * 58
        result = rule_backtest(self.rows(closes, states))
        c = SWITCH_COST
        self.assertAlmostEqual(SWITCH_COST, 0.0015)
        end = (1 - c) ** 2 * 1.21
        stats = result["stats"]
        self.assertAlmostEqual(stats["cagr"], round(end ** (365 / 60) - 1, 6))
        self.assertEqual(stats["switches"], 2)
        self.assertAlmostEqual(stats["time_in_market"], round(2 / 60, 6))
        self.assertAlmostEqual(stats["bh_max_dd"], round(1 - 110 / 121, 6))
        self.assertAlmostEqual(stats["bh_cagr"], round(((1 - c) * 1.1) ** (365 / 60) - 1, 6))
        self.assertEqual(result["equity"]["rule"][-1][1], round(end, 6))
        free = rule_backtest(self.rows(closes, states), cost=0.0)
        self.assertAlmostEqual(free["equity"]["rule"][-1][1], 1.21)
        trade = result["trades"][0]
        self.assertEqual((trade["entry_price"], trade["exit_price"], trade["days"]), (100.0, 121.0, 2))
        self.assertAlmostEqual(trade["return_pct"], round(1.21 * (1 - c) ** 2 - 1, 6))

    def test_open_trade_and_short_history(self):
        result = rule_backtest(self.rows([100.0, 110.0, 99.0], ["CASH", "HOLD", "HOLD"]))
        self.assertIsNone(result["stats"])
        self.assertEqual(result["reason"], "insufficient_history")
        self.assertEqual(result["trades"][0]["exit_at"], None)
        self.assertIsNone(result["trades"][0]["return_pct"])
        self.assertEqual(result["trades"][0]["days"], 1)

    def test_backtest_coin_tiny_series_and_insufficient_history(self):
        bars = [[BOUNDARY - (5 - i) * DAY, c, c * 1.01, c * 0.99, c, 1.0] for i, c in enumerate([100, 110, 99, 108.9, 119.79])]
        short = backtest_coin(bars, "buy_hold")
        self.assertTrue(short["insufficient_history"])
        self.assertNotIn("sharpe", short)
        tiny = backtest_coin(bars, "buy_hold", min_bars=5)
        fee = 0.0025
        self.assertAlmostEqual(tiny["total_return_pct"], round((1.1 - fee) * 0.9 * 1.1 * 1.1 - 1, 6))
        self.assertAlmostEqual(tiny["max_dd_pct"], 0.1)
        self.assertEqual((tiny["trades"], tiny["win_rate"], tiny["time_in_market_pct"]), (1, 1.0, 1.0))
        idle = backtest_coin(bars, "rule", min_bars=5)  # no rule state yet: never in the market
        self.assertEqual((idle["trades"], idle["win_rate"], idle["total_return_pct"], idle["sharpe"]), (0, None, 0.0, None))
        with self.assertRaises(PaperTradingError):
            backtest_coin(bars, "martingale")

    def test_backtest_coin_rule_on_a_steady_uptrend(self):
        g = 0.01
        closes = [100 * (1 + g) ** i for i in range(400)]
        bars = [[BOUNDARY - (400 - i) * DAY, c, c, c, c, 1.0] for i, c in enumerate(closes)]
        rule = backtest_coin(bars, "rule")
        self.assertFalse(rule["insufficient_history"])
        self.assertEqual(rule["trades"], 1)
        self.assertAlmostEqual(rule["time_in_market_pct"], round(300 / 399, 6))
        self.assertAlmostEqual(rule["total_return_pct"], round((1 + g - 0.0025) * (1 + g) ** 299 - 1, 4), places=3)
        self.assertEqual(rule["max_dd_pct"], 0.0)


# ---------------------------------------------------------------------------
# ladder, CDC, actions, sizing
# ---------------------------------------------------------------------------


class LadderAndCdcTests(unittest.TestCase):
    def test_ladder_state_transitions(self):
        def row(state, hi20, close, ema):
            return {"state": state, "hi20": hi20, "c": close, "ema20": ema}

        rows = [
            row(None, None, 1, 1),        # warm-up
            row("CASH", False, 1, 1),     # OUT
            row("HOLD", False, 10, 9),    # STARTER (rule on, no breakout)
            row("HOLD", True, 11, 9),     # FULL (20-day closing high)
            row("HOLD", False, 10, 9.5),  # stays FULL (sticky)
            row("HOLD", False, 9, 9.5),   # below EMA20 -> STARTER
            row("HOLD", True, 12, 13),    # breakout but below EMA20 -> STARTER
            row("HOLD", True, 14, 13),    # FULL
            row("WATCH", True, 15, 13),   # rule off -> OUT
            row("HOLD", False, 15, 13),   # back on: STARTER (FULL flag reset)
        ]
        self.assertEqual(ladder_states(rows),
                         [None, "OUT", "STARTER", "FULL", "FULL", "STARTER", "STARTER", "FULL", "OUT", "STARTER"])
        self.assertEqual(ladder_action("OUT", "STARTER"), "BUY_STARTER")
        self.assertEqual(ladder_action("STARTER", "FULL"), "ADD")
        self.assertEqual(ladder_action("OUT", "FULL"), "ADD")
        self.assertEqual(ladder_action("FULL", "STARTER"), "TRIM")
        self.assertEqual(ladder_action("FULL", "OUT"), "SELL_ALL")
        self.assertEqual(ladder_action("STARTER", "STARTER"), "HOLD")

    def test_ladder_transitions_carry_marker_codes(self):
        rows = signal_rows(candles_from(wave(500)))
        transitions = ladder_transitions(rows)
        self.assertTrue(transitions)
        for item in transitions:
            self.assertEqual(item["code"], {"BUY_STARTER": "B", "ADD": "A", "TRIM": "T", "SELL_ALL": "S"}[item["action"]])
            self.assertNotEqual(item["from"], item["to"])

    def test_add_above_and_trim_below_are_exact_next_close_triggers(self):
        candles = candles_from(wave(300))
        last = signal_rows(candles)[-1]
        for level, key, above in ((last["add_above"], "hi20", True), (last["ema20"], "below_ema", False)):
            for factor in (1 + 1e-7, 1 - 1e-7):
                nxt = {**candles[-1], "t": candles[-1]["t"] + DAY, "c": level * factor}
                row = signal_rows(candles + [nxt])[-1]
                if key == "hi20":
                    self.assertEqual(row["hi20"], factor > 1)
                else:
                    self.assertEqual(row["c"] < row["ema20"], factor < 1)

    def test_ladder_weights_scale_to_capital(self):
        universe = UNIVERSE
        four_full = {base: ("FULL" if i < 4 else "OUT") for i, base in enumerate(universe)}
        weights = ladder_weights(four_full, universe)
        self.assertAlmostEqual(sum(weights.values()), 1.0)  # 4 x 2/7 = 8/7 -> scaled to 100%
        self.assertAlmostEqual(weights["BTC"], 0.25)
        mixed = {base: ("STARTER" if i < 3 else "OUT") for i, base in enumerate(universe)}
        self.assertAlmostEqual(ladder_weights(mixed, universe)["BTC"], 0.5 / 7)
        self.assertIsNone(ladder_weights({"BTC": None}, universe)["BTC"])

    def test_cdc_zone_colours_on_a_known_series(self):
        closes = [100 + i for i in range(60)] + [160 - 2 * i for i in range(1, 41)]
        rows = signal_rows(candles_from(closes))
        zones = [row["cdc"] for row in rows]
        self.assertTrue(all(zone is None for zone in zones[:26]))
        self.assertEqual(zones[59], "green")
        self.assertEqual(zones[-1], "red")
        first_red = zones.index("red")
        self.assertIn("blue", zones[59:first_red])  # the pullback inside a bull stays blue before turning red
        # independent recomputation of the definition
        bars = candles_from(closes)

        def ema(values, n):
            a, out, e = 2 / (n + 1), [], None
            for v in values:
                e = v if e is None else a * v + (1 - a) * e
                out.append(e)
            return out

        ap = ema([(b["o"] + b["h"] + b["l"] + b["c"]) / 4 for b in bars], 2)
        fast, slow = ema(ap, 12), ema(ap, 26)
        for i in range(26, len(bars)):
            bull, above = fast[i] > slow[i], ap[i] > fast[i]
            expected = ("green" if above else "blue") if bull else ("yellow" if above else "red")
            self.assertEqual(zones[i], expected)

    def test_rule_action_table(self):
        cases = [("HOLD", False, "BUY"), ("HOLD", True, "HOLD"), ("HOLD", None, "IN"), ("CASH", True, "SELL"),
                 ("WATCH", False, "WAIT"), ("CASH", None, "OUT")]
        for state, held, expected in cases:
            action = rule_action(state, held, 84000.0, 0.02, True)
            self.assertEqual(action["type"], expected)
            self.assertEqual(action["trigger"]["kind"], "close_below" if state == "HOLD" else "close_above")
            self.assertEqual(action["trigger"]["price"], 84000.0)
            self.assertTrue(action["fresh"])
        self.assertIn("84,000.0", rule_action("HOLD", True, 84000.0, 0.02, False)["text"])
        self.assertIsNone(rule_action(None, True, None, None, False))

    def test_sizing_weights(self):
        self.assertAlmostEqual(sizing_weights("equal_weight", {}, UNIVERSE)["NEAR"], 1 / 7)
        weights = sizing_weights("inverse_vol", {"BTC": 0.02, "ETH": 0.04, "NEAR": None}, ["BTC", "ETH", "NEAR"])
        self.assertAlmostEqual(weights["BTC"], 2 / 3)
        self.assertAlmostEqual(weights["ETH"], 1 / 3)
        self.assertIsNone(weights["NEAR"])


# ---------------------------------------------------------------------------
# market data (closed bars only; public GET)
# ---------------------------------------------------------------------------


class MarketDataTests(CoTraderCase):
    def test_gate_daily_source_drops_open_bars_and_uses_get_only(self):
        requests = []
        now = NOW

        def transport(url, timeout):
            requests.append(url)
            closed = [[str(BOUNDARY - 2 * DAY), "1", "101", "102", "99", "100", "10", "true"],
                      [str(BOUNDARY - DAY), "1", "103", "104", "100", "101", "10", "true"]]
            still_open = [str(BOUNDARY), "1", "105", "106", "102", "103", "10", "false"]
            mislabeled = [str(BOUNDARY), "1", "105", "106", "102", "103", "10", "true"]  # closes in the future
            return json.dumps(closed + [still_open, mislabeled]).encode()

        source = GateSpotDailySource(GateSpotMarketDataProvider(transport=transport))
        candles = source.daily_candles("BTC_USDT", start_ts=BOUNDARY - 5 * DAY, now=now)
        self.assertEqual([c["t"] for c in candles], [BOUNDARY - 2 * DAY, BOUNDARY - DAY])
        self.assertEqual(candles[-1]["c"], 103.0)
        self.assertTrue(all("/spot/candlesticks" in url and "interval=1d" in url for url in requests))

        def misaligned(url, timeout):
            return json.dumps([[str(BOUNDARY - DAY + 3600), "1", "1", "1", "1", "1", "1", "true"]]).encode()

        with self.assertRaises(Exception):
            GateSpotDailySource(GateSpotMarketDataProvider(transport=misaligned)).daily_candles(
                "BTC_USDT", start_ts=BOUNDARY - 5 * DAY, now=now)

    def test_refresh_never_stores_an_open_bar_and_refetches_only_recent_days(self):
        series = default_series(450)
        for base in series:  # the bar opening at the latest close is still forming
            last = series[base][-1]
            series[base].append({**last, "t": BOUNDARY, "c": last["c"] * 2})
        service = self.make(series)
        self.source.leak = True
        result = service.refresh()
        self.assertTrue(result["complete"])
        latest = self.store._query("SELECT MAX(t) AS t FROM cotrader_candles WHERE base='BTC'")[0]["t"]
        self.assertEqual(latest + DAY, BOUNDARY)
        rows, _ = service.signals("BTC")
        self.assertLessEqual(rows[-1]["close_ts"], int(NOW.timestamp()))
        self.source.calls.clear()
        service.refresh()
        self.assertTrue(all(start >= BOUNDARY - 4 * DAY for _, start in self.source.calls))

    def test_missing_history_is_null_with_a_reason(self):
        series = default_series(450)
        series["ENA"] = series["ENA"][-50:]
        service = self.make(series)
        service.refresh()
        coin = next(c for c in service.overview()["coins"] if c["base"] == "ENA")
        self.assertIsNone(coin["state"])
        self.assertIsNone(coin["action"])
        self.assertIsNone(coin["sizing"])
        self.assertIn("100", coin["unavailable_reason"])


# ---------------------------------------------------------------------------
# events, AI triggers, caching, budget
# ---------------------------------------------------------------------------


def changing_series() -> dict[str, list[dict]]:
    """BTC turns from HOLD to WATCH/CASH exactly at today's close; the others are steady."""

    series = default_series(450)
    closes = [100.0] * 300 + [100 + i for i in range(1, 150)] + [60.0]
    series["BTC"] = candles_from(closes)
    return series


class TriggerTests(CoTraderCase):
    def test_events_are_idempotent_and_state_change_review_is_not_rebilled(self):
        service = self.make(changing_series())
        service.refresh()
        first = service.process_close()
        self.assertIn("BTC", first["changed_today"])
        self.assertEqual(len(self.gpt.reviews), 1)
        self.assertGreater(first["recorded_events"], 0)
        count = self.store._query("SELECT COUNT(*) AS n FROM cotrader_events")[0]["n"]
        again = service.process_close()
        self.assertEqual(again["recorded_events"], 0)
        self.assertEqual(self.store._query("SELECT COUNT(*) AS n FROM cotrader_events")[0]["n"], count)
        self.assertEqual(len(self.gpt.reviews), 1)
        restarted = SpotCoTrader(self.runtime, source=self.source, allow_fixture_ai=True)
        restarted.process_close()
        self.assertEqual(len(self.gpt.reviews), 1)
        coin = next(c for c in service.overview()["coins"] if c["base"] == "BTC")
        self.assertTrue(coin["changed_today"])
        self.assertEqual(coin["event"]["at"], "2026-09-29T00:00:00Z")
        self.assertEqual(coin["last_ai"]["trigger"], "state_change")
        self.assertTrue(coin["action"]["fresh"])
        context = self.gpt.reviews[0]
        self.assertEqual(len(context["candles"]), 120)
        self.assertLessEqual(context["candles"][-1][0] + DAY, int(NOW.timestamp()))
        self.assertNotIn("price", context)

    def test_same_coin_state_and_day_is_cached_across_triggers(self):
        service = self.make(changing_series())
        service.refresh()
        manual = service.review("BTC", trigger="manual")
        self.assertIsNotNone(manual["analysis"])
        self.assertEqual(len(self.gpt.reviews), 1)
        service.process_close()  # the state-change review reuses today's review of the same state
        self.assertEqual(len(self.gpt.reviews), 1)
        cached = service.review("BTC", trigger="jev_disagree")
        self.assertTrue(cached["cached"])
        self.assertEqual(len(self.gpt.reviews), 1)

    def test_manual_cap_requires_confirmation(self):
        service = self.make()
        service.refresh()
        for _ in range(3):
            self.assertIsNotNone(service.review("ETH", trigger="manual")["analysis"])
        with self.assertRaises(CoTraderConfirmationRequired) as caught:
            service.review("ETH", trigger="manual")
        self.assertEqual(caught.exception.reason, "manual_cap")
        self.assertEqual(len(self.gpt.reviews), 3)
        self.assertIsNotNone(service.review("ETH", trigger="manual", confirm=True)["analysis"])
        self.assertEqual(len(self.gpt.reviews), 4)
        self.assertIsNotNone(service.review("NEAR", trigger="manual")["analysis"])  # the cap is per coin
        self.clock.now = NOW + timedelta(days=1)
        self.assertIsNotNone(service.review("ETH", trigger="manual")["analysis"])  # and per UTC day

    def test_jev_once_per_coin_per_close_and_disagree_escalates_once(self):
        service = self.make(jev=CountingJev(force="disagree"))
        service.refresh()
        result = service.jev_daily()
        self.assertEqual(len(self.jev.states), 7)
        self.assertEqual(len(self.gpt.reviews), 7)  # one Luna review per disagreeing coin
        self.assertTrue(all(result["coins"][base] == "ok" for base in UNIVERSE))
        service.jev_daily()
        SpotCoTrader(self.runtime, source=self.source, allow_fixture_ai=True).jev_daily()
        self.assertEqual(len(self.jev.states), 7)
        self.assertEqual(len(self.gpt.reviews), 7)
        again = service.review("SEI", trigger="jev_disagree")
        self.assertTrue(again["cached"])
        self.assertEqual(len(self.gpt.reviews), 7)
        state = self.jev.states[0]
        self.assertEqual(len(state["daily_close_volume"]), 60)
        self.assertLess(len(json.dumps(state)) / 3, 2000)  # well under ~2k tokens
        coin = next(c for c in service.overview()["coins"] if c["base"] == "SEI")
        self.assertEqual(coin["jev"]["rule_agreement"], "disagree")
        self.assertTrue(0 <= coin["jev"]["trend_strength"] <= 1)
        luna = [a for a in service.coin_detail("SEI")["analyses"] if a["source"] == "luna"]
        self.assertEqual(luna[0]["trigger"], "jev_disagree")

    def test_jev_waits_for_the_current_close(self):
        series = {base: rows[:-1] for base, rows in default_series().items()}  # yesterday's data only
        service = self.make(series)
        service.refresh()
        result = service.jev_daily()
        self.assertEqual(set(result["coins"].values()), {"no_current_close"})
        self.assertEqual(self.jev.states, [])

    def test_briefing_once_per_day_with_the_jev_table(self):
        service = self.make()
        service.refresh()
        service.jev_daily()
        first = service.daily_briefing()
        self.assertEqual(first["briefing"]["stance_market"] in {"risk_on", "mixed", "risk_off"}, True)
        service.daily_briefing()
        self.assertEqual(len(self.gpt.briefings), 1)
        self.assertTrue(all(coin["jev"] is not None for coin in self.gpt.briefings[0]["coins"]))
        self.assertIsNotNone(service.overview()["briefing"])

    def test_fixture_provider_is_refused_outside_tests(self):
        store = PaperStore(":memory:")
        self.addCleanup(store.close)
        runtime = PaperRuntime(store, clock=FixedClock(NOW))  # default config uses fixture-gpt / fixture-jev
        service = SpotCoTrader(runtime, source=FakeSource(default_series()))
        service.refresh()
        result = service.review("BTC", trigger="manual")
        self.assertIsNone(result["analysis"])
        self.assertTrue(result["blocked_reason"].startswith("fixture_provider"))
        self.assertFalse(service.ai_status()["enabled"])
        self.assertEqual(service.jev_daily()["coins"]["BTC"].split(":")[0], "blocked")


class ProviderPathTests(CoTraderCase):
    GPT = {"provider_id": "azure-gpt6-luna", "kind": "foundry_responses", "display_name": "Azure GPT-6 Luna",
           "base_url": "https://example-resource.services.ai.azure.com/api/projects/demo", "model": "gpt-6-luna",
           "enabled": True, "reasoning_effort": "medium", "credential_secret": "provider.azure-gpt6-luna",
           "timeout_seconds": 60}
    PRICE = {"provider_kind": "foundry_responses", "model": "gpt-6-luna", "version": "integration-check-explicit-fallback.v1",
             "effective_from": "2026-01-01T00:00:00Z", "input_per_million": 15.0, "cached_input_per_million": 15.0,
             "output_per_million": 60.0, "reasoning_billing_rule": "included_in_output",
             "source": "EXPLICIT integration-check fallback, NOT contract pricing"}

    def luna_service(self, transport, *, price=True):
        store = PaperStore(":memory:")
        store.save_provider(self.GPT)
        config = default_experiment_config()
        config["gpt_provider_id"] = "azure-gpt6-luna"
        store.save_experiment(config)
        if price:
            store.cost_ledger.add_price(self.PRICE)
        secrets = MemorySecretStore()
        secrets.set("provider.azure-gpt6-luna", SENTINEL)
        resolver = CredentialResolver(secrets, environ={})
        self.store = store
        self.addCleanup(store.close)
        runtime = PaperRuntime(store, clock=FixedClock(NOW), resolver=resolver, jev_provider=CountingJev())
        adapter = ResponsesAdapter(self.GPT, transport=transport, resolver=resolver, max_output_tokens=4000,
                                   skill_context="unit-test skill extract")
        runtime._gpt_provider_override = adapter
        self.runtime = runtime
        service = SpotCoTrader(runtime, source=FakeSource(default_series()), allow_fixture_ai=True)
        service.refresh()
        return service

    def test_budget_block_records_the_reason_and_never_calls(self):
        def transport(*args):
            raise AssertionError("no request may be sent when the budget blocks")

        service = self.luna_service(transport, price=False)
        result = service.review("BTC", trigger="manual")
        self.assertIsNone(result["analysis"])
        self.assertEqual(result["blocked_reason"], "UNKNOWN_PRICE")
        row = self.store._query("SELECT status, reason, payload_json FROM cotrader_analyses")[0]
        self.assertEqual((row["status"], row["reason"], row["payload_json"]), ("blocked", "UNKNOWN_PRICE", None))
        self.assertEqual(service.ai_status()["blocked_reason"], "UNKNOWN_PRICE")
        service.update_settings({"ai_budget": {"daily_usd": 0.0001}})
        self.store.cost_ledger.add_price(self.PRICE)
        self.assertEqual(service.review("ETH", trigger="manual")["blocked_reason"], "DAILY_BUDGET")
        # a blocked manual call does not use up the manual cap
        for _ in range(3):
            service.review("ETH", trigger="manual")

    def test_real_adapter_path_is_parsed_costed_and_secret_free(self):
        seen = {}

        def transport(url, headers, body, timeout):
            seen["body"] = json.loads(body)
            seen["headers"] = headers
            review = {
                "stance": "caution", "conviction": "medium", "summary_th": "ภาพรวมยังเป็นขาขึ้น แต่ราคาห่างเส้นเฉลี่ยมาก ควรระวัง",
                "bull_points": ["แนวโน้ม 60 วันเป็นบวก"], "bear_points": ["ราคายืดตัวมาก"],
                "key_levels": {"support": [80000], "resistance": [88000], "invalidation": 76500},
                "risks": ["ความผันผวนสูง"], "change_my_mind": "ปิดรายวันต่ำกว่าเส้นแนวโน้ม",
            }
            response = {"id": "resp-1", "model": "gpt-6-luna", "status": "completed", "reasoning": {"effort": "medium"},
                        "output_text": json.dumps(review, ensure_ascii=False),
                        "usage": {"input_tokens": 3000, "output_tokens": 900}}
            return json.dumps(response).encode(), {"x-request-id": "req-1"}

        service = self.luna_service(transport)
        result = service.review("BTC", trigger="manual")
        analysis = result["analysis"]
        self.assertEqual(analysis["stance"], "caution")
        self.assertAlmostEqual(analysis["cost_usd"], (3000 * 15 + 900 * 60) / 1_000_000)
        self.assertEqual(seen["body"]["text"]["format"]["schema"], SPOT_REVIEW_RESPONSE_SCHEMA)
        self.assertEqual(seen["body"]["max_output_tokens"], 4000)
        self.assertIn("plain, simple Thai", seen["body"]["instructions"])
        self.assertNotIn(SENTINEL, json.dumps(seen["body"]))
        self.assertIn(SENTINEL, seen["headers"]["api-key"])  # only in the transport header
        ai = service.ai_status()
        self.assertTrue(ai["pricing_is_fallback"])
        self.assertEqual(ai["luna_calls_today"], 1)
        self.assertGreater(ai["luna_spent_usd"], 0)
        self.assertGreater(ai["spent_today_usd"], 0)
        experiment_id = self.store.experiment()["experiment_id"]
        self.assertEqual(self.store.cost_ledger.usage_events(experiment_id), [])  # never in the experiment's economics
        self.assertEqual(len(self.store.cost_ledger.usage_events(LEDGER_SCOPE)), 1)
        dump = json.dumps([dict(r) for r in self.store._query("SELECT * FROM cotrader_analyses")], ensure_ascii=False)
        self.assertNotIn(SENTINEL, dump)

    def test_invalid_model_output_fails_closed(self):
        def transport(url, headers, body, timeout):
            bad = {"stance": "agree", "conviction": "medium", "summary_th": "English only summary",
                   "bull_points": ["a"], "bear_points": ["b"], "key_levels": {"support": [], "resistance": [], "invalidation": None},
                   "risks": ["c"], "change_my_mind": "d"}
            response = {"id": "r", "model": "gpt-6-luna", "status": "completed", "output_text": json.dumps(bad),
                        "usage": {"input_tokens": 10, "output_tokens": 10}}
            return json.dumps(response).encode(), {}

        service = self.luna_service(transport)
        result = service.review("BTC", trigger="manual")
        self.assertIsNone(result["analysis"])
        self.assertIn("not Thai", result["blocked_reason"])
        self.assertEqual(self.store._query("SELECT status FROM cotrader_analyses")[0]["status"], "failed")


class AdapterContractTests(unittest.TestCase):
    def test_fixture_outputs_pass_the_response_schemas(self):
        rows = signal_rows(candles_from(wave(300)))
        last = rows[-1]
        context = {"coin": "BTC", "symbol": "BTC_USDT", "state": last["state"], "ret_60d": last["ret_60d"],
                   "dist_sma100": last["dist_sma100"], "vol_20d": last["vol_20d"], "sma100": last["sma100"],
                   "candles": [[r["t"], r["o"], r["h"], r["l"], r["c"], r["v"]] for r in rows[-120:]]}
        review = FixtureGPTProvider().generate_spot_review(context)["review"]
        errors: list[str] = []
        validate_repo.validate_schema(review, SPOT_REVIEW_RESPONSE_SCHEMA, SCHEMA_PATH, SPOT_REVIEW_RESPONSE_SCHEMA, "review", errors)
        self.assertEqual(errors, [])
        self.assertRegex(review["summary_th"], "[฀-๿]")
        self.assertLessEqual(len(review["summary_th"]), 700)
        briefing_context = {"regime": {"label": "MIXED"}, "coins": [{"coin": "BTC", "changed_today": True}]}
        briefing = FixtureGPTProvider().generate_spot_briefing(briefing_context)["briefing"]
        schema = spot_briefing_response_schema(["BTC"])
        validate_repo.validate_schema(briefing, schema, SCHEMA_PATH, schema, "briefing", errors)
        self.assertEqual(errors, [])

    def test_spot_review_parser_is_strict(self):
        good = {"stance": "agree", "conviction": "low", "summary_th": "สรุป", "bull_points": ["ก"], "bear_points": ["ข"],
                "key_levels": {"support": [1.0], "resistance": [2.0], "invalidation": None}, "risks": ["ค"],
                "change_my_mind": "ง"}
        self.assertEqual(parse_spot_review(good)["stance"], "agree")
        for bad in ({**good, "stance": "buy"}, {**good, "extra": 1}, {**good, "bull_points": []},
                    {**good, "key_levels": {"support": [-1], "resistance": [], "invalidation": None}}):
            with self.assertRaises(AIProviderError):
                parse_spot_review(bad)
        long = parse_spot_review({**good, "summary_th": "ก" * 900})
        self.assertLessEqual(len(long["summary_th"]), 700)

    def test_spot_jev_question_set_and_unit_scores(self):
        questions = build_spot_jev_questions()
        self.assertEqual(set(questions), {"trend_regime", "trend_strength", "reversal_risk", "rule_agreement",
                                          "entry_timing", "key_risk"})
        self.assertEqual(set(questions["rule_agreement"]["criteria"]), {"agree", "caution", "disagree"})
        self.assertEqual(len(questions["trend_strength"]["criteria"]), 5)
        self.assertEqual(set(questions["entry_timing"]["criteria"]),
                         {"good_now", "wait_pullback", "extended_late", "not_applicable"})
        state = {"rule": {"state": "HOLD"}, "ret_60d": 0.25, "dist_sma100": 0.12, "vol_20d": 0.03,
                 "data_cutoff": "2026-09-29T00:00:00Z"}
        vector = FixtureJevProvider().evaluate_spot(state)
        self.assertEqual(vector["question_schema_version"], "jev-spot-questions.v1")
        self.assertEqual(jev_score_unit(vector["answers"]["trend_strength"]), 0.5)
        self.assertEqual(jev_score_unit({"type": "score", "value": 4.0}), 1.0)
        self.assertEqual(jev_score_unit({"type": "score", "value": 0.0}), 0.0)
        self.assertIsNone(jev_score_unit({"type": "choice", "value": "agree"}))
        self.assertEqual(vector["answers"]["entry_timing"]["value"], "wait_pullback")

    def test_jev_adapter_sends_the_spot_question_set(self):
        sent = {}

        def transport(url, headers, body, timeout):
            payload = json.loads(body)
            sent.update(payload)
            answers = FixtureJevProvider().evaluate_spot(payload["state"])["answers"]
            raw = {}
            for key, answer in answers.items():
                item = {"type": answer["type"], "confidence": answer.get("confidence"),
                        "probabilities": answer.get("probabilities")}
                item[answer["type"]] = answer["value"]
                if "legend" in answer:
                    item["legend"] = answer["legend"]
                raw[key] = item
            return json.dumps({"model": "jev-latest", "answers": raw, "usage": {"input_tokens": 500, "output_tokens": 80}}).encode(), {}

        provider = {"provider_id": "typesafe-jev", "kind": "typesafe_jev", "display_name": "Jev", "model": "jev-latest",
                    "base_url": "https://api.typesafe.ai", "enabled": True, "credential_env": "UNIT_JEV"}
        adapter = JevAdapter(provider, environ={"UNIT_JEV": SENTINEL}, transport=transport)
        state = {"rule": {"state": "CASH"}, "ret_60d": -0.2, "dist_sma100": -0.1, "vol_20d": 0.02}
        vector = adapter.evaluate_spot(state)
        self.assertEqual(set(sent["questions"]), set(build_spot_jev_questions()))
        self.assertEqual(vector["answers"]["trend_regime"]["value"], "downtrend")
        self.assertNotIn(SENTINEL, json.dumps(sent))
        with self.assertRaises(AIProviderError):
            parse_jev_response({"model": "x", "answers": {}}, build_spot_jev_questions(), snapshot_hash="h")


# ---------------------------------------------------------------------------
# scorecard, journal, settings, sizing
# ---------------------------------------------------------------------------


class ScorecardJournalTests(CoTraderCase):
    def test_scorecard_rows_math_and_small_n(self):
        samples = [
            {"source": "luna", "stance": "agree", "fwd_7d": 0.10, "fwd_30d": 0.2, "rule_same_7d": True, "rule_same_30d": True},
            {"source": "luna", "stance": "agree", "fwd_7d": -0.02, "fwd_30d": None, "rule_same_7d": False, "rule_same_30d": None},
            {"source": "luna", "stance": "disagree", "fwd_7d": -0.05, "fwd_30d": None, "rule_same_7d": True, "rule_same_30d": None},
        ]
        rows = scorecard_rows(samples)
        agree = next(r for r in rows if r["source"] == "luna" and r["stance"] == "agree")
        self.assertEqual((agree["n_7d"], agree["n_30d"]), (2, 1))
        self.assertAlmostEqual(agree["avg_ret_7d"], 0.04)
        self.assertAlmostEqual(agree["hit_7d"], 0.5)
        self.assertAlmostEqual(agree["rule_same_7d"], 0.5)
        caution = next(r for r in rows if r["source"] == "luna" and r["stance"] == "caution")
        self.assertEqual((caution["n_7d"], caution["avg_ret_7d"], caution["hit_7d"]), (0, None, None))
        self.assertEqual(len(rows), 9)

    def test_scorecard_uses_closed_forward_bars(self):
        service = self.make()
        service.refresh()
        self.clock.now = NOW - timedelta(days=40)  # analyses recorded 40 and 3 days ago
        rows, _ = service.signals("BTC")
        past = [r for r in rows if r["close_ts"] == BOUNDARY - 40 * DAY][0]
        recent = [r for r in rows if r["close_ts"] == BOUNDARY - 3 * DAY][0]
        for ref, stance in ((past, "agree"), (recent, "disagree")):
            service._record(coin="BTC", source="luna", trigger="manual", trigger_key=f"k{stance}", rule_state=ref["state"],
                            status="ok", stance=stance, payload={"summary_th": "ก"}, call_id="c", ref_close=ref["close"],
                            ref_close_ts=ref["close_ts"])
        self.clock.now = NOW
        card = service.scorecard()
        self.assertEqual(conforms(card, "#/$defs/scorecard"), [])
        agree = next(r for r in card["rows"] if r["source"] == "luna" and r["stance"] == "agree")
        by_ts = {r["close_ts"]: r["close"] for r in rows}
        self.assertEqual((agree["n_7d"], agree["n_30d"]), (1, 1))
        self.assertAlmostEqual(agree["avg_ret_7d"], round(by_ts[past["close_ts"] + 7 * DAY] / past["close"] - 1, 6))
        disagree = next(r for r in card["rows"] if r["source"] == "luna" and r["stance"] == "disagree")
        self.assertEqual(disagree["n_7d"], 0)  # the +7d close does not exist yet: not scored
        self.assertIsNone(next(s for s in card["spreads"] if s["source"] == "luna")["agree_minus_disagree_7d"])

    def test_journal_round_trip_and_outcomes(self):
        service = self.make()
        service.refresh()
        entry = service.add_journal({"coin": "BTC_USDT", "action": "buy", "price": 100.0, "amount_usdt": 50, "note": "first"})
        self.assertEqual(entry["coin"], "BTC")
        self.assertTrue(entry["id"].startswith("j-"))
        price_now = self.source.series["BTC"][-1]["c"] * 1.01
        self.assertAlmostEqual(entry["price_now"], price_now)
        self.assertAlmostEqual(entry["change_since"], round(price_now / 100 - 1, 6))
        state = service.overview()["coins"][0]["state"]
        self.assertEqual(entry["rule_state"], state)
        self.assertEqual(entry["rule_agreed"], state == "HOLD")
        skipped = service.add_journal({"coin": "ETH", "action": "skip", "price": None, "amount_usdt": None, "note": ""})
        self.assertIsNone(skipped["change_since"])  # no P&L invented without a price
        self.assertEqual([e["id"] for e in service.journal("BTC")], [entry["id"]])
        self.assertEqual(len(service.journal()), 2)
        for bad in ({"coin": "BTC", "action": "short"}, {"coin": "BTC", "action": "buy", "price": -1},
                    {"coin": "BTC", "action": "buy", "note": "x" * 501}, {"coin": "BTC", "action": "buy", "extra": 1},
                    {"coin": "DOGE", "action": "buy"}):
            with self.assertRaises(PaperTradingError):
                service.add_journal(bad)

    def test_settings_capital_and_sizing(self):
        holdings = {"BTC": {"qty": 0.001, "value_usdt": 60.0}, "ETH": {"qty": 0.0, "value_usdt": 0.0}}
        service = self.make(holdings_provider=lambda base: holdings.get(base),
                            holdings_total_provider=lambda: 700.0)
        service.refresh()
        overview = service.overview()
        self.assertEqual(overview["settings"], {"cotrader_capital_usdt": 700.0, "capital_source": "holdings",
                                                "sizing_method": "equal_weight"})
        service.update_settings({"cotrader_capital_usdt": 1400, "sizing_method": "inverse_vol"})
        coins = {c["base"]: c for c in service.overview()["coins"]}
        self.assertTrue(coins["BTC"]["held"])
        self.assertFalse(coins["ETH"]["held"])
        self.assertIsNone(coins["NEAR"]["held"])  # unknown holdings stay null
        weights = [c["sizing"]["weight"] for c in coins.values()]
        self.assertAlmostEqual(sum(weights), 1.0, places=4)
        for coin in coins.values():
            sizing = coin["sizing"]
            expected_target = sizing["weight"] * 1400 if coin["state"] == "HOLD" else 0.0
            self.assertAlmostEqual(sizing["target_usdt"], round(expected_target, 2), places=2)
            if coin["base"] == "BTC":
                self.assertAlmostEqual(sizing["delta_usdt"], round(expected_target - 60.0, 2), places=2)
                self.assertAlmostEqual(sizing["delta_qty"], round((expected_target - 60.0) / coin["price"], 8), places=6)
            if coin["base"] == "NEAR":
                self.assertIsNone(sizing["delta_usdt"])
            if coin["state"] == "HOLD":
                price, level = coin["price"], coin["trend_line_next"]
                self.assertAlmostEqual(sizing["risk_to_trend_line_usdt"],
                                       round(expected_target * max(0, price - level) / price, 2), places=1)
            ladder = coin["ladder"]
            self.assertIn(ladder["state"], {"OUT", "STARTER", "FULL"})
            self.assertEqual(ladder["state"], coin["ladder_state"])
            if ladder["action"]["target_usdt"] is not None and coin["base"] == "BTC":
                self.assertAlmostEqual(ladder["action"]["delta_usdt"], round(ladder["action"]["target_usdt"] - 60.0, 2), places=1)
        self.assertEqual(coins["BTC"]["action"]["type"], "HOLD" if coins["BTC"]["state"] == "HOLD" else "SELL")
        service.update_settings({"cotrader_capital_usdt": None})
        self.assertEqual(service.settings_view()["capital_source"], "holdings")
        for bad in ({"sizing_method": "kelly"}, {"cotrader_capital_usdt": 0}, {"universe": ["BTC"]},
                    {"ai_budget": {"limit_action": "PAUSE_NEW_ENTRIES"}}, {"leverage": 3}):
            with self.assertRaises(PaperTradingError):
                service.update_settings(bad)

    def test_null_capital_never_invents_a_size(self):
        service = self.make()
        service.refresh()
        for coin in service.overview()["coins"]:
            self.assertIsNone(coin["held"])
            self.assertIsNone(coin["sizing"]["target_usdt"])
            self.assertIsNone(coin["sizing"]["delta_usdt"])
            self.assertIsNone(coin["ladder"]["action"]["delta_usdt"])
            self.assertIsNone(coin["ladder"]["action"]["target_usdt"])
            ladder = coin["ladder"]
            self.assertAlmostEqual(ladder["action"]["target_weight"],
                                   round(ladder["action"]["capital_fraction"] * 7, 4), places=3)  # slots
            self.assertLessEqual(ladder["action"]["target_weight"], 2.0)
        total = sum(c["ladder"]["action"]["capital_fraction"] for c in service.overview()["coins"])
        self.assertLessEqual(total, 1.0 + 1e-9)  # the ladder never exceeds 100% of capital


# ---------------------------------------------------------------------------
# scheduler
# ---------------------------------------------------------------------------


class SchedulerTests(CoTraderCase):
    def test_daily_schedule_refresh_jev_briefing_and_restart_safety(self):
        midnight = datetime.fromtimestamp(BOUNDARY, tz=timezone.utc)
        service = self.make(changing_series(), clock=FixedClock(midnight + timedelta(seconds=30)))
        scheduler = CoTraderScheduler(service)
        self.assertNotIn("refresh", scheduler.tick())  # before the refresh delay
        self.clock.now = midnight + timedelta(minutes=2)
        self.assertEqual(scheduler.tick()["refresh"], "done")
        self.assertEqual(len(self.gpt.reviews), 1)  # BTC state change
        self.assertEqual(self.jev.states, [])
        self.clock.now = midnight + timedelta(minutes=6)
        scheduler.tick()
        self.assertEqual(len(self.jev.states), 7)
        disagreeing = {row["coin"] for row in self.store._query(
            "SELECT coin FROM cotrader_analyses WHERE source='jev' AND stance='disagree'")}
        # Jev "disagree" escalates to Luna, except BTC: its state-change review today is reused (cached)
        self.assertEqual(len(self.gpt.reviews), 1 + len(disagreeing - {"BTC"}))
        self.assertEqual(self.gpt.briefings, [])
        self.clock.now = midnight + timedelta(minutes=11)
        self.assertEqual(scheduler.tick()["briefing"], "ok")
        before = (len(self.jev.states), len(self.gpt.briefings), len(self.gpt.reviews))
        self.assertEqual(before[:2], (7, 1))
        restarted = CoTraderScheduler(SpotCoTrader(self.runtime, source=self.source, allow_fixture_ai=True))
        restarted.tick()
        scheduler.tick()
        self.assertEqual((len(self.jev.states), len(self.gpt.briefings), len(self.gpt.reviews)), before)

    def test_refresh_retries_until_the_close_arrives(self):
        full = default_series()
        lagging = {base: rows[:-1] for base, rows in full.items()}
        midnight = datetime.fromtimestamp(BOUNDARY, tz=timezone.utc)
        service = self.make(lagging, clock=FixedClock(midnight + timedelta(minutes=2)))
        scheduler = CoTraderScheduler(service, retry_seconds=120)
        self.assertEqual(scheduler.tick()["refresh"], "retrying")
        self.clock.now += timedelta(seconds=30)
        self.assertNotIn("refresh", scheduler.tick())  # waits for the retry spacing
        self.source.series = full
        self.clock.now += timedelta(minutes=2)
        self.assertEqual(scheduler.tick()["refresh"], "done")

    def test_thread_starts_and_stops_cleanly(self):
        service = self.make()
        scheduler = CoTraderScheduler(service, poll_seconds=0.05)
        scheduler.start()
        time.sleep(0.2)
        started = time.monotonic()
        scheduler.shutdown(timeout=5)
        self.assertLess(time.monotonic() - started, 2)
        self.assertIsNone(scheduler._thread)
        self.assertIsNone(scheduler.last_error)


# ---------------------------------------------------------------------------
# holdings integration (crypto_eval.holdings)
# ---------------------------------------------------------------------------


class HoldingsIntegrationTests(CoTraderCase):
    def attached(self):
        from crypto_eval.holdings import Holdings
        from crypto_eval.spot_cotrader import attach_cotrader

        self.make()
        service = attach_cotrader(self.runtime, source=self.source, allow_fixture_ai=True)
        prices = {f"{base}_USDT": {"last": str(rows[-1]["c"])} for base, rows in self.source.series.items()}
        self.runtime.holdings._ticker_source = lambda: prices
        self.assertIsInstance(self.runtime.holdings, Holdings)
        self.assertIs(service.holdings, self.runtime.holdings)
        service.refresh()
        return service

    def test_rule_state_alignment_and_held_come_from_holdings(self):
        service = self.attached()
        before = {c["base"]: c["held"] for c in service.overview()["coins"]}
        self.assertEqual(set(before.values()), {None})  # no holdings known yet
        self.runtime.holdings.save_manual({"base": "BTC", "qty": 0.5, "avg_price": 100.0})
        coins = {c["base"]: c for c in service.overview()["coins"]}
        self.assertTrue(coins["BTC"]["held"])
        self.assertFalse(coins["ETH"]["held"])  # holdings are known now: not held
        self.assertAlmostEqual(coins["BTC"]["sizing"]["current_usdt"],
                               round(0.5 * self.source.series["BTC"][-1]["c"], 2), places=2)
        self.assertIsNone(service.settings_view()["cotrader_capital_usdt"])  # manual only: cash unknown
        payload = self.runtime.holdings.payload()
        btc = next(row for row in payload["holdings"] if row["base"] == "BTC")
        self.assertEqual(btc["rule_state"], coins["BTC"]["state"])
        self.assertIsNotNone(btc["alignment"])
        self.assertIsNone(service.rule_state("DOGE"))

    def test_holding_reaches_ai_only_when_shared(self):
        service = self.attached()
        self.runtime.holdings.save_manual({"base": "ETH", "qty": 2, "avg_price": 50.0})
        service.review("ETH", trigger="manual")
        self.assertIsNone(self.gpt.reviews[-1]["holding"])
        self.runtime.holdings.update_settings({"share_holdings_with_ai": True})
        service.review("ETH", trigger="manual")
        holding = self.gpt.reviews[-1]["holding"]
        self.assertEqual((holding["held"], holding["qty"], holding["avg_price"]), (True, 2.0, 50.0))
        service.jev_daily()
        state = next(item for item in self.jev.states if item["coin"] == "ETH")
        self.assertEqual(set(state["holding"]), {"held", "unrealized_pct"})

    def test_scheduler_calls_holdings_sync_if_due(self):
        service = self.make()
        calls = []

        class FakeHoldings:
            def sync_if_due(self):
                calls.append(1)
                return {"sources": {"gate": {"status": "OK"}}}

            def held(self, base):
                return None

        service.holdings = FakeHoldings()
        out = CoTraderScheduler(service).tick()
        self.assertEqual((len(calls), out["holdings_sync"]), (1, "OK"))


# ---------------------------------------------------------------------------
# endpoints (live loopback server)
# ---------------------------------------------------------------------------


class EndpointTests(CoTraderCase):
    def serve(self, cotrader=True):
        runtime = self.runtime
        scheduler = PaperScheduler(runtime)
        server = PaperHTTPServer(("127.0.0.1", 0), PaperRequestHandler, runtime, scheduler)
        if cotrader:
            server.cotrader = self.service
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(scheduler.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join, 5)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def call(self, base_url, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(f"{base_url}{path}", data=data, method="GET" if body is None else "POST",
                                         headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read().decode()
                return response.status, json.loads(raw), raw
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            return exc.code, json.loads(raw), raw

    def test_contract_endpoints_end_to_end(self):
        self.make(changing_series())
        self.store.save_provider({"provider_id": "azure-gpt6-luna", "kind": "foundry_responses", "display_name": "Luna",
                                  "model": "gpt-6-luna", "enabled": True, "credential_env": "UNIT_LUNA_KEY_REF",
                                  "base_url": "https://example-resource.services.ai.azure.com"})
        self.service.refresh()
        self.service.process_close()
        self.service.jev_daily()
        self.service.daily_briefing()
        base_url = self.serve()
        bodies = []

        status, overview, raw = self.call(base_url, "/api/cotrader")
        bodies.append(raw)
        self.assertEqual(status, 200)
        self.assertEqual(conforms(overview), [])
        self.assertEqual(len(overview["coins"]), 7)
        self.assertEqual(overview["last_close"], "2026-09-29T00:00:00Z")
        self.assertEqual(overview["next_close"], "2026-09-30T00:00:00Z")
        self.assertIsNotNone(overview["briefing"])

        status, detail, raw = self.call(base_url, "/api/cotrader/btc")
        bodies.append(raw)
        self.assertEqual(status, 200)
        self.assertEqual(conforms(detail, "#/$defs/coin_detail"), [])
        self.assertEqual(len(detail["candles"]), 400)
        self.assertEqual(detail["candles"][-1][0] + DAY, BOUNDARY)
        self.assertEqual(detail["periods"][0]["from"] >= detail["periods"][0]["from"], True)
        self.assertEqual(detail["trend_line_series"][-1][0], BOUNDARY)
        self.assertIn(detail["analyses"][0]["source"], {"jev", "luna"})
        self.assertFalse(detail["evidence"]["insufficient_history"])
        for name in ("ladder", "rule", "cdc_1d", "buy_hold"):
            self.assertLessEqual(0, detail["evidence"][name]["time_in_market_pct"])
            self.assertLessEqual(detail["evidence"][name]["time_in_market_pct"], 1)
        self.assertTrue(all(zone in {"green", "blue", "yellow", "red"} for _, zone in detail["cdc_series"]))
        self.assertTrue(all({"at", "action"} <= set(item) for item in detail["ladder_transitions"]))

        status, body, _ = self.call(base_url, "/api/cotrader/DOGE")
        self.assertEqual(status, 404)

        for _ in range(3):
            status, body, raw = self.call(base_url, "/api/cotrader/ETH/analyze", {"confirm": False})
            bodies.append(raw)
            self.assertEqual(status, 200)
            self.assertEqual(conforms(body["analysis"], "#/$defs/analysis"), [])
        status, body, _ = self.call(base_url, "/api/cotrader/ETH/analyze", {"confirm": False})
        self.assertEqual((status, body), (409, {"confirmation_required": True, "reason": "manual_cap"}))
        status, body, _ = self.call(base_url, "/api/cotrader/ETH/analyze", {"confirm": True})
        self.assertEqual(status, 200)
        status, body, _ = self.call(base_url, "/api/cotrader/ETH/analyze", {"confirm": "yes"})
        self.assertEqual(status, 400)

        status, body, raw = self.call(base_url, "/api/cotrader/journal",
                                      {"coin": "NEAR", "action": "hold", "price": 5.0, "amount_usdt": None, "note": "n"})
        bodies.append(raw)
        self.assertEqual(status, 200)
        self.assertEqual(conforms(body["entry"], "#/$defs/journal_entry"), [])
        status, body, _ = self.call(base_url, "/api/cotrader/journal")
        self.assertEqual(len(body["journal"]), 1)
        status, body, _ = self.call(base_url, "/api/cotrader/NEAR")
        self.assertEqual(len(body["journal"]), 1)

        status, card, raw = self.call(base_url, "/api/cotrader/scorecard")
        bodies.append(raw)
        self.assertEqual(status, 200)
        self.assertEqual(conforms(card, "#/$defs/scorecard"), [])

        status, body, raw = self.call(base_url, "/api/cotrader/settings",
                                      {"cotrader_capital_usdt": 1000, "sizing_method": "equal_weight"})
        bodies.append(raw)
        self.assertEqual(status, 200)
        self.assertEqual(body["settings"]["capital_source"], "user")
        status, overview, _ = self.call(base_url, "/api/cotrader")
        self.assertEqual(overview["settings"]["cotrader_capital_usdt"], 1000.0)
        self.assertEqual(conforms(overview), [])

        # POSTs keep the existing origin validation
        status, body, _ = self.call(base_url, "/api/cotrader/journal", {"coin": "BTC", "action": "buy"},
                                    headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 400)
        status, body, _ = self.call(base_url, "/api/cotrader/BTC/analyze", {}, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 400)
        self.assertEqual(len(self.service.journal()), 1)

        for raw in bodies:
            self.assertNotIn(SENTINEL, raw)
            self.assertNotIn("UNIT_LUNA_KEY_REF", raw)
            self.assertNotIn("credential", raw)

    def test_budget_blocked_analyze_returns_200_with_reason(self):
        self.make()
        self.service.refresh()
        self.service.update_settings({"ai_budget": {"daily_usd": 0.0}})
        # fixture calls are free, so force a real-adapter path through a blocked provider instead
        self.runtime._gpt_provider_override = ResponsesAdapter(
            {"provider_id": "fixture-gpt", "kind": "foundry_responses", "model": "gpt-6-luna", "base_url": "https://x.example",
             "reasoning_effort": "medium"}, transport=lambda *a: (_ for _ in ()).throw(AssertionError("sent")))
        base_url = self.serve()
        status, body, _ = self.call(base_url, "/api/cotrader/BTC/analyze", {"confirm": False})
        self.assertEqual(status, 200)
        self.assertIsNone(body["analysis"])
        self.assertEqual(body["blocked_reason"], "UNKNOWN_PRICE")

    def test_server_without_the_flag_has_no_cotrader_routes(self):
        self.make()
        base_url = self.serve(cotrader=False)
        for path in ("/api/cotrader", "/api/cotrader/BTC", "/api/cotrader/scorecard"):
            status, _, _ = self.call(base_url, path)
            self.assertEqual(status, 404)
        status, _, _ = self.call(base_url, "/api/cotrader/journal", {"coin": "BTC", "action": "buy"})
        self.assertEqual(status, 404)
        status, health, _ = self.call(base_url, "/api/health")
        self.assertEqual(health["real_money_execution"], False)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# watchlist (add / remove coins without any exchange key) and provider-setup retries
# ---------------------------------------------------------------------------


class VolumeSource(FakeSource):
    """FakeSource whose tickers carry a 24h quote volume per base (default 50M USDT)."""

    def __init__(self, series, volumes=None, **kwargs):
        super().__init__(series, **kwargs)
        self.volumes = volumes or {}

    def tickers(self):
        return {pair: {**row, "quote_volume": self.volumes.get(pair.split("_")[0], 50_000_000.0)}
                for pair, row in super().tickers().items()}


class WatchlistTests(CoTraderCase):
    def make_with(self, extra: dict[str, list[dict]], volumes=None):
        series = {**default_series(), **extra}
        service = self.make(series)
        # the default universe must not include the extra coins; the source lists them on "Gate"
        self.source = VolumeSource(series, volumes)
        service.source = self.source
        return service

    def test_add_listed_coin_fetches_history_and_reports_liquidity(self):
        service = self.make_with({"SOL": candles_from(wave(500, 2.0))})
        result = service.update_watchlist({"add": "sol_usdt"})
        self.assertEqual(result["universe"][-1], "SOL")
        added = result["added"]
        self.assertEqual(added["base"], "SOL")
        self.assertFalse(added["thin"])
        self.assertTrue(added["history_ok"])
        self.assertGreaterEqual(added["daily_bars"], 365)
        self.assertIn("SOL", [coin["base"] for coin in service.overview()["coins"]])
        self.assertEqual({pair for pair, _ in self.source.calls}, {"SOL_USDT"})  # only the new coin is fetched

    def test_thin_or_short_history_coin_is_added_with_warnings(self):
        service = self.make_with({"NEWC": candles_from(wave(90))}, volumes={"NEWC": 120_000.0})
        added = service.update_watchlist({"add": "NEWC"})["added"]
        self.assertTrue(added["thin"])
        self.assertFalse(added["history_ok"])
        self.assertEqual(added["quote_volume_24h_usdt"], 120000)

    def test_unlisted_duplicate_stablecoin_and_full_list_are_refused(self):
        service = self.make_with({})
        with self.assertRaisesRegex(PaperTradingError, "not listed on Gate spot"):
            service.update_watchlist({"add": "NOPE"})
        with self.assertRaisesRegex(PaperTradingError, "already in the watchlist"):
            service.update_watchlist({"add": "BTC"})
        with self.assertRaisesRegex(PaperTradingError, "stablecoins"):
            service.update_watchlist({"add": "USDC"})
        with self.assertRaisesRegex(PaperTradingError, "exactly one"):
            service.update_watchlist({"add": "SOL", "remove": "BTC"})
        service.update_settings({"universe": [f"C{i}" for i in range(20)]})
        with self.assertRaisesRegex(PaperTradingError, "at most 20"):
            service.update_watchlist({"add": "BTC"})
        self.assertEqual(len(service.universe()), 20)

    def test_remove_keeps_at_least_one_coin(self):
        service = self.make_with({})
        self.assertEqual(service.update_watchlist({"remove": "SEI"})["removed"], "SEI")
        self.assertNotIn("SEI", service.universe())
        for base in service.universe()[:-1]:
            service.update_watchlist({"remove": base})
        with self.assertRaisesRegex(PaperTradingError, "at least one"):
            service.update_watchlist({"remove": service.universe()[0]})

    def test_listing_check_failure_does_not_change_the_watchlist(self):
        service = self.make_with({"SOL": candles_from(wave(500))})

        def broken():
            raise OSError("network down")

        self.source.tickers = broken
        before = service.universe()
        with self.assertRaisesRegex(PaperTradingError, "cannot check Gate spot listings"):
            service.update_watchlist({"add": "SOL"})
        self.assertEqual(service.universe(), before)


class ProviderSetupRetryTests(CoTraderCase):
    def test_provider_setup_block_is_retried_after_spacing_but_budget_block_is_final(self):
        store = PaperStore(":memory:")
        self.addCleanup(store.close)
        clock = FixedClock(NOW)
        runtime = PaperRuntime(store, clock=clock)  # fixture providers -> refused outside tests
        service = SpotCoTrader(runtime, source=FakeSource(default_series()), clock=clock)
        service.refresh()
        self.assertTrue(service.jev_daily()["coins"]["BTC"].startswith("blocked:fixture_provider"))
        # within the retry spacing: no new attempt row
        rows = lambda: store._query("SELECT COUNT(*) AS n FROM cotrader_analyses WHERE coin='BTC' AND source='jev'")[0]["n"]
        self.assertEqual(rows(), 1)
        service.jev_daily()
        self.assertEqual(rows(), 1)
        # after the spacing the provider is tried again (still fixture here, so blocked again)
        clock.now = NOW + timedelta(minutes=11)
        service.jev_daily()
        self.assertEqual(rows(), 2)

    def test_allowed_attempt_ignores_setup_blocks_but_not_budget_blocks(self):
        service = self.make()
        key = "unit:key"
        common = {"coin": "BTC", "source": "jev", "trigger": "daily", "trigger_key": key, "call_id": "c1"}
        service._record(**common, status="blocked", reason="provider_unavailable")
        allowed, _ = service._attempt_allowed(key, NOW + timedelta(hours=1))
        self.assertTrue(allowed)
        service._record(**{**common, "call_id": "c2"}, status="blocked", reason="DAILY_BUDGET")
        allowed, existing = service._attempt_allowed(key, NOW + timedelta(hours=2))
        self.assertFalse(allowed)
        self.assertEqual(existing["reason"], "DAILY_BUDGET")


class HealthFlagTests(CoTraderCase):
    serve = EndpointTests.serve
    call = EndpointTests.call

    def test_health_reports_cotrader_mode_and_watchlist_endpoint(self):
        self.make()
        self.source = VolumeSource({**default_series(), "SOL": candles_from(wave(500))})
        self.service.source = self.source
        base_url = self.serve()
        status, health, _ = self.call(base_url, "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["cotrader"])
        status, body, _ = self.call(base_url, "/api/cotrader/watchlist", {"add": "SOL"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["added"]["base"], "SOL")
        status, body, _ = self.call(base_url, "/api/cotrader/watchlist", {"add": "NOPE"})
        self.assertEqual(status, 400)

    def test_health_without_cotrader_flag(self):
        self.make()
        base_url = self.serve(cotrader=False)
        _, health, _ = self.call(base_url, "/api/health")
        self.assertFalse(health["cotrader"])
