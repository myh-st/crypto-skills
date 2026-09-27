from __future__ import annotations

import json
import io
import unittest
import urllib.error
from datetime import datetime, timezone
from typing import Any

from crypto_eval.contracts import DECISION_STATES, MAX_TARGET_LEVELS, EvaluationError
from crypto_eval.openai_runner import (
    DEFAULT_MODEL_ID,
    DEFAULT_OPENAI_BASE_URL,
    MissingOpenAICredentialsError,
    OpenAIResponsesConfig,
    OpenAIResponsesError,
    OpenAIResponsesRunner,
    load_skill_instructions,
)
from crypto_eval.runner import run_predictions
from tests.eval_test_support import demo_dataset


def valid_decision(case: dict[str, Any]) -> dict[str, Any]:
    reference = float(case["snapshot"]["candles"][-1]["close"])
    return {
        "decision_state": "WAIT_FOR_PULLBACK",
        "bias": "bullish",
        "confidence": "moderate",
        "entry": {
            "kind": "pullback",
            "direction": "long",
            "zone_low": reference * 0.97,
            "zone_high": reference * 0.99,
            "level": None,
            "reference_price": None,
            "confirmation": "touch",
        },
        "invalidation": reference * 0.95,
        "targets": [reference * 1.03],
        "leverage_stress": None,
        "rationale": "Conditional setup based only on the supplied closed candles.",
    }


def response_for(value: Any) -> bytes:
    return json.dumps(
        {
            "id": "resp-mocked",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps(value)}],
                }
            ],
        }
    ).encode("utf-8")


class OpenAIResponsesRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = demo_dataset()
        self.case = self.dataset["cases"][0]
        self.mock_key = "mock-token-not-a-real-credential"

    def test_responses_api_shape_and_frozen_prediction_contract(self) -> None:
        calls: list[dict[str, Any]] = []

        def transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
            calls.append(
                {
                    "url": url,
                    "headers": headers,
                    "body": json.loads(body),
                    "timeout": timeout,
                }
            )
            return response_for(valid_decision(self.case))

        runner = OpenAIResponsesRunner(
            OpenAIResponsesConfig(),
            api_key=self.mock_key,
            variant="skill",
            run_id="pair-contract-test",
            skill_text="Use only the supplied closed candles.",
            skill_commit="test-skill-commit",
            question="Describe a conditional setup.",
            transport=transport,
            clock=lambda: datetime(2026, 1, 11, tzinfo=timezone.utc),
        )
        predictions = run_predictions(
            self.dataset,
            runner,
            case_ids=[self.case["case_id"]],
        )

        self.assertEqual(len(predictions), 1)
        prediction = predictions[0]
        self.assertEqual(prediction["runner"]["model_id"], DEFAULT_MODEL_ID)
        self.assertEqual(prediction["runner"]["execution_status"], "invoked")
        self.assertEqual(prediction["runner"]["variant"], "skill")
        self.assertEqual(prediction["dataset_hash"], self.dataset["dataset_hash"])
        self.assertEqual(prediction["frozen_at"], "2026-01-11T00:00:00.000000Z")
        self.assertEqual(calls[0]["url"], f"{DEFAULT_OPENAI_BASE_URL}/responses")
        auth_header = calls[0]["headers"].get("Authorization", "")
        self.assertTrue(auth_header.startswith("Bearer "))
        self.assertEqual(auth_header.partition(" ")[2], self.mock_key)
        self.assertEqual(
            set(calls[0]["headers"]),
            {"Authorization", "Content-Type", "Accept"},
        )
        request_body = calls[0]["body"]
        self.assertEqual(request_body["model"], "gpt-6-luna")
        self.assertEqual(request_body["reasoning"], {"effort": "max"})
        self.assertFalse(request_body["store"])
        self.assertEqual(request_body["text"]["format"]["type"], "json_schema")
        self.assertTrue(request_body["text"]["format"]["strict"])
        self.assertIn("Use only the supplied closed candles.", request_body["instructions"])
        self.assertNotIn("outcomes", request_body["input"].lower())
        self.assertNotIn("future_candles", request_body["input"].lower())
        self.assertNotIn(self.mock_key, json.dumps(request_body))
        self.assertNotIn(self.mock_key, json.dumps(prediction["runner"]))
        self.assertNotIn(self.mock_key, json.dumps(prediction))

    def test_paired_arms_share_everything_except_the_skill_instruction_text(self) -> None:
        requests: list[dict[str, Any]] = []

        def transport(_url: str, _headers: dict[str, str], body: bytes, _timeout: float) -> bytes:
            requests.append(json.loads(body))
            return response_for(valid_decision(self.case))

        common = {
            "config": OpenAIResponsesConfig(),
            "api_key": self.mock_key,
            "run_id": "pair-same-snapshot",
            "skill_text": "One exact skill payload.",
            "question": "Compare current spot structure.",
            "transport": transport,
            "clock": lambda: datetime(2026, 1, 11, tzinfo=timezone.utc),
        }
        skill = OpenAIResponsesRunner(
            common["config"],
            api_key=common["api_key"],
            variant="skill",
            run_id=common["run_id"],
            skill_text=common["skill_text"],
            skill_commit="test-skill-commit",
            question=common["question"],
            transport=transport,
            clock=common["clock"],
        )
        control = OpenAIResponsesRunner(
            common["config"],
            api_key=common["api_key"],
            variant="control",
            run_id=common["run_id"],
            skill_text=common["skill_text"],
            skill_commit=None,
            question=common["question"],
            transport=transport,
            clock=common["clock"],
        )

        skill_prediction = run_predictions(self.dataset, skill, [self.case["case_id"]])[0]
        control_prediction = run_predictions(self.dataset, control, [self.case["case_id"]])[0]

        self.assertEqual(len(requests), 2)
        skill_request, control_request = requests
        self.assertEqual(skill_request["model"], control_request["model"])
        self.assertEqual(skill_request["reasoning"], control_request["reasoning"])
        self.assertEqual(skill_request["input"], control_request["input"])
        self.assertEqual(
            skill_request["text"]["format"],
            control_request["text"]["format"],
        )
        self.assertEqual(
            {key: value for key, value in skill_request.items() if key != "instructions"},
            {key: value for key, value in control_request.items() if key != "instructions"},
        )
        self.assertNotEqual(
            skill_request["instructions"],
            control_request["instructions"],
        )
        self.assertEqual(
            skill_request["instructions"].replace("One exact skill payload.", ""),
            control_request["instructions"],
        )
        self.assertEqual(
            skill_prediction["runner"]["inference_config_hash"],
            control_prediction["runner"]["inference_config_hash"],
        )
        self.assertTrue(
            skill_prediction["runner"]["prompt_version"].startswith(
                "crypto-market-decision.v1:skill-sha256-"
            )
        )
        self.assertEqual(
            control_prediction["runner"]["prompt_version"],
            "crypto-market-decision.v1:control-no-skill",
        )
        self.assertEqual(skill_prediction["runner"]["run_id"], control_prediction["runner"]["run_id"])
        self.assertEqual(skill_prediction["dataset_hash"], control_prediction["dataset_hash"])
        self.assertEqual(skill_prediction["case_id"], control_prediction["case_id"])
        self.assertNotEqual(skill_prediction["prediction_id"], control_prediction["prediction_id"])
        self.assertEqual(skill_prediction["runner"]["skill_commit"], "test-skill-commit")
        self.assertIsNone(control_prediction["runner"]["skill_commit"])

    def test_credentials_api_errors_invalid_json_tool_calls_and_refusals_fail_explicitly(self) -> None:
        no_call_runner = OpenAIResponsesRunner(
            OpenAIResponsesConfig(),
            api_key=None,
            variant="control",
            run_id="missing-key",
            skill_text="",
            transport=lambda *_args: self.fail("transport must not run without credentials"),
        )
        with self.assertRaises(MissingOpenAICredentialsError):
            no_call_runner.predict(self.case)

        def runner_for(transport):
            return OpenAIResponsesRunner(
                OpenAIResponsesConfig(),
                api_key=self.mock_key,
                variant="control",
                run_id="error-test",
                skill_text="",
                transport=transport,
            )

        cases = [
            ("invalid JSON", lambda *_args: b"{not-json"),
            (
                "tool use",
                lambda *_args: json.dumps(
                    {
                        "status": "completed",
                        "output": [{"type": "function_call", "name": "browser"}],
                    }
                ).encode("utf-8"),
            ),
            (
                "refusal",
                lambda *_args: json.dumps(
                    {
                        "status": "completed",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "refusal", "refusal": "Cannot comply"}],
                            }
                        ],
                    }
                ).encode("utf-8"),
            ),
        ]
        for expected, transport in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(OpenAIResponsesError):
                    runner_for(transport).predict(self.case)

        secret = self.mock_key

        def http_error(url: str, *_args) -> bytes:
            raise urllib.error.HTTPError(
                url,
                429,
                f"error body {secret}",
                {},
                io.BytesIO(secret.encode("utf-8")),
            )

        with self.assertRaises(OpenAIResponsesError) as context:
            runner_for(http_error).predict(self.case)
        self.assertIn("429", str(context.exception))
        self.assertNotIn(secret, str(context.exception))

        def timeout(*_args):
            raise TimeoutError(secret)

        with self.assertRaises(OpenAIResponsesError) as context:
            runner_for(timeout).predict(self.case)
        self.assertIn("timed out", str(context.exception))
        self.assertNotIn(secret, str(context.exception))

    def test_unsupported_state_and_missing_required_fields_are_rejected(self) -> None:
        for mutate in (
            lambda decision: decision.update(decision_state="NOT_A_STATE"),
            lambda decision: decision.pop("targets"),
        ):
            decision = valid_decision(self.case)
            mutate(decision)
            runner = OpenAIResponsesRunner(
                OpenAIResponsesConfig(),
                api_key=self.mock_key,
                variant="control",
                run_id="invalid-output",
                skill_text="",
                transport=lambda *_args, value=decision: response_for(value),
            )
            with self.assertRaises(EvaluationError):
                runner.predict(self.case)

        self.assertIn("NO_TRADE", DECISION_STATES)

    def test_model_schema_and_parser_reject_more_than_five_targets(self) -> None:
        captured_body: dict[str, Any] = {}
        decision = valid_decision(self.case)
        decision["targets"] = [
            float(self.case["snapshot"]["candles"][-1]["close"]) * (1 + 0.01 * index)
            for index in range(1, MAX_TARGET_LEVELS + 2)
        ]

        def transport(_url: str, _headers: dict[str, str], body: bytes, _timeout: float) -> bytes:
            captured_body.update(json.loads(body))
            return response_for(decision)

        runner = OpenAIResponsesRunner(
            OpenAIResponsesConfig(),
            api_key=self.mock_key,
            variant="control",
            run_id="target-limit-test",
            skill_text="",
            transport=transport,
        )

        with self.assertRaisesRegex(OpenAIResponsesError, "at most 5 levels"):
            runner.predict(self.case)
        self.assertEqual(
            captured_body["text"]["format"]["schema"]["properties"]["targets"]["maxItems"],
            MAX_TARGET_LEVELS,
        )


if __name__ == "__main__":
    unittest.main()
