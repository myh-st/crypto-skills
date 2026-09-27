from __future__ import annotations

import json
import os
import shutil
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from crypto_eval.contracts import EvaluationError, digest
from crypto_eval.forward import ForwardRuntimeService, OutcomeNotReadyError
from crypto_eval.market_data import BinanceSpotKlinesProvider
from crypto_eval.openai_runner import (
    MissingOpenAICredentialsError,
    OpenAIResponsesConfig,
    load_skill_instructions,
)
from crypto_eval.server import create_runtime_server
from tests.eval_test_support import schema_errors


ROOT = Path(__file__).resolve().parents[1]
MOCK_KEY = "mock-token-not-a-real-credential"


def epoch_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def fake_kline(open_ms: int) -> list[object]:
    bar_index = open_ms // (60 * 60 * 1000)
    base = 200.0 + bar_index * 0.05
    return [
        open_ms,
        str(base),
        str(base + 1.0),
        str(base - 1.0),
        str(base + 0.5),
        "250",
        open_ms + 60 * 60 * 1000 - 1,
        "50000",
        42,
        "125",
        "25000",
        "0",
    ]


class MockedKlineTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, url: str, _timeout: float) -> bytes:
        query = parse_qs(urlparse(url).query)
        start_ms = int(query["startTime"][0])
        end_ms = int(query["endTime"][0])
        limit = int(query["limit"][0])
        self.calls.append(
            {"symbol": query["symbol"][0], "interval": query["interval"][0], "start": start_ms, "end": end_ms}
        )
        rows = []
        open_ms = start_ms
        step = 60 * 60 * 1000
        while open_ms <= end_ms and len(rows) < limit:
            rows.append(fake_kline(open_ms))
            open_ms += step
        return json.dumps(rows).encode("utf-8")


class MockedResponsesTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
        timeout: float,
    ) -> bytes:
        request = json.loads(body)
        user_input = json.loads(request["input"])
        case = user_input["case"]
        reference = float(case["snapshot"]["candles"][-1]["close"])
        is_skill = not request["instructions"].endswith("<skill_instructions>\n\n</skill_instructions>")
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "timeout": timeout,
                "body": request,
                "case": case,
            }
        )
        if is_skill:
            entry = {
                "kind": "pullback",
                "direction": "long",
                "zone_low": reference * 0.97,
                "zone_high": reference * 0.99,
                "level": None,
                "reference_price": None,
                "confirmation": "touch",
            }
            decision = {
                "decision_state": "WAIT_FOR_PULLBACK",
                "bias": "bullish",
                "confidence": "moderate",
                "entry": entry,
                "invalidation": reference * 0.95,
                "targets": [reference * 1.03],
                "leverage_stress": None,
                "rationale": "Wait for the supplied spot-candle pullback zone.",
            }
        else:
            decision = {
                "decision_state": "NO_TRADE",
                "bias": "neutral",
                "confidence": "low",
                "entry": {
                    "kind": "none",
                    "direction": None,
                    "zone_low": None,
                    "zone_high": None,
                    "level": None,
                    "reference_price": None,
                    "confirmation": None,
                },
                "invalidation": None,
                "targets": [],
                "leverage_stress": None,
                "rationale": "The control arm declines to form a setup from this snapshot.",
            }
        response = {
            "id": f"resp-mocked-{len(self.calls)}",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps(decision)}],
                }
            ],
        }
        return json.dumps(response).encode("utf-8")


class ForwardRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._now = [datetime(2026, 9, 27, 17, 10, tzinfo=timezone.utc)]
        self.clock = lambda: self._now[0]
        self.scratch = ROOT / f".test-forward-runtime-{os.getpid()}"
        if self.scratch.exists():
            shutil.rmtree(self.scratch)
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.klines = MockedKlineTransport()
        self.responses = MockedResponsesTransport()
        self.provider = BinanceSpotKlinesProvider(
            transport=self.klines,
            clock=self.clock,
        )
        self.service = ForwardRuntimeService(
            data_dir=self.scratch,
            interval="1h",
            history_bars=3,
            market_provider=self.provider,
            openai_config=OpenAIResponsesConfig(),
            api_key_provider=lambda: MOCK_KEY,
            openai_transport=self.responses,
            skill_commit_provider=lambda: "test-skill-commit",
            clock=self.clock,
        )

    def _request(self) -> dict[str, str]:
        return {
            "symbol": "BTCUSDT",
            "instrument": "spot",
            "horizon": "intraday",
            "question": "Describe a conditional spot setup.",
            "risk_style": "neutral",
        }

    def test_mocked_pair_freezes_same_snapshot_and_only_skill_text_differs(self) -> None:
        response = self.service.create(self._request())
        run = response["run"]

        self.assertEqual(run["runtimeMode"], "live")
        self.assertEqual(run["evaluationStatus"], "waiting_for_outcome")
        self.assertEqual(run["symbol"], "BTCUSDT")
        self.assertEqual(run["instrument"], "spot")
        self.assertEqual(run["entryKind"], "pullback")
        self.assertEqual(run["requestSettings"]["interval"], "1h")
        self.assertEqual(len(self.responses.calls), 2)
        skill_request, control_request = self.responses.calls
        self.assertEqual(skill_request["case"], control_request["case"])
        self.assertEqual(
            json.loads(skill_request["body"]["input"])["risk_style"],
            "neutral",
        )
        self.assertEqual(
            skill_request["body"]["input"],
            control_request["body"]["input"],
        )
        self.assertEqual(
            skill_request["body"]["reasoning"],
            control_request["body"]["reasoning"],
        )
        self.assertIn(
            "Loaded skill reference: point-in-time.md",
            skill_request["body"]["instructions"],
        )
        self.assertEqual(
            skill_request["body"]["model"],
            control_request["body"]["model"],
        )
        self.assertEqual(
            skill_request["body"]["instructions"].replace(
                load_skill_instructions(
                    ROOT / "skills/crypto-market-trading-analysis/SKILL.md"
                ),
                "",
            ),
            control_request["body"]["instructions"],
        )
        cutoff = run["requestSettings"]["dataAsOf"]
        self.assertTrue(all(candle["close_time"] <= cutoff for candle in run["marketCandles"]))
        self.assertNotIn("outcomes", skill_request["body"]["input"].lower())
        self.assertNotIn("future", skill_request["body"]["input"].lower())
        self.assertEqual(response["prediction"]["dataset_hash"], run["datasetHash"])
        self.assertEqual(response["prediction"]["case_id"], run["caseId"])
        self.assertEqual(response["prediction"]["runner"]["model_id"], "gpt-6-luna")
        self.assertEqual(response["prediction"]["runner"]["skill_commit"], "test-skill-commit")
        manifest = json.loads(
            (
                self.scratch
                / "forward"
                / run["id"]
                / "run.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            manifest["question_sha256"],
            digest("Describe a conditional spot setup."),
        )
        self.assertEqual(response["prediction"]["frozen_at"], run["createdAt"])
        self.assertEqual(
            response["prediction"]["runner"]["inference_config_hash"],
            response["pair"]["control_inference_config_hash"],
        )
        self.assertEqual(
            schema_errors(response["analysis"], "analysis-output.schema.json"),
            [],
        )
        self.assertEqual(
            schema_errors(response["prediction"], "eval-prediction.schema.json"),
            [],
        )
        self.assertEqual(
            schema_errors(run["evidence"], "evidence-ledger.schema.json"),
            [],
        )

        run_dir = self.scratch / "forward" / run["id"]
        for path in run_dir.rglob("*"):
            if path.is_file():
                self.assertNotIn(MOCK_KEY, path.read_text(encoding="utf-8"))
        archive = next((self.scratch / "archives").glob("*.json"))
        archive_json = json.loads(archive.read_text(encoding="utf-8"))
        self.assertEqual(archive_json["provider_id"], "binance-public-spot")
        self.assertEqual(archive_json["content_sha256"], run["archiveContentSha256"])
        self.assertNotIn(MOCK_KEY, archive.read_text(encoding="utf-8"))

    def test_horizon_blocks_early_scoring_then_scores_separate_outcomes(self) -> None:
        response = self.service.create(self._request())
        run_id = response["run"]["id"]
        provider_calls_after_create = len(self.klines.calls)
        with self.assertRaises(OutcomeNotReadyError):
            self.service.score(run_id)
        self.assertEqual(len(self.klines.calls), provider_calls_after_create)
        self.assertFalse(
            (self.scratch / "forward" / run_id / "outcomes.json").exists()
        )

        horizon_close = datetime.fromisoformat(
            response["run"]["horizonClosesAt"].replace("Z", "+00:00")
        )
        self._now[0] = horizon_close + timedelta(minutes=2)
        scored = self.service.score(run_id)

        self.assertEqual(scored["run"]["evaluationStatus"], "scored")
        self.assertTrue((self.scratch / "forward" / run_id / "outcomes.json").is_file())
        self.assertTrue((self.scratch / "forward" / run_id / "scores.json").is_file())
        archives = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (self.scratch / "archives").glob("*.json")
        ]
        self.assertEqual(len(archives), 2)
        self.assertTrue(
            any(
                item["data_cutoff"] == response["run"]["horizonClosesAt"]
                and item["candle_count"] == 24
                for item in archives
            )
        )
        outcome = json.loads(
            (self.scratch / "forward" / run_id / "outcomes.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(outcome["records"][0]["status"], "complete")
        self.assertEqual(len(outcome["records"][0]["candles"]), 24)
        self.assertTrue(
            all(
                candle["open_time"] >= response["run"]["requestSettings"]["dataAsOf"]
                for candle in outcome["records"][0]["candles"]
            )
        )
        self.assertNotIn(MOCK_KEY, json.dumps(scored))
        self.assertEqual(self.service.score(run_id)["run"]["evaluationStatus"], "scored")

    def test_missing_runtime_key_fails_before_market_or_model_requests(self) -> None:
        market_transport = MockedKlineTransport()
        model_transport = MockedResponsesTransport()
        runtime = ForwardRuntimeService(
            data_dir=self.scratch / "no-key",
            interval="1h",
            history_bars=3,
            market_provider=BinanceSpotKlinesProvider(
                transport=market_transport,
                clock=self.clock,
            ),
            openai_config=OpenAIResponsesConfig(),
            api_key_provider=lambda: "",
            openai_transport=model_transport,
            skill_commit_provider=lambda: "test-skill-commit",
            clock=self.clock,
        )

        self.assertFalse(runtime.status()["model_configured"])
        with self.assertRaises(MissingOpenAICredentialsError):
            runtime.create(self._request())
        self.assertEqual(market_transport.calls, [])
        self.assertEqual(model_transport.calls, [])

    def test_api_serves_frontend_and_returns_mocked_analysis_on_same_origin(self) -> None:
        with self.assertRaisesRegex(EvaluationError, "loopback"):
            create_runtime_server(self.service, host="0.0.0.0", port=0)
        server = create_runtime_server(
            self.service,
            host="127.0.0.1",
            port=0,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base_url = f"http://127.0.0.1:{server.server_address[1]}"

        with urlopen(f"{base_url}/api/status", timeout=3) as result:
            status = json.loads(result.read())
        self.assertEqual(status["mode"], "live")
        self.assertEqual(status["model_id"], "gpt-6-luna")
        self.assertTrue(status["model_configured"])
        self.assertNotIn(MOCK_KEY, json.dumps(status))

        with urlopen(f"{base_url}/frontend/", timeout=3) as result:
            self.assertEqual(result.status, 200)
            self.assertIn("frame-ancestors 'none'", result.headers["Content-Security-Policy"])
            self.assertIn('id="content"', result.read().decode("utf-8"))

        traversal = Request(f"{base_url}/frontend/../tests/test_frontend_smoke.py")
        with self.assertRaises(HTTPError) as context:
            urlopen(traversal, timeout=3)
        self.assertEqual(context.exception.code, 404)

        cross_origin = Request(
            f"{base_url}/api/analyze",
            data=json.dumps(self._request()).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Origin": "http://evil.example",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(cross_origin, timeout=3)
        self.assertEqual(context.exception.code, 403)
        self.assertEqual(self.responses.calls, [])

        invalid_json = Request(
            f"{base_url}/api/analyze",
            data=b"{",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(invalid_json, timeout=3)
        self.assertEqual(context.exception.code, 400)
        self.assertEqual(
            json.loads(context.exception.read())["error"]["code"],
            "invalid_json",
        )

        unsupported_media = Request(
            f"{base_url}/api/analyze",
            data=b"symbol=BTCUSDT",
            headers={"Content-Type": "text/plain"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(unsupported_media, timeout=3)
        self.assertEqual(context.exception.code, 415)
        self.assertEqual(self.klines.calls, [])
        self.assertEqual(self.responses.calls, [])

        request = Request(
            f"{base_url}/api/analyze",
            data=json.dumps(self._request()).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as result:
            analysis = json.loads(result.read())
        self.assertEqual(result.status, 201)
        self.assertEqual(analysis["run"]["evaluationStatus"], "waiting_for_outcome")
        self.assertEqual(analysis["prediction"]["runner"]["variant"], "skill")

        with urlopen(f"{base_url}/api/evaluations", timeout=3) as result:
            cases = json.loads(result.read())
        self.assertEqual(len(cases["runs"]), 1)
        self.assertEqual(cases["runs"][0]["status"], "waiting_for_outcome")

        provider_call_count = len(self.klines.calls)
        user_supplied_outcome = Request(
            f"{base_url}/api/forward/{analysis['run']['id']}/score",
            data=b'{"outcomes": []}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(user_supplied_outcome, timeout=3)
        self.assertEqual(context.exception.code, 400)

        early_score = Request(
            f"{base_url}/api/forward/{analysis['run']['id']}/score",
            method="POST",
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(early_score, timeout=3)
        self.assertEqual(context.exception.code, 409)
        self.assertEqual(len(self.klines.calls), provider_call_count)
        self.assertFalse(
            (self.scratch / "forward" / analysis["run"]["id"] / "outcomes.json").exists()
        )


if __name__ == "__main__":
    unittest.main()
