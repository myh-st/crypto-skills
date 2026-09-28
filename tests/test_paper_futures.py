from __future__ import annotations

import io
import json
import os
import csv
import time
import threading
import urllib.request
import contextlib
from unittest.mock import patch
import unittest
import urllib.error
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from crypto_eval.contracts import digest, parse_timestamp
from crypto_eval.envfile import load_environment_file
from crypto_eval.paper_ai import (
    AIProviderError,
    FixtureGPTProvider,
    FixtureJevProvider,
    JevAdapter,
    ResponsesAdapter,
    build_gpt_input,
    build_jev_questions,
    jev_state,
    parse_jev_response,
    route_escalation,
)
from crypto_eval.paper_contracts import (
    INTENT_SCHEMA_VERSION,
    PaperTradingError,
    TradingIntent,
    default_experiment_config,
    validate_experiment_config,
    validate_provider_config,
)
from crypto_eval.paper_market import (
    BinanceUsdMFuturesMarketDataProvider,
    FixtureFuturesMarketDataProvider,
    MarketDataError,
    WARMUP_PROFILES,
    floor_time,
)
from crypto_eval.paper_server import PaperHTTPServer, PaperRequestHandler
from crypto_eval.paper_runtime import (
    PaperRuntime,
    PaperScheduler,
    PaperStore,
    PaperScheduler,
    RiskEngine,
    RiskDecision,
    build_fast_intent,
    compute_features,
    liquidation_price,
)


ROOT = Path(__file__).resolve().parents[1]
UNIT_TOKEN = "local-unit-test-placeholder-not-a-credential"
ENV_SENTINEL = "UNIT_ENV_SECRET_SENTINEL_NOT_A_REAL_KEY"


