import json
import unittest

from scripts.validate_repo import (
    EXAMPLE_SCHEMAS,
    ROOT,
    SCHEMA_DIR,
    load_fixture_yaml,
    validate_repository,
)


class RepositoryContractTests(unittest.TestCase):
    def test_repository_contracts_pass(self) -> None:
        errors, examples_validated, states = validate_repository()
        self.assertEqual(errors, [])
        self.assertEqual(examples_validated, len(EXAMPLE_SCHEMAS))
        self.assertIn("WAIT_FOR_PULLBACK", states)
        self.assertIn("EXIT", states)

    def test_decision_record_example_matches_schema_root(self) -> None:
        example = load_fixture_yaml(ROOT / "examples" / "decision-record.yaml")
        self.assertIsInstance(example, dict)
        self.assertNotIn("decision_record", example)
        self.assertEqual(example["decision_state"], "WAIT_FOR_PULLBACK")

    def test_analysis_and_decision_states_use_shared_ref(self) -> None:
        analysis = json.loads(
            (SCHEMA_DIR / "analysis-output.schema.json").read_text(encoding="utf-8")
        )
        decision = json.loads(
            (SCHEMA_DIR / "decision-record.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            analysis["properties"]["state"]["$ref"], "decision-state.schema.json"
        )
        self.assertEqual(
            decision["properties"]["decision_state"]["$ref"],
            "decision-state.schema.json",
        )


if __name__ == "__main__":
    unittest.main()