class LocalEnvironmentFileTests(unittest.TestCase):
    def test_dotenv_parser_loads_plain_values_without_evaluation_or_logging(self):
        path = ROOT / f".test-envfile-{os.getpid()}"
        path.write_text(
            "\n".join(
                [
                    "# local-only fixture values",
                    f"TYPESAFE_API_KEY={ENV_SENTINEL}",
                    "AZURE_OPENAI_API_KEY=dotenv-value",
                    'OPENAI_API_KEY="$(echo must-not-run)"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.addCleanup(path.unlink, missing_ok=True)
        environment = {"AZURE_OPENAI_API_KEY": "process-value"}
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            loaded_count = load_environment_file(path, environ=environment)

        self.assertEqual(loaded_count, 2)
        self.assertEqual(environment["TYPESAFE_API_KEY"], ENV_SENTINEL)
        self.assertEqual(environment["AZURE_OPENAI_API_KEY"], "process-value")
        self.assertEqual(environment["OPENAI_API_KEY"], "$(echo must-not-run)")
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn(ENV_SENTINEL, output.getvalue())

    def test_dotenv_example_contains_only_supported_blank_variables(self):
        example = ROOT / ".env.example"
        entries = {
            line.split("=", 1)[0]: line.split("=", 1)[1]
            for line in example.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")
        }
        self.assertEqual(
            set(entries),
            {
                "TYPESAFE_API_KEY",
                "OPENAI_API_KEY",
                "AZURE_OPENAI_API_KEY",
                "COMPATIBLE_AI_API_KEY",
            },
        )
        self.assertTrue(all(value == "" for value in entries.values()))


class FixedClock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def make_type_safe_response(questions: dict) -> dict:
    answers = {}
    for key, question in questions.items():
        if question["type"] == "choice":
            options = list(question["criteria"])
            selected = "bull" if "bull" in options else options[0]
            probabilities = {option: (0.7 if option == selected else 0.3 / (len(options) - 1)) for option in options}
            answers[key] = {
                "type": "choice",
                "choice": selected,
                "confidence": 0.7,
                "probabilities": probabilities,
            }
        elif question["type"] == "score":
            score = min(3, len(question["criteria"]) - 1)
            probabilities = {str(index): 0.05 for index in range(len(question["criteria"]))}
            probabilities[str(score)] = 1 - sum(
                value for key, value in probabilities.items() if key != str(score)
            )
            answers[key] = {
                "type": "score",
                "score": float(score),
                "confidence": 0.75,
                "legend": {str(index): value for index, value in enumerate(question["criteria"])},
                "probabilities": probabilities,
            }
        else:
            answers[key] = {"type": "noul", "noul": 0.2}
    return {
        "model": "jev-test-model",
        "answers": answers,
        "usage": {"input_tokens": 32, "output_tokens": 14},
    }


def simple_candle(open_time: datetime, close_time: datetime, price: float) -> dict:
    return {
        "open_time": open_time.isoformat().replace("+00:00", "Z"),
        "close_time": close_time.isoformat().replace("+00:00", "Z"),
        "open": price,
        "high": price * 1.0002,
        "low": price * 0.9998,
        "close": price,
        "volume": 10,
    }


def saved_config(store: PaperStore, *, arm: str = "hybrid", force: bool = False) -> dict:
    config = default_experiment_config()
    config["evaluation_arms"] = [arm]
    config["primary_arm"] = arm
    config["force_escalation"] = force
    return store.save_experiment(config)


class CountingJevProvider:
    def __init__(self):
        self.calls = 0
        self.fixture = FixtureJevProvider()

    def evaluate(self, snapshot, features, portfolio):
        self.calls += 1
        return self.fixture.evaluate(snapshot, features, portfolio)


class CountingGPTProvider:
    def __init__(self):
        self.calls = []
        self.fixture = FixtureGPTProvider()

    def generate_intent(
        self,
        snapshot,
        features,
        portfolio,
        *,
        jev_vector,
        source_arm,
        include_skill,
    ):
        safe_input = build_gpt_input(
            snapshot, features, jev_vector=jev_vector, portfolio=portfolio
        )
        self.calls.append(
            {
                "arm": source_arm,
                "include_skill": include_skill,
                "input": safe_input,
            }
        )
        return self.fixture.generate_intent(
            snapshot,
            features,
            portfolio,
            jev_vector=jev_vector,
            source_arm=source_arm,
            include_skill=include_skill,
        )


class PaperFuturesContractsAndProviderTests(unittest.TestCase):
    def test_experiment_defaults_are_paper_and_leverage_is_capped(self):
        defaults = default_experiment_config()
        self.assertEqual(defaults["execution_mode"], "PAPER")
        self.assertEqual(defaults["starting_balance_usdt"], 100)
        self.assertEqual(defaults["risk_per_trade"], 0.01)
        self.assertEqual(defaults["primary_leverage"], 3)
        self.assertEqual(defaults["shadow_leverage"], [1, 2, 3, 5, 10])
        self.assertEqual(defaults["symbols"], ["BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "SEIUSDT"])
        defaults["max_leverage"] = 11
        with self.assertRaises(PaperTradingError):
            validate_experiment_config(defaults)

    def test_intent_rejects_quantity_leverage_and_unsafe_reduce_shapes(self):
        now = datetime.now(timezone.utc).isoformat()
        intent = {
            "schema_version": INTENT_SCHEMA_VERSION,
            "action": "open",
            "symbol": "BTCUSDT",
            "side": "long",
            "order_type": "market",
            "entry_price": 100,
            "stop_price": 95,
            "target_price": 110,
            "reduce_only": False,
            "reduce_fraction": None,
            "position_id": None,
            "reason": "fixture",
            "source_arm": "hybrid",
            "as_of": now,
        }
        self.assertEqual(TradingIntent.from_dict(intent).side, "long")
        with self.assertRaisesRegex(PaperTradingError, "credentials|unsupported fields"):
            TradingIntent.from_dict({**intent, "quantity": 100, "leverage": 10})
        with self.assertRaises(PaperTradingError):
            TradingIntent.from_dict({**intent, "action": "reduce", "reduce_only": False})

    def test_typesafe_request_has_choice_score_noul_and_normalizes_vector(self):
        snapshot = FixtureFuturesMarketDataProvider().fetch_snapshot(
            "BTCUSDT", datetime.now(timezone.utc)
        )
        features = compute_features(snapshot)
        questions = build_jev_questions(include_open_interest=True, include_spread=True)
        calls = []

        def transport(url, headers, body, timeout):
            calls.append((url, headers, json.loads(body), timeout))
            return json.dumps(make_type_safe_response(questions)).encode()

        provider = validate_provider_config(
            {
                "provider_id": "jev-test",
                "kind": "typesafe_jev",
                "display_name": "Jev test",
                "base_url": "https://api.typesafe.ai",
                "model": "jev-latest",
                "timeout_seconds": 8,
                "enabled": True,
                "credential_env": "UNIT_TEST_JEV_KEY",
            }
        )
        adapter = JevAdapter(
            provider,
            environ={"UNIT_TEST_JEV_KEY": UNIT_TOKEN},
            transport=transport,
        )
        vector = adapter.evaluate(snapshot, features, {})

        self.assertEqual(len(calls), 1)
        self.assertEqual(urlsplit(calls[0][0]).path, "/v1/systemone")
        self.assertEqual(calls[0][1]["Authorization"], f"Bearer {UNIT_TOKEN}")
        self.assertEqual(calls[0][2]["model"], "jev-latest")
        self.assertEqual(
            calls[0][2]["state"]["snapshot_hash"],
            jev_state(snapshot, features, {})["snapshot_hash"],
        )
        self.assertEqual({item["type"] for item in calls[0][2]["questions"].values()}, {"choice", "score", "noul"})
        self.assertEqual(vector["schema_version"], "jev-decision-vector.v1")
        self.assertEqual(
            vector["snapshot_hash"], jev_state(snapshot, features, {})["snapshot_hash"]
        )
        self.assertEqual(vector["answers"]["market_regime"]["value"], "bull")
        self.assertEqual(vector["answers"]["setup_quality"]["type"], "score")
        self.assertEqual(vector["answers"]["funding_concern"]["type"], "noul")
        self.assertEqual(vector["usage"]["input_tokens"], 32)
        self.assertEqual(vector["question_schema_version"], "jev-questions.v1")

    def test_jev_auth_timeout_rate_limit_and_malformed_errors_are_sanitized(self):
        questions = build_jev_questions(include_open_interest=False, include_spread=False)
        responses = make_type_safe_response(questions)
        provider = validate_provider_config(
            {
                "provider_id": "jev-errors",
                "kind": "typesafe_jev",
                "display_name": "Jev error test",
                "base_url": "https://api.typesafe.ai",
                "model": "jev-latest",
                "enabled": True,
                "credential_env": "UNIT_TEST_JEV_KEY",
            }
        )
        snapshot = FixtureFuturesMarketDataProvider().fetch_snapshot(
            "BTCUSDT", datetime.now(timezone.utc)
        )
        features = compute_features(snapshot)
        error_body = f"upstream body {UNIT_TOKEN}".encode()

        def http_error(status):
            return lambda url, _headers, _body, _timeout: (_ for _ in ()).throw(
                urllib.error.HTTPError(
                    url,
                    status,
                    f"provider detail {UNIT_TOKEN}",
                    {},
                    io.BytesIO(error_body),
                )
            )

        for status in (401, 429):
            adapter = JevAdapter(
                provider,
                environ={"UNIT_TEST_JEV_KEY": UNIT_TOKEN},
                transport=http_error(status),
            )
            with self.assertRaises(AIProviderError) as context:
                adapter.evaluate(snapshot, features, {})
            self.assertIn(str(status), str(context.exception))
            self.assertNotIn(UNIT_TOKEN, str(context.exception))

        adapter = JevAdapter(
            provider,
            environ={"UNIT_TEST_JEV_KEY": UNIT_TOKEN},
            transport=lambda *_: (_ for _ in ()).throw(TimeoutError(UNIT_TOKEN)),
        )
        with self.assertRaises(AIProviderError) as context:
            adapter.evaluate(snapshot, features, {})
        self.assertNotIn(UNIT_TOKEN, str(context.exception))

        adapter = JevAdapter(
            provider,
            environ={"UNIT_TEST_JEV_KEY": UNIT_TOKEN},
            transport=lambda *_: b"not-json",
        )
        with self.assertRaises(AIProviderError) as context:
            adapter.evaluate(snapshot, features, {})
        self.assertNotIn(UNIT_TOKEN, str(context.exception))

    def test_missing_env_reference_never_makes_provider_request(self):
        calls = []
        provider = validate_provider_config(
            {
                "provider_id": "jev-no-env",
                "kind": "typesafe_jev",
                "display_name": "Jev without env",
                "base_url": "https://api.typesafe.ai",
                "model": "jev-latest",
                "enabled": True,
                "credential_env": "MISSING_PAPER_TEST_KEY",
            }
        )
        snapshot = FixtureFuturesMarketDataProvider().fetch_snapshot(
            "BTCUSDT", datetime.now(timezone.utc)
        )
        adapter = JevAdapter(
            provider,
            environ={},
            transport=lambda *args: calls.append(args) or b"{}",
        )
        with self.assertRaisesRegex(AIProviderError, "no external model call"):
            adapter.evaluate(snapshot, compute_features(snapshot), {})
        self.assertEqual(calls, [])

    def test_typesafe_test_connection_validates_all_primitive_types(self):
        captured = []

        def transport(url, headers, body, timeout):
            request = json.loads(body)
            captured.append(request)
            return json.dumps(make_type_safe_response(request["questions"])).encode()

        provider = validate_provider_config(
            {
                "provider_id": "jev-connection-test",
                "kind": "typesafe_jev",
                "display_name": "Jev test connection",
                "base_url": "https://api.typesafe.ai",
                "model": "jev-latest",
                "enabled": True,
                "credential_env": "UNIT_TEST_JEV_KEY",
            }
        )
        result = JevAdapter(
            provider,
            environ={"UNIT_TEST_JEV_KEY": UNIT_TOKEN},
            transport=transport,
        ).test_connection()
        self.assertTrue(result["ok"])
        self.assertEqual(result["validated_question_types"], ["noul", "choice", "score"])
        self.assertEqual(
            {question["type"] for question in captured[0]["questions"].values()},
            {"noul", "choice", "score"},
        )

        raw_provider = validate_provider_config(
            {
                "provider_id": "jev-raw-auth",
                "kind": "typesafe_jev",
                "display_name": "Jev raw-header test",
                "base_url": "https://api.typesafe.ai",
                "model": "jev-latest",
                "auth_scheme": "raw",
                "enabled": True,
                "credential_env": "UNIT_TEST_JEV_KEY",
            }
        )
        auth_headers = []

        def raw_transport(url, headers, body, timeout):
            auth_headers.append(headers["Authorization"])
            request = json.loads(body)
            return json.dumps(make_type_safe_response(request["questions"])).encode()

        JevAdapter(
            raw_provider,
            environ={"UNIT_TEST_JEV_KEY": UNIT_TOKEN},
            transport=raw_transport,
        ).test_connection()
        self.assertEqual(auth_headers, [UNIT_TOKEN])

    def test_responses_foundry_endpoint_uses_api_key_header_and_structured_output(self):
        snapshot = FixtureFuturesMarketDataProvider().fetch_snapshot(
            "BTCUSDT", datetime.now(timezone.utc)
        )
        captured = []
        no_trade = {
            "action": "no_trade",
            "symbol": "BTCUSDT",
            "side": None,
            "entry_price": None,
            "stop_price": None,
            "target_price": None,
            "reason": "Connection validation only.",
        }

        def transport(url, headers, body, timeout):
            captured.append((url, headers, json.loads(body), timeout))
            return json.dumps(
                {
                    "status": "completed",
                    "output_text": json.dumps(no_trade),
                    "usage": {
                        "input_tokens": 8,
                        "output_tokens": 12,
                        "output_tokens_details": {"reasoning_tokens": 7},
                    },
                }
            ).encode()

        provider = validate_provider_config(
            {
                "provider_id": "azure-test",
                "kind": "foundry_responses",
                "display_name": "Azure test",
                "base_url": "https://resource.openai.azure.com/openai/v1/responses?api-version=2025-04-01-preview",
                "model": "deployment-name",
                "timeout_seconds": 9,
                "enabled": True,
                "credential_env": "UNIT_TEST_AZURE_KEY",
            }
        )
        adapter = ResponsesAdapter(
            provider,
            environ={"UNIT_TEST_AZURE_KEY": UNIT_TOKEN},
            transport=transport,
            skill_context="TEST SKILL CONTENT",
        )
        connection = adapter.test_connection()
        result = adapter.generate_intent(
            snapshot,
            compute_features(snapshot),
            {},
            jev_vector=None,
            source_arm="luna",
            include_skill=False,
        )
        self.assertTrue(connection["responses_compatible"])
        self.assertIsNone(result["intent"])
        self.assertEqual(result["usage"]["reasoning_tokens"], 7)
        self.assertEqual(len(captured), 2)
        self.assertEqual(urlsplit(captured[0][0]).path, "/openai/v1/responses")
        self.assertEqual(parse_qs(urlsplit(captured[0][0]).query)["api-version"], ["2025-04-01-preview"])
        self.assertEqual(captured[0][1]["api-key"], UNIT_TOKEN)
        self.assertNotIn(UNIT_TOKEN, json.dumps(captured[0][2]))
        self.assertEqual(captured[1][2]["text"]["format"]["type"], "json_schema")

    def test_responses_auth_timeout_and_invalid_structured_body_are_sanitized(self):
        provider = validate_provider_config(
            {
                "provider_id": "gpt-error-test",
                "kind": "openai_responses",
                "display_name": "OpenAI test adapter",
                "base_url": "https://api.openai.com/v1",
                "model": "test-model",
                "enabled": True,
                "credential_env": "UNIT_TEST_OPENAI_KEY",
            }
        )
        error_body = f"private provider response {UNIT_TOKEN}".encode()

        def fail(status):
            return lambda url, _headers, _body, _timeout: (_ for _ in ()).throw(
                urllib.error.HTTPError(url, status, UNIT_TOKEN, {}, io.BytesIO(error_body))
            )

        for status in (401, 429):
            with self.assertRaises(AIProviderError) as context:
                ResponsesAdapter(
                    provider,
                    environ={"UNIT_TEST_OPENAI_KEY": UNIT_TOKEN},
                    transport=fail(status),
                ).test_connection()
            self.assertIn(str(status), str(context.exception))
            self.assertNotIn(UNIT_TOKEN, str(context.exception))
        with self.assertRaises(AIProviderError) as context:
            ResponsesAdapter(
                provider,
                environ={"UNIT_TEST_OPENAI_KEY": UNIT_TOKEN},
                transport=lambda *_: (_ for _ in ()).throw(TimeoutError(UNIT_TOKEN)),
            ).test_connection()
        self.assertNotIn(UNIT_TOKEN, str(context.exception))
        with self.assertRaises(AIProviderError) as context:
            ResponsesAdapter(
                provider,
                environ={"UNIT_TEST_OPENAI_KEY": UNIT_TOKEN},
                transport=lambda *_: json.dumps(
                    {"status": "completed", "output_text": "not json"}
                ).encode(),
            ).test_connection()
        self.assertNotIn(UNIT_TOKEN, str(context.exception))

    def test_generic_provider_rejects_credential_urls_and_secret_fields(self):
        with self.assertRaisesRegex(PaperTradingError, "credential query"):
            validate_provider_config(
                {
                    "provider_id": "unsafe-url",
                    "kind": "compatible_responses",
                    "display_name": "Unsafe",
                    "base_url": "https://api.example.test/v1/responses?api_key=bad",
                    "model": "model",
                    "enabled": True,
                }
            )
        with self.assertRaisesRegex(PaperTradingError, "must not contain credentials"):
            validate_provider_config(
                {
                    "provider_id": "no-secrets",
                    "kind": "fixture_gpt",
                    "display_name": "Fixture",
                    "model": "fixture",
                    "enabled": True,
                    "api_key": UNIT_TOKEN,
                }
            )

    def test_escalation_threshold_and_fast_path_boundaries(self):
        snapshot = FixtureFuturesMarketDataProvider().fetch_snapshot(
            "BTCUSDT", datetime.now(timezone.utc)
        )
        features = compute_features(snapshot)
        vector = FixtureJevProvider().evaluate(snapshot, features, {})
        policy = default_experiment_config()["escalation_policy"]
        no_escalation = route_escalation(vector, features, {}, policy)
        self.assertFalse(no_escalation["escalate"])
        self.assertTrue(all(key in vector["answers"] for key in ("market_regime", "setup_quality", "breakout_valid")))

        policy_boundary = dict(policy)
        policy_boundary["minimum_confidence"] = 0.8
        confidence_at_boundary = json.loads(json.dumps(vector))
        for key in ("market_regime", "trend_alignment", "setup_quality"):
            confidence_at_boundary["answers"][key]["confidence"] = 0.8
        self.assertFalse(
            route_escalation(confidence_at_boundary, features, {}, policy_boundary)["escalate"]
        )
        confidence_below_boundary = json.loads(json.dumps(confidence_at_boundary))
        confidence_below_boundary["answers"]["setup_quality"]["confidence"] = 0.799
        self.assertIn(
            "low_critical_confidence",
            route_escalation(
                confidence_below_boundary, features, {}, policy_boundary
            )["reasons"],
        )

        conflicting = json.loads(json.dumps(vector))
        conflicting["answers"]["signal_conflict"]["value"] = policy["conflict_threshold"]
        boundary = route_escalation(conflicting, features, {}, policy)
        self.assertTrue(boundary["escalate"])
        self.assertIn("signal_conflict", boundary["reasons"])

    def test_outcome_fields_are_not_forwarded_to_gpt_input(self):
        snapshot = FixtureFuturesMarketDataProvider().fetch_snapshot(
            "BTCUSDT", datetime.now(timezone.utc)
        )
        features = {
            **compute_features(snapshot),
            "outcome": {"label": "future-label-never-forwarded"},
            "future_bars": [{"label": "future-label-never-forwarded"}],
        }
        state = build_gpt_input(snapshot, features, jev_vector=None, portfolio={})
        serialized = json.dumps(state)
        self.assertNotIn('"outcome":', serialized)
        self.assertNotIn("future_bars", serialized)
        self.assertNotIn("future-label-never-forwarded", serialized)
        self.assertNotIn("outcome", state["features"])
        self.assertNotIn("future_bars", state["features"])


class PaperFuturesMarketDataTests(unittest.TestCase):
    def test_public_history_provider_pages_exact_closed_ranges_without_live_io(self):
        as_of = datetime(2026, 8, 22, 13, 17, 31, tzinfo=timezone.utc)
        cutoff = floor_time(as_of, "15m")
        requested_bars = 1005
        interval_seconds = 900
        start = cutoff - timedelta(seconds=requested_bars * interval_seconds)
        calls = []

        def transport(url, _timeout):
            parsed = urlsplit(url)
            query = parse_qs(parsed.query)
            calls.append((parsed.path, query))
            self.assertEqual(parsed.path, "/fapi/v1/klines")
            cursor = int(query["startTime"][0])
            limit = int(query["limit"][0])
            rows = []
            for index in range(limit):
                opened_ms = cursor + index * interval_seconds * 1000
                price = 100 + (len(calls) * 1000 + index) * 0.001
                rows.append(
                    [
                        opened_ms,
                        str(price),
                        str(price + 0.1),
                        str(price - 0.1),
                        str(price + 0.01),
                        "2",
                        opened_ms + interval_seconds * 1000 - 1,
                    ]
                )
            return json.dumps(rows).encode()

        provider = BinanceUsdMFuturesMarketDataProvider(transport=transport)
        candles = provider.fetch_history(
            "BTCUSDT",
            "15m",
            bars=requested_bars,
            as_of=as_of,
        )
        self.assertEqual(len(candles), requested_bars)
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            datetime.fromisoformat(candles[0]["open_time"].replace("Z", "+00:00")),
            start,
        )
        self.assertEqual(
            datetime.fromisoformat(candles[-1]["close_time"].replace("Z", "+00:00")),
            cutoff,
        )

    def test_monitor_backfills_closed_bars_in_pages_and_uses_historical_funding(self):
        after = datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc)
        as_of = after + timedelta(minutes=1005)
        calls = []

        def transport(url, _timeout):
            parsed = urlsplit(url)
            query = parse_qs(parsed.query)
            calls.append((parsed.path, query))
            if parsed.path == "/fapi/v1/klines":
                start = int(query["startTime"][0])
                limit = int(query["limit"][0])
                rows = []
                for index in range(limit):
                    open_ms = start + index * 60_000
                    price = 100 + index / 1000
                    rows.append(
                        [
                            open_ms,
                            str(price),
                            str(price + 0.1),
                            str(price - 0.1),
                            str(price + 0.01),
                            "5",
                            open_ms + 59_999,
                        ]
                    )
                return json.dumps(rows).encode()
            if parsed.path == "/fapi/v1/fundingRate":
                observed = int((after + timedelta(minutes=480)).timestamp() * 1000)
                return json.dumps(
                    [
                        {
                            "symbol": "BTCUSDT",
                            "fundingTime": observed,
                            "fundingRate": "0.0001",
                        }
                    ]
                ).encode()
            raise AssertionError(f"unexpected endpoint {parsed.path}")

        provider = BinanceUsdMFuturesMarketDataProvider(transport=transport)
        bars = provider.fetch_monitor_bars("BTCUSDT", after, as_of)
        self.assertEqual(len(bars), 1005)
        self.assertEqual(len([call for call in calls if call[0] == "/fapi/v1/klines"]), 2)
        self.assertEqual(
            datetime.fromisoformat(bars[0]["open_time"].replace("Z", "+00:00")),
            after,
        )
        self.assertEqual(
            datetime.fromisoformat(bars[-1]["close_time"].replace("Z", "+00:00")),
            as_of,
        )
        funding = provider.fetch_funding_rate(
            "BTCUSDT", after + timedelta(minutes=481)
        )
        self.assertAlmostEqual(funding, 0.0001)
        self.assertEqual(calls[-1][0], "/fapi/v1/fundingRate")

    def test_binance_symbol_support_comes_from_exchange_metadata(self):
        def transport(url, _timeout):
            self.assertEqual(urlsplit(url).path, "/fapi/v1/exchangeInfo")
            return json.dumps(
                {
                    "symbols": [
                        {
                            "symbol": "BTCUSDT",
                            "status": "TRADING",
                            "contractType": "PERPETUAL",
                            "quoteAsset": "USDT",
                        },
                        {
                            "symbol": "OLDUSDT",
                            "status": "BREAK",
                            "contractType": "PERPETUAL",
                            "quoteAsset": "USDT",
                        },
                    ]
                }
            ).encode()

        support = BinanceUsdMFuturesMarketDataProvider(
            transport=transport
        ).validate_symbols(["BTCUSDT", "OLDUSDT", "UNKNOWNUSDT"])
        self.assertEqual(support["supported"], ["BTCUSDT"])
        self.assertEqual(
            [row["symbol"] for row in support["excluded"]],
            ["OLDUSDT", "UNKNOWNUSDT"],
        )

    def test_binance_usdm_market_data_uses_interval_closed_cutoffs(self):
        as_of = datetime(2026, 8, 19, 9, 27, tzinfo=timezone.utc)
        cutoff = floor_time(as_of, "15m")
        calls = []

        def transport(url, _timeout):
            parsed = urlsplit(url)
            query = parse_qs(parsed.query)
            calls.append((parsed.path, query))
            if parsed.path == "/fapi/v1/klines":
                interval = query["interval"][0]
                seconds = {"1m": 60, "15m": 900, "1h": 3600, "4h": 14400}[interval]
                end_boundary = datetime.fromtimestamp(
                    (int(query["endTime"][0]) + 1) / 1000, tz=timezone.utc
                )
                limit = int(query["limit"][0])
                rows = []
                for index in range(limit):
                    close_at = end_boundary - timedelta(seconds=(limit - index - 1) * seconds)
                    open_at = close_at - timedelta(seconds=seconds)
                    price = 100 + index * 0.01
                    rows.append(
                        [
                            int(open_at.timestamp() * 1000),
                            str(price),
                            str(price * 1.01),
                            str(price * 0.99),
                            str(price * 1.005),
                            "10",
                            int(close_at.timestamp() * 1000) - 1,
                        ]
                    )
                return json.dumps(rows).encode()
            if parsed.path == "/fapi/v1/premiumIndex":
                return json.dumps(
                    {
                        "time": int(cutoff.timestamp() * 1000) - 60_000,
                        "lastFundingRate": "0.0001",
                    }
                ).encode()
            if parsed.path == "/futures/data/openInterestHist":
                end_ms = int(query["endTime"][0]) + 1
                cutoff_time = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
                return json.dumps(
                    [
                        {
                            "timestamp": int((cutoff_time - timedelta(hours=3)).timestamp() * 1000),
                            "sumOpenInterest": "100",
                        },
                        {
                            "timestamp": int((cutoff_time - timedelta(hours=2)).timestamp() * 1000),
                            "sumOpenInterest": "110",
                        },
                    ]
                ).encode()
            raise AssertionError(f"unexpected public endpoint {parsed.path}")

        provider = BinanceUsdMFuturesMarketDataProvider(transport=transport)
        snapshot = provider.fetch_snapshot("BTCUSDT", as_of)

        self.assertEqual(snapshot.data_origin, "BINANCE_USDM_PUBLIC")
        self.assertLessEqual(datetime.fromisoformat(snapshot.candles_1h[-1]["close_time"].replace("Z", "+00:00")), cutoff)
        self.assertEqual(
            datetime.fromisoformat(snapshot.candles_15m[-1]["close_time"].replace("Z", "+00:00")),
            cutoff,
        )
        self.assertAlmostEqual(snapshot.open_interest_change_1h, 0.1)
        one_hour_call = next(query for path, query in calls if path == "/fapi/v1/klines" and query["interval"] == ["1h"])
        expected_end = floor_time(cutoff, "1h")
        self.assertEqual(int(one_hour_call["endTime"][0]) + 1, int(expected_end.timestamp() * 1000))

    def test_public_market_transport_errors_are_generic(self):
        provider = BinanceUsdMFuturesMarketDataProvider(
            transport=lambda *_: (_ for _ in ()).throw(TimeoutError(UNIT_TOKEN))
        )
        with self.assertRaises(MarketDataError) as context:
            provider.fetch_snapshot("BTCUSDT", datetime.now(timezone.utc))
        self.assertNotIn(UNIT_TOKEN, str(context.exception))


class PaperFuturesRuntimeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.clock = FixedClock(self.now)
        self.store = PaperStore(":memory:")
        self.config = saved_config(self.store, force=True)

    def tearDown(self):
        self.store.close()

    def test_market_to_escalation_fill_funding_exit_metrics_and_export(self):
        config = self.store.experiment()["config"]
        config["evaluation_arms"] = ["quant", "jev", "luna", "luna_skill", "quant_jev", "hybrid"]
        config["primary_arm"] = "hybrid"
        config["force_escalation"] = True
        self.store.save_experiment(config)

        # Build a funding-boundary bar followed by an adverse stop bar; these bars
        # are handed only to the position monitor, never to an AI adapter.
        snapshot = FixtureFuturesMarketDataProvider().fetch_snapshot("BTCUSDT", self.now)
        features = compute_features(snapshot)
        entry = features["last_price"]
        stop_distance = 1.5 * features["atr"]
        stop = entry - stop_distance
        opened = self.now
        next_settlement = (
            int(opened.timestamp()) // (8 * 60 * 60) + 1
        ) * (8 * 60 * 60)
        settlement = datetime.fromtimestamp(next_settlement, tz=timezone.utc)
        settle_bar = {
            "open_time": (settlement - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            "close_time": settlement.isoformat().replace("+00:00", "Z"),
            "open": entry,
            "high": entry + stop_distance * 0.1,
            "low": entry - stop_distance * 0.1,
            "close": entry,
            "volume": 5,
        }
        exit_time = settlement + timedelta(minutes=1)
        stop_bar = {
            "open_time": settlement.isoformat().replace("+00:00", "Z"),
            "close_time": exit_time.isoformat().replace("+00:00", "Z"),
            "open": entry,
            "high": entry + stop_distance * 0.1,
            "low": stop - stop_distance * 0.1,
            "close": stop - stop_distance * 0.05,
            "volume": 5,
        }
        market = FixtureFuturesMarketDataProvider(
            future_path={"BTCUSDT": [settle_bar, stop_bar]}
        )
        jev = CountingJevProvider()
        gpt = CountingGPTProvider()
        runtime = PaperRuntime(
            self.store,
            market_provider=market,
            jev_provider=jev,
            gpt_provider=gpt,
            clock=self.clock,
        )
        runtime.start()
        cycle = runtime.run_cycle("BTCUSDT", as_of=self.now, manual=True)

        self.assertTrue(cycle["quant_gate"]["eligible"])
        self.assertEqual(cycle["features"]["quant_direction"], "long")
        self.assertEqual(jev.calls, 1)
        self.assertEqual({call["arm"] for call in gpt.calls}, {"luna", "luna_skill", "hybrid"})
        self.assertEqual(sum(call["path"] == "jev" for call in self.store.ai_usage("EXP-001")), 1)
        self.assertEqual(
            sum(call["path"].startswith("gpt:") for call in self.store.ai_usage("EXP-001")),
            3,
        )
        hashes = {arm["snapshot_hash"] for arm in cycle["arms"].values()}
        self.assertEqual(hashes, {cycle["snapshot_hash"]})
        decision_hashes = {
            arm["decision_input_hash"] for arm in cycle["arms"].values()
        }
        self.assertEqual(decision_hashes, {cycle["decision_input_hash"]})
        self.assertEqual(
            cycle["jev_vector"]["snapshot_hash"], cycle["decision_input_hash"]
        )
        self.assertTrue(cycle["arms"]["hybrid"]["escalation"]["escalate"])
        self.assertEqual(cycle["primary_decision"]["intent"]["source_arm"], "hybrid")
        self.assertTrue(
            all(
                "future-label-never-forwarded" not in json.dumps(call["input"])
                and "outcome" not in call["input"]["features"]
                for call in gpt.calls
            )
        )

        duplicate = runtime.run_cycle("BTCUSDT", as_of=self.now, manual=True)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(jev.calls, 1)
        self.assertEqual(len(gpt.calls), 3)

        self.assertEqual(
            sum(item["status"] == "filled" for item in cycle["executions"]),
            12,
        )
        primary = next(
            item for item in self.store.open_positions("EXP-001") if item["cohort"] == "primary"
        )
        self.assertEqual(primary["side"], "long")
        self.assertLess(primary["quantity"], 1)
        ai_call_count_before_monitor = len(self.store.ai_usage("EXP-001"))
        events = runtime.monitor_once(as_of=exit_time + timedelta(minutes=1))
        self.assertEqual(len(self.store.ai_usage("EXP-001")), ai_call_count_before_monitor)
        self.assertTrue(any(event["event"] == "stop" for event in events))
        closed = [
            item
            for item in self.store.list_positions("EXP-001", cohort="primary")
            if item["status"] == "closed"
        ]
        self.assertEqual(len(closed), 1)
        realized = closed[0]["realized_pnl"]
        self.assertTrue(closed[0]["closed_pnl_recorded"])
        self.assertNotEqual(closed[0]["funding_paid"], 0)
        self.assertGreater(closed[0]["entry_fee"] + closed[0]["exit_fees"], 0)
        self.assertGreater(closed[0]["slippage_paid"], 0)
        runtime.monitor_once(as_of=exit_time + timedelta(minutes=2))
        still_closed = [
            item
            for item in self.store.list_positions("EXP-001", cohort="primary")
            if item["status"] == "closed"
        ]
        self.assertEqual(still_closed[0]["realized_pnl"], realized)
        self.assertEqual(still_closed[0]["closed_pnl_recorded"], 1)

        metrics = runtime.metrics()
        self.assertTrue(metrics["reconciliation"]["ending_equity_equals_cash_plus_unrealized"])
        self.assertTrue(metrics["reconciliation"]["closed_pnl_matches_closed_trade_ledger"])
        self.assertEqual(metrics["portfolio"]["closed_trade_count"], 1)
        self.assertAlmostEqual(
            metrics["portfolio"]["net_pnl_usdt"],
            metrics["portfolio"]["cash_balance_usdt"] - 100,
            places=7,
        )

        archive_bytes, filename = runtime.export_bundle()
        self.assertTrue(filename.endswith(".zip"))
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            names = set(archive.namelist())
            self.assertTrue(
                {
                    "manifest.json",
                    "config.json",
                    "summary.md",
                    "metrics.json",
                    "trades.csv",
                    "positions.csv",
                    "orders.csv",
                    "wallets.csv",
                    "fills.csv",
                    "equity.csv",
                    "pnl-by-day.csv",
                    "pnl-by-asset.csv",
                    "pnl-by-leverage.csv",
                    "decisions.jsonl",
                    "signals.jsonl",
                    "risk-events.csv",
                    "jev-decisions.jsonl",
                    "escalation-events.jsonl",
                    "ai-usage.csv",
                    "ai-cost-by-provider.csv",
                    "latency.csv",
                    "market-history.csv",
                    "market-snapshots.jsonl",
                    "warmup-bars.jsonl",
                }.issubset(names)
            )
            bundle_text = "".join(
                archive.read(name).decode("utf-8")
                for name in names
                if not name.endswith(".zip")
            )
            self.assertNotIn("credential_env", bundle_text)
            self.assertNotIn(UNIT_TOKEN, bundle_text)
            exported_metrics = json.loads(archive.read("metrics.json"))
            jev_records = [
                json.loads(line)
                for line in archive.read("jev-decisions.jsonl").decode("utf-8").splitlines()
                if line
            ]
            self.assertEqual(len(jev_records), 1)
            self.assertIn(
                "probabilities",
                json.dumps(jev_records[0]["decision_vector"]["answers"]),
            )
            trades = list(
                csv.DictReader(io.StringIO(archive.read("trades.csv").decode("utf-8")))
            )
            primary_trades = [trade for trade in trades if trade["cohort"] == "primary"]
            self.assertEqual(len(primary_trades), 1)
            self.assertAlmostEqual(
                sum(float(trade["realized_pnl"]) for trade in primary_trades),
                exported_metrics["portfolio"]["realized_pnl_usdt"],
                places=7,
            )
            self.assertTrue(exported_metrics["reconciliation"]["ending_equity_equals_cash_plus_unrealized"])

    def test_risk_rejection_cannot_create_order_or_fill(self):
        config = self.store.experiment()["config"]
        snapshot = FixtureFuturesMarketDataProvider().fetch_snapshot("BTCUSDT", self.now)
        features = compute_features(snapshot)
        intent = TradingIntent.from_dict(
            build_fast_intent(
                snapshot,
                features,
                side="long",
                source_arm="hybrid",
                reason="unit test",
            )
        )
        decision = RiskEngine().evaluate(
            intent,
            config,
            equity=100,
            margin_used=0,
            open_positions=[],
            data_cutoff=snapshot.data_cutoff,
            decision_as_of=self.now,
            signal_approved=False,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "SIGNAL_GATE_BLOCKED")
        cycle_id = "risk-reject:BTCUSDT:cycle"
        self.store.begin_cycle(
            cycle_id,
            "EXP-001",
            "BTCUSDT",
            snapshot.data_cutoff,
            snapshot.snapshot_hash,
            snapshot.as_of,
            snapshot.data_origin,
        )
        self.store.complete_cycle(
            cycle_id,
            {
                "cycle_id": cycle_id,
                "as_of": snapshot.as_of,
                "status": "complete",
                "primary_decision": {},
            },
            ai_calls=[],
            executions=[
                {
                    "cohort": "primary",
                    "risk": decision.to_dict(),
                    "intent": intent.to_dict(),
                    "reference_price": features["last_price"],
                    "slippage_bps": config["slippage_bps"],
                    "fee_rate": config["taker_fee_rate"],
                    "as_of": snapshot.as_of,
                    "data_origin": snapshot.data_origin,
                }
            ],
            risk_events=[],
            data_origin=snapshot.data_origin,
        )
        records = self.store.export_records("EXP-001")
        self.assertEqual(records["fills"], [])
        self.assertEqual(records["positions"], [])
        self.assertEqual(records["risk_events"][0]["status"], "blocked")

    def test_margin_sizing_rejects_dust_below_minimum_notional(self):
        config = self.store.experiment()["config"]
        snapshot = FixtureFuturesMarketDataProvider().fetch_snapshot("BTCUSDT", self.now)
        intent = TradingIntent.from_dict(
            {
                "schema_version": INTENT_SCHEMA_VERSION,
                "action": "open",
                "symbol": "BTCUSDT",
                "side": "long",
                "order_type": "market",
                "entry_price": 60_000.0,
                "stop_price": 45_000.0,
                "target_price": 90_000.0,
                "reduce_only": False,
                "reduce_fraction": None,
                "position_id": None,
                "reason": "minimum notional boundary",
                "source_arm": "quant",
                "as_of": snapshot.data_cutoff,
            }
        )
        risk = RiskEngine().evaluate(
            intent,
            config,
            equity=100,
            margin_used=0,
            open_positions=[],
            data_cutoff=snapshot.data_cutoff,
            decision_as_of=self.now,
            signal_approved=True,
            current_price=60_000.0,
            current_atr=500.0,
        )
        self.assertFalse(risk.allowed)
        self.assertEqual(risk.code, "MINIMUM_NOTIONAL")

    def test_daily_loss_drawdown_and_leverage_caps_fail_closed(self):
        config = self.store.experiment()["config"]
        snapshot = FixtureFuturesMarketDataProvider().fetch_snapshot("BTCUSDT", self.now)
        features = compute_features(snapshot)
        intent = TradingIntent.from_dict(
            build_fast_intent(
                snapshot,
                features,
                side="long",
                source_arm="quant",
                reason="risk stop boundary",
            )
        )
        engine = RiskEngine()
        kwargs = {
            "equity": 100,
            "margin_used": 0,
            "open_positions": [],
            "data_cutoff": snapshot.data_cutoff,
            "decision_as_of": self.now,
            "signal_approved": True,
            "current_price": features["last_price"],
            "current_atr": features["atr"],
            "last_bar_close_time": features["last_bar_close_time"],
        }
        daily = engine.evaluate(intent, config, **kwargs, daily_loss=config["max_daily_loss"])
        drawdown = engine.evaluate(
            intent,
            config,
            **kwargs,
            drawdown=config["max_drawdown_stop"],
        )
        leverage = engine.evaluate(intent, config, **kwargs, leverage=11)
        self.assertEqual(daily.code, "DAILY_LOSS_LIMIT")
        self.assertEqual(drawdown.code, "DRAWDOWN_LIMIT")
        self.assertEqual(leverage.code, "LEVERAGE_LIMIT")

    def test_exp001_fixture_warmup_archives_point_in_time_lanes_idempotently(self):
        config = default_experiment_config()
        config["symbols"] = ["BTCUSDT"]
        self.store.save_experiment(config)
        runtime = PaperRuntime(
            self.store,
            market_provider=FixtureFuturesMarketDataProvider(),
            clock=self.clock,
        )
        result = runtime.warm_up_market_history(as_of=self.now)
        expected_bars = sum(WARMUP_PROFILES["EXP-001"].values())
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["completed_lane_count"], 4)
        self.assertEqual(result["retrieved_bars"], expected_bars)
        self.assertEqual(result["inserted_bars"], expected_bars)
        self.assertEqual(result["data_origin"], "FIXTURE")
        for lane in result["history"]["lanes"]:
            self.assertEqual(lane["stored_bars"], WARMUP_PROFILES["EXP-001"][lane["interval"]])
            self.assertLessEqual(
                parse_timestamp(lane["last_close_time"], "warmup.last_close_time"),
                self.now,
            )

        repeated = runtime.warm_up_market_history(as_of=self.now)
        self.assertEqual(repeated["retrieved_bars"], expected_bars)
        self.assertEqual(repeated["inserted_bars"], 0)
        self.assertEqual(len(repeated["history"]["runs"]), 4)

        archive, _ = runtime.export_bundle()
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            self.assertIn("market-history.csv", bundle.namelist())
            self.assertIn("warmup-bars.jsonl", bundle.namelist())
            warmup_rows = [
                json.loads(line)
                for line in bundle.read("warmup-bars.jsonl").decode("utf-8").splitlines()
                if line
            ]
            self.assertEqual(len(warmup_rows), expected_bars)
            self.assertTrue(
                all(
                    parse_timestamp(row["close_time"], "export.close_time") <= self.now
                    for row in warmup_rows
                )
            )
            market_history = json.loads(
                bundle.read("manifest.json").decode("utf-8")
            )
        self.assertEqual(market_history["warmup_lanes"], 4)
        self.assertEqual(market_history["warmup_requested_bars"], expected_bars)
        self.assertEqual(market_history["market_history_stored_bars"], expected_bars)

    def test_archived_warmup_history_is_consumed_by_quant_features_and_hashed(self):
        config = default_experiment_config()
        config["symbols"] = ["BTCUSDT"]
        config["evaluation_arms"] = ["quant"]
        config["primary_arm"] = "quant"
        self.store.save_experiment(config)
        provider = FixtureFuturesMarketDataProvider()
        runtime = PaperRuntime(
            self.store,
            market_provider=provider,
            clock=self.clock,
        )
        warmup = runtime.warm_up_market_history(as_of=self.now)
        raw_snapshot = provider.fetch_snapshot("BTCUSDT", self.now)
        without_archive = compute_features(raw_snapshot)
        runtime.start()
        cycle = runtime.run_cycle("BTCUSDT", as_of=self.now, manual=True)
        features = cycle["features"]
        self.assertEqual(warmup["status"], "complete")
        self.assertGreater(features["feature_history_15m_bars"], 0)
        self.assertGreater(features["feature_history_1h_bars"], 0)
        self.assertGreater(features["feature_history_4h_bars"], 0)
        self.assertNotEqual(features["feature_history_hash"], digest({}))
        self.assertEqual(
            cycle["decision_input_hash"],
            cycle["arms"]["quant"]["decision_input_hash"],
        )
        self.assertEqual(
            features["feature_history_15m_bars"],
            WARMUP_PROFILES["EXP-001"]["15m"] - len(raw_snapshot.candles_15m),
        )
        self.assertEqual(
            features["last_price"], without_archive["last_price"]
        )

    def test_loopback_warmup_endpoints_return_fixture_archive_status(self):
        config = default_experiment_config()
        config["symbols"] = ["BTCUSDT"]
        self.store.save_experiment(config)
        runtime = PaperRuntime(
            self.store,
            market_provider=FixtureFuturesMarketDataProvider(),
            clock=self.clock,
        )
        scheduler = PaperScheduler(runtime)
        server = PaperHTTPServer(("127.0.0.1", 0), PaperRequestHandler, runtime, scheduler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(scheduler.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join, 5)
        self.addCleanup(server.shutdown)
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        request = urllib.request.Request(
            f"{base_url}/api/market-data/warm-up",
            data=json.dumps({"profile": "EXP-001"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            warmed = json.loads(response.read())
        with urllib.request.urlopen(
            f"{base_url}/api/market-data/status", timeout=5
        ) as response:
            status = json.loads(response.read())
        self.assertEqual(warmed["result"]["status"], "complete")
        self.assertEqual(warmed["result"]["completed_lane_count"], 4)
        self.assertEqual(len(status["history"]["lanes"]), 4)

    def test_loopback_jev_test_connection_resolves_env_server_side_with_mock_transport(self):
        env_path = ROOT / f".test-env-provider-{os.getpid()}"
        env_path.write_text(
            f"TYPESAFE_API_KEY={ENV_SENTINEL}\n",
            encoding="utf-8",
        )
        self.addCleanup(env_path.unlink, missing_ok=True)
        local_environment: dict[str, str] = {}
        self.assertEqual(load_environment_file(env_path, environ=local_environment), 1)
        provider = {
            "provider_id": "jev-loopback-mock",
            "kind": "typesafe_jev",
            "display_name": "Mocked Jev connection",
            "base_url": "https://api.typesafe.ai",
            "model": "jev-latest",
            "enabled": True,
            "credential_env": "TYPESAFE_API_KEY",
        }
        self.store.save_provider(provider)
        runtime = PaperRuntime(self.store, clock=self.clock)
        scheduler = PaperScheduler(runtime)
        server = PaperHTTPServer(("127.0.0.1", 0), PaperRequestHandler, runtime, scheduler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(scheduler.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join, 5)
        self.addCleanup(server.shutdown)
        requests = []

        def mocked_post(url, headers, body, timeout):
            request_data = json.loads(body)
            requests.append(
                {
                    "url": url,
                    "authorization": headers.get("Authorization"),
                    "body": request_data,
                }
            )
            return json.dumps(
                make_type_safe_response(request_data["questions"])
            ).encode("utf-8")

        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/providers/"
            "jev-loopback-mock/test",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        logs = io.StringIO()
        with patch.dict(os.environ, local_environment), patch(
            "crypto_eval.paper_ai._post_json", mocked_post
        ), contextlib.redirect_stdout(logs), contextlib.redirect_stderr(logs):
            with urllib.request.urlopen(request, timeout=5) as response:
                response_body = response.read().decode("utf-8")

        self.assertEqual(len(requests), 1)
        self.assertEqual(
            requests[0]["authorization"], f"Bearer {ENV_SENTINEL}"
        )
        self.assertIn("/v1/systemone", requests[0]["url"])
        self.assertNotIn(ENV_SENTINEL, response_body)
        self.assertNotIn("credential_env", response_body)
        self.assertNotIn(ENV_SENTINEL, logs.getvalue())
        self.assertEqual(
            self.store.provider_validation_status("jev-loopback-mock"),
            "passed",
        )

    def test_reduce_only_clips_to_open_position_and_never_increases_exposure(self):
        runtime = PaperRuntime(
            self.store,
            market_provider=FixtureFuturesMarketDataProvider(),
            clock=self.clock,
        )
        runtime.start()
        cycle = runtime.run_cycle("BTCUSDT", as_of=self.now, manual=True)
        position = next(
            item for item in self.store.open_positions("EXP-001") if item["cohort"] == "primary"
        )
        before = position["quantity"]
        intent = TradingIntent.from_dict(
            {
                "schema_version": INTENT_SCHEMA_VERSION,
                "action": "reduce",
                "symbol": position["symbol"],
                "side": position["side"],
                "order_type": "market",
                "entry_price": None,
                "stop_price": None,
                "target_price": None,
                "reduce_only": True,
                "reduce_fraction": 0.5,
                "position_id": position["position_id"],
                "reason": "partial close",
                "source_arm": "hybrid",
                "as_of": self.now.isoformat(),
            }
        )
        wallet = self.store.wallet_summary("EXP-001", "primary")
        risk = RiskEngine().evaluate(
            intent,
            self.store.experiment()["config"],
            equity=wallet["equity"],
            margin_used=wallet["margin_used"],
            open_positions=self.store.open_positions("EXP-001"),
            data_cutoff=cycle["data_cutoff"],
            decision_as_of=self.now,
            position_lookup=self.store._position_lookup("EXP-001"),
        )
        self.assertTrue(risk.allowed)
        self.assertTrue(risk.reduce_only)
        result = self.store.execute_reduce(
            "EXP-001",
            intent,
            risk,
            mark_price=position["entry_price"],
            fee_rate=0.0004,
            as_of=(self.now + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            data_origin="FIXTURE",
        )
        remaining = next(
            item
            for item in self.store.open_positions("EXP-001")
            if item["position_id"] == position["position_id"]
        )
        self.assertTrue(result["accepted"])
        self.assertLessEqual(result["filled_quantity"], before)
        self.assertAlmostEqual(remaining["quantity"], before - result["filled_quantity"])

    def test_limit_fills_are_bounded_and_leverage_calculation_is_isolated(self):
        config = self.store.experiment()["config"]
        self.store.set_status("running")
        snapshot = FixtureFuturesMarketDataProvider().fetch_snapshot("BTCUSDT", self.now)
        features = compute_features(snapshot)
        intent_value = build_fast_intent(
            snapshot,
            features,
            side="long",
            source_arm="quant",
            reason="limit fill test",
        )
        intent_value["order_type"] = "limit"
        intent = TradingIntent.from_dict(intent_value)
        risk = RiskEngine().evaluate(
            intent,
            config,
            equity=100,
            margin_used=0,
            open_positions=[],
            data_cutoff=snapshot.data_cutoff,
            decision_as_of=self.now,
            signal_approved=True,
        )
        self.assertTrue(risk.allowed)
        liq = liquidation_price(
            intent.entry_price,
            intent.side,
            3,
            config["maintenance_margin_rate"],
        )
        self.assertLess(liq, intent.stop_price)
        cycle_id = "limit-fill:BTCUSDT:cycle"
        self.store.begin_cycle(
            cycle_id,
            "EXP-001",
            "BTCUSDT",
            snapshot.data_cutoff,
            snapshot.snapshot_hash,
            snapshot.as_of,
            snapshot.data_origin,
        )
        pending_cycle = self.store.complete_cycle(
            cycle_id,
            {"cycle_id": cycle_id, "as_of": snapshot.as_of, "status": "complete"},
            ai_calls=[],
            executions=[
                {
                    "cohort": "primary",
                    "risk": risk.to_dict(),
                    "intent": intent.to_dict(),
                    "reference_price": intent.entry_price,
                    "slippage_bps": 0,
                    "fee_rate": config["taker_fee_rate"],
                    "as_of": snapshot.as_of,
                    "data_origin": snapshot.data_origin,
                }
            ],
            risk_events=[],
            data_origin=snapshot.data_origin,
        )
        self.assertEqual(pending_cycle["executions"][0]["status"], "pending")
        opened = datetime.fromisoformat(snapshot.data_cutoff.replace("Z", "+00:00"))
        bar_close = opened + timedelta(minutes=1)
        bar = {
            "open_time": opened.isoformat().replace("+00:00", "Z"),
            "close_time": bar_close.isoformat().replace("+00:00", "Z"),
            "open": intent.entry_price + 0.1,
            "high": intent.entry_price + 1,
            "low": intent.entry_price - 0.2,
            "close": intent.entry_price + 0.2,
            "volume": 1,
        }
        self.store.apply_monitor_bar(
            "EXP-001",
            "BTCUSDT",
            bar,
            funding_rate=None,
            taker_fee_rate=config["taker_fee_rate"],
            data_origin="FIXTURE",
        )
        fills = self.store.export_records("EXP-001")["fills"]
        self.assertEqual(len(fills), 1)
        self.assertLessEqual(fills[0]["quantity"], risk.quantity)

    def test_background_monitor_fills_pending_limit_without_a_browser_or_model_call(self):
        config = default_experiment_config()
        config["evaluation_arms"] = ["quant"]
        config["primary_arm"] = "quant"
        config["entry_order_type"] = "limit"
        self.store.save_experiment(config)
        self.store.set_status("running")
        snapshot = FixtureFuturesMarketDataProvider().fetch_snapshot("BTCUSDT", self.now)
        features = compute_features(snapshot)
        future_close = self.now + timedelta(minutes=1)
        future_bar = {
            "open_time": (future_close - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            "close_time": future_close.isoformat().replace("+00:00", "Z"),
            "open": features["last_price"],
            "high": features["last_price"] * 1.0001,
            "low": features["last_price"] * 0.999,
            "close": features["last_price"] * 0.9995,
            "volume": 4,
        }
        jev = CountingJevProvider()
        market = FixtureFuturesMarketDataProvider(
            future_path={"BTCUSDT": [future_bar]}
        )
        runtime = PaperRuntime(
            self.store,
            market_provider=market,
            jev_provider=jev,
            clock=self.clock,
        )
        cycle = runtime.run_cycle("BTCUSDT", as_of=self.now, manual=True)
        self.assertEqual(cycle["executions"][0]["status"], "pending")
        self.assertEqual(self.store.pending_orders("EXP-001")[0]["order_type"], "limit")
        before_ai_calls = jev.calls
        events = runtime.monitor_once(as_of=future_close + timedelta(seconds=1))
        self.assertTrue(any(event["event"] == "limit_filled" for event in events))
        self.assertEqual(jev.calls, before_ai_calls)
        position = next(
            item
            for item in self.store.open_positions("EXP-001")
            if item["cohort"] == "primary"
        )
        self.assertGreater(position["quantity"], 0)
        self.assertGreaterEqual(position["quantity"] * position["entry_price"], 5)

    def test_short_position_uses_isolated_liquidation_and_conservative_stop_fill(self):
        config = self.store.experiment()["config"]
        self.store.set_status("running")
        snapshot = FixtureFuturesMarketDataProvider().fetch_snapshot("BTCUSDT", self.now)
        features = compute_features(snapshot)
        intent = TradingIntent.from_dict(
            build_fast_intent(
                snapshot,
                features,
                side="short",
                source_arm="quant",
                reason="short-side mechanics test",
            )
        )
        risk = RiskEngine().evaluate(
            intent,
            config,
            equity=100,
            margin_used=0,
            open_positions=[],
            data_cutoff=snapshot.data_cutoff,
            decision_as_of=self.now,
            signal_approved=True,
        )
        self.assertTrue(risk.allowed)
        self.assertGreater(risk.liquidation_price, intent.stop_price)
        cycle_id = "short-fill:BTCUSDT:cycle"
        self.store.begin_cycle(
            cycle_id,
            "EXP-001",
            "BTCUSDT",
            snapshot.data_cutoff,
            snapshot.snapshot_hash,
            snapshot.as_of,
            snapshot.data_origin,
        )
        self.store.complete_cycle(
            cycle_id,
            {"cycle_id": cycle_id, "as_of": snapshot.as_of, "status": "complete"},
            ai_calls=[],
            executions=[
                {
                    "cohort": "primary",
                    "risk": risk.to_dict(),
                    "intent": intent.to_dict(),
                    "reference_price": intent.entry_price,
                    "slippage_bps": config["slippage_bps"],
                    "fee_rate": config["taker_fee_rate"],
                    "as_of": snapshot.as_of,
                    "data_origin": "FIXTURE",
                }
            ],
            risk_events=[],
            data_origin="FIXTURE",
        )
        position = self.store.open_positions("EXP-001")[0]
        self.assertEqual(position["side"], "short")
        self.assertEqual(position["leverage"], 3)
        opened = datetime.fromisoformat(snapshot.data_cutoff.replace("Z", "+00:00"))
        closed_at = opened + timedelta(minutes=1)
        bar = {
            "open_time": opened.isoformat().replace("+00:00", "Z"),
            "close_time": closed_at.isoformat().replace("+00:00", "Z"),
            "open": intent.entry_price,
            "high": intent.stop_price + (risk.liquidation_price - intent.stop_price) * 0.05,
            "low": intent.entry_price - features["atr"] * 0.1,
            "close": intent.stop_price + 1,
            "volume": 1,
        }
        events = self.store.apply_monitor_bar(
            "EXP-001",
            "BTCUSDT",
            bar,
            funding_rate=None,
            taker_fee_rate=config["taker_fee_rate"],
            slippage_bps=config["slippage_bps"],
            data_origin="FIXTURE",
        )
        self.assertEqual(events[0]["event"], "stop")
        closed = self.store.list_positions("EXP-001", cohort="primary")[0]
        self.assertEqual(closed["status"], "closed")
        self.assertLess(closed["realized_pnl"], 0)
        self.assertGreater(closed["slippage_paid"], 0)

    def test_jev_failure_fallback_is_configured_and_fails_closed(self):
        class FailingJev:
            calls = 0

            def evaluate(self, *_args):
                self.calls += 1
                raise AIProviderError("mock provider failure")

        skipped_store = PaperStore(":memory:")
        skipped_config = default_experiment_config()
        skipped_config["evaluation_arms"] = ["hybrid"]
        skipped_config["primary_arm"] = "hybrid"
        skipped_config["fallback_policy"] = "SKIP"
        skipped_store.save_experiment(skipped_config)
        failing = FailingJev()
        skipped_gpt = CountingGPTProvider()
        skipped = PaperRuntime(
            skipped_store,
            jev_provider=failing,
            gpt_provider=skipped_gpt,
            clock=self.clock,
        )
        skipped.start()
        skipped_cycle = skipped.run_cycle("BTCUSDT", as_of=self.now, manual=True)
        self.assertFalse(skipped_cycle["primary_decision"]["risk_eligible"])
        self.assertIsNone(skipped_cycle["primary_decision"]["intent"])
        self.assertEqual(skipped_gpt.calls, [])
        self.assertEqual(
            sum(position["status"] == "open" for position in skipped_store.open_positions("EXP-001")),
            0,
        )
        self.assertEqual(skipped_store.ai_usage("EXP-001")[0]["status"], "failed")
        skipped_store.close()

        fallback_store = PaperStore(":memory:")
        fallback_config = default_experiment_config()
        fallback_config["evaluation_arms"] = ["hybrid"]
        fallback_config["primary_arm"] = "hybrid"
        fallback_config["fallback_policy"] = "GPT_FALLBACK"
        fallback_store.save_experiment(fallback_config)
        fallback_gpt = CountingGPTProvider()
        fallback = PaperRuntime(
            fallback_store,
            jev_provider=FailingJev(),
            gpt_provider=fallback_gpt,
            clock=self.clock,
        )
        fallback.start()
        result = fallback.run_cycle("BTCUSDT", as_of=self.now, manual=True)
        self.assertEqual(len(fallback_gpt.calls), 1)
        self.assertEqual(result["primary_decision"]["intent"]["source_arm"], "hybrid")
        self.assertIn(
            "jev_unavailable_gpt_fallback",
            result["primary_decision"]["escalation"]["reasons"],
        )
        fallback_store.close()

    def test_experiment_can_disable_jev_and_routed_gpt_without_implicit_calls(self):
        store = PaperStore(":memory:")
        config = default_experiment_config()
        config.update(
            {
                "evaluation_arms": ["hybrid"],
                "primary_arm": "hybrid",
                "jev_enabled": False,
                "gpt_escalation_enabled": False,
                "fallback_policy": "SKIP",
            }
        )
        store.save_experiment(config)
        gpt = CountingGPTProvider()
        jev = CountingJevProvider()
        runtime = PaperRuntime(
            store,
            market_provider=FixtureFuturesMarketDataProvider(),
            jev_provider=jev,
            gpt_provider=gpt,
            clock=self.clock,
        )
        runtime.start()
        result = runtime.run_cycle("BTCUSDT", as_of=self.now, manual=True)
        self.assertEqual(jev.calls, 0)
        self.assertEqual(gpt.calls, [])
        self.assertIsNone(result["primary_decision"]["intent"])
        self.assertIn(
            "jev_disabled_fail_closed",
            result["primary_decision"]["escalation"]["reasons"],
        )
        self.assertEqual(
            sum(p["status"] == "open" for p in store.open_positions("EXP-001")),
            0,
        )
        store.close()

    def test_versioned_provider_pricing_records_cost_and_version(self):
        provider = validate_provider_config(
            {
                "provider_id": "priced-provider",
                "kind": "openai_responses",
                "display_name": "Priced test provider",
                "base_url": "https://api.openai.com/v1",
                "model": "test-model",
                "enabled": True,
                "credential_env": "PAPER_TEST_PRICE_KEY",
                "pricing": {
                    "version": "fixture-pricing-2026-09",
                    "input_per_million": 2.0,
                    "output_per_million": 4.0,
                },
            }
        )
        record = PaperRuntime._call_record(
            provider,
            "gpt:hybrid",
            {
                "model": "test-model",
                "usage": {"input_tokens": 1000, "output_tokens": 1000},
                "latency_ms": 12.5,
                "prompt_hash": "a" * 64,
            },
        )
        self.assertAlmostEqual(record["cost_estimate"], 0.006)
        self.assertEqual(record["pricing_version"], "fixture-pricing-2026-09")
        self.assertEqual(record["latency_ms"], 12.5)

    def test_provider_validation_status_is_persisted_without_exposing_env_reference(self):
        public = self.store.list_providers()
        fixture = next(item for item in public if item["provider_id"] == "fixture-jev")
        self.assertEqual(fixture["last_validation_status"], "not_tested")
        self.assertNotIn("credential_env", fixture)

        runtime = PaperRuntime(self.store, clock=self.clock)
        result = runtime.test_provider("fixture-jev")
        self.assertTrue(result["fixture"])
        tested = next(
            item for item in self.store.list_providers() if item["provider_id"] == "fixture-jev"
        )
        self.assertEqual(tested["last_validation_status"], "passed")
        self.assertIsNotNone(tested["last_validated_at"])
        self.assertNotIn("credential_env", tested)

    def test_provider_export_omits_environment_reference_and_secret_values(self):
        provider = validate_provider_config(
            {
                "provider_id": "safe-provider",
                "kind": "openai_responses",
                "display_name": "Safe provider test",
                "base_url": "https://api.openai.com/v1",
                "model": "test-model",
                "enabled": True,
                "credential_env": "UNIT_TEST_SECRET_ENV_NAME",
            }
        )
        self.store.save_provider(provider)
        visible = next(
            item
            for item in self.store.list_providers()
            if item["provider_id"] == "safe-provider"
        )
        self.assertNotIn("credential_env", visible)
        archive_bytes, _ = PaperRuntime(self.store, clock=self.clock).export_bundle()
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            text = "".join(archive.read(name).decode("utf-8") for name in archive.namelist())
        self.assertNotIn("UNIT_TEST_SECRET_ENV_NAME", text)
        self.assertNotIn(UNIT_TOKEN, text)

    def test_loaded_env_value_is_absent_from_local_api_logs_and_export(self):
        self.store.save_provider(
            {
                "provider_id": "foundry-env-test",
                "kind": "foundry_responses",
                "display_name": "Foundry env isolation test",
                "base_url": "https://example.invalid/openai/v1",
                "model": "deployment-test",
                "enabled": True,
                "credential_env": "AZURE_OPENAI_API_KEY",
            }
        )
        runtime = PaperRuntime(self.store, clock=self.clock)
        scheduler = PaperScheduler(runtime)
        server = PaperHTTPServer(("127.0.0.1", 0), PaperRequestHandler, runtime, scheduler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(scheduler.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join, 5)
        self.addCleanup(server.shutdown)
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        logs = io.StringIO()
        with patch.dict(os.environ, {"AZURE_OPENAI_API_KEY": ENV_SENTINEL}), contextlib.redirect_stdout(
            logs
        ), contextlib.redirect_stderr(logs):
            with urllib.request.urlopen(f"{base_url}/api/providers", timeout=5) as response:
                public_body = response.read().decode("utf-8")
            archive, _ = runtime.export_bundle()
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            export_body = "".join(
                bundle.read(name).decode("utf-8")
                for name in bundle.namelist()
                if name.endswith((".json", ".jsonl", ".csv", ".md"))
            )
        self.assertNotIn(ENV_SENTINEL, public_body)
        self.assertNotIn("AZURE_OPENAI_API_KEY", public_body)
        self.assertNotIn(ENV_SENTINEL, export_body)
        self.assertNotIn("AZURE_OPENAI_API_KEY", export_body)
        self.assertNotIn(ENV_SENTINEL, logs.getvalue())

    def test_scheduler_keeps_running_without_browser_and_recovers_idempotently(self):
        config = default_experiment_config()
        config.update(
            {
                "symbols": ["BTCUSDT"],
                "evaluation_arms": ["quant"],
                "primary_arm": "quant",
                "schedule_delay_seconds": 0,
            }
        )
        self.store.save_experiment(config)
        runtime = PaperRuntime(
            self.store,
            market_provider=FixtureFuturesMarketDataProvider(),
            clock=self.clock,
        )
        scheduler = PaperScheduler(runtime, poll_seconds=1)
        scheduler.start()
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            cycles = self.store.list_cycles("EXP-001")
            if cycles and cycles[0]["status"] == "complete":
                break
            time.sleep(0.05)
        first = self.store.list_cycles("EXP-001")
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["status"], "complete")
        duplicate_events = [
            event
            for event in self.store.activity("EXP-001")
            if event["event_type"] == "duplicate_cycle_prevented"
        ]
        self.assertEqual(duplicate_events, [])
        scheduler.shutdown()

        recovered = PaperScheduler(runtime, poll_seconds=1)
        recovered.resume_on_startup()
        time.sleep(0.15)
        self.assertEqual(len(self.store.list_cycles("EXP-001")), 1)
        self.assertEqual(self.store.experiment()["status"], "running")
        recovered.shutdown()
        self.store.set_status("stopped")

    def test_runtime_persistence_and_configuration_freeze(self):
        path = ROOT / f".test-paper-futures-{os.getpid()}.sqlite3"
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{path}{suffix}")
            self.addCleanup(lambda path=candidate: path.unlink(missing_ok=True))
        store = PaperStore(path)
        runtime = PaperRuntime(store, clock=self.clock)
        runtime.start()
        result = runtime.run_cycle("BTCUSDT", as_of=self.now, manual=True)
        store.close()
        reopened = PaperStore(path)
        self.addCleanup(reopened.close)
        loaded = reopened.cycle(result["cycle_id"])
        self.assertEqual(loaded["snapshot_hash"], result["snapshot_hash"])
        self.assertEqual(len(reopened.open_positions("EXP-001")), 12)
        reopened.set_status("stopped")
        changed = reopened.experiment()["config"]
        changed["starting_balance_usdt"] = 200
        with self.assertRaisesRegex(PaperTradingError, "frozen"):
            reopened.save_experiment(changed)

    def test_restart_marks_processing_cycle_interrupted_without_repeating_ai(self):
        cycle_id = "EXP-001:BTCUSDT:interrupted"
        snapshot = FixtureFuturesMarketDataProvider().fetch_snapshot("BTCUSDT", self.now)
        self.store.begin_cycle(
            cycle_id,
            "EXP-001",
            "BTCUSDT",
            snapshot.data_cutoff,
            snapshot.snapshot_hash,
            snapshot.as_of,
            snapshot.data_origin,
        )
        runtime = PaperRuntime(
            self.store,
            clock=self.clock,
            jev_provider=CountingJevProvider(),
            gpt_provider=CountingGPTProvider(),
        )
        scheduler = PaperScheduler(runtime, poll_seconds=1)
        scheduler.resume_on_startup()
        recovered = self.store.cycle(cycle_id)
        self.assertEqual(recovered["status"], "interrupted")
        self.assertEqual(recovered["retry_policy"], "no_ai_retry_for_consumed_cycle")
        self.assertEqual(self.store.pending_orders("EXP-001"), [])
        scheduler.shutdown()
        self.store.set_status("stopped")
