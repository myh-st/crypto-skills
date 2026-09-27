#!/usr/bin/env python3
"""Dependency-free contract checks for the crypto-skills repository.

The repository intentionally keeps validation runnable with the Python standard
library. The small YAML reader below covers the fixture subset used by this
repository (maps, lists, quoted/unquoted scalars, and inline arrays); it is not
intended to be a general-purpose YAML implementation.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "crypto-market-trading-analysis"
SCHEMA_DIR = ROOT / "schemas"

EXAMPLE_SCHEMAS = {
    ROOT / "examples" / "analysis-output.yaml": SCHEMA_DIR / "analysis-output.schema.json",
    ROOT / "examples" / "decision-record.yaml": SCHEMA_DIR / "decision-record.schema.json",
    ROOT / "examples" / "evidence-ledger.yaml": SCHEMA_DIR / "evidence-ledger.schema.json",
}

EVAL_JSON_EXAMPLES = {
    ROOT / "eval" / "specs" / "crypto-market-v1.json": SCHEMA_DIR / "eval-spec.schema.json",
    ROOT / "crypto_eval" / "fixtures" / "fixture-spec.json": SCHEMA_DIR / "eval-spec.schema.json",
    ROOT / "crypto_eval" / "fixtures" / "candidates.json": SCHEMA_DIR / "eval-candidates.schema.json",
    ROOT / "crypto_eval" / "fixtures" / "outcomes.json": SCHEMA_DIR / "eval-outcomes.schema.json",
}

REQUIRED_REFERENCES = [
    SKILL_DIR / "references" / "evidence-ledger.md",
    SKILL_DIR / "references" / "point-in-time.md",
    SKILL_DIR / "references" / "decision-memory.md",
    SKILL_DIR / "references" / "data-contract.md",
    SKILL_DIR / "references" / "technical-analysis.md",
    SKILL_DIR / "references" / "order-flow.md",
    SKILL_DIR / "references" / "derivatives.md",
    SKILL_DIR / "references" / "options.md",
    SKILL_DIR / "references" / "on-chain.md",
    SKILL_DIR / "references" / "market-context.md",
    SKILL_DIR / "references" / "tokenomics.md",
    SKILL_DIR / "references" / "portfolio-risk.md",
    SKILL_DIR / "references" / "backtesting.md",
    SKILL_DIR / "references" / "valuation-multiples.md",
    SKILL_DIR / "examples" / "concise-response.md",
]


class ContractError(ValueError):
    """Raised for a malformed repository fixture or schema."""


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def parse_scalar(raw: str) -> Any:
    value = raw.strip()
    if not value:
        return None
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value in {"true", "True", "TRUE"}:
        return True
    if value in {"false", "False", "FALSE"}:
        return False
    if value.startswith('"') and value.endswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ContractError(f"invalid quoted scalar {value!r}: {exc}") from exc
    if value.startswith("'") and value.endswith("'"):
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise ContractError(f"invalid quoted scalar {value!r}: {exc}") from exc
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(part) for part in split_inline(inner)]
    if value.startswith("{") and value.endswith("}"):
        raise ContractError("inline mapping syntax is not supported in fixtures")
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def split_inline(value: str) -> list[str]:
    """Split a simple inline YAML list without splitting quoted commas."""

    parts: list[str] = []
    start = 0
    quote: str | None = None
    depth = 0
    for index, char in enumerate(value):
        if quote:
            if char == quote and (index == 0 or value[index - 1] != "\\"):
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    parts.append(value[start:].strip())
    return parts


def split_key_value(text: str) -> tuple[str, str]:
    quote: str | None = None
    for index, char in enumerate(text):
        if quote:
            if char == quote and (index == 0 or text[index - 1] != "\\"):
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == ":":
            key = text[:index].strip()
            if not key:
                raise ContractError(f"empty mapping key in {text!r}")
            return key, text[index + 1 :].strip()
    raise ContractError(f"expected mapping entry, got {text!r}")


def load_fixture_yaml(path: Path) -> Any:
    """Load the small, deterministic YAML subset used by repository examples."""

    entries: list[tuple[int, str, int]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if "\t" in raw_line[:indent]:
            raise ContractError(f"tabs are not supported at {path}:{line_number}")
        text = raw_line[indent:]
        entries.append((indent, text, line_number))

    def parse_block(position: int, indent: int) -> tuple[Any, int]:
        if position >= len(entries) or entries[position][0] < indent:
            return None, position
        if entries[position][0] != indent:
            raise ContractError(
                f"unexpected indentation at {path}:{entries[position][2]}"
            )
        if entries[position][1].startswith("- ") or entries[position][1] == "-":
            result: list[Any] = []
            while position < len(entries) and entries[position][0] == indent:
                text = entries[position][1]
                if not (text.startswith("- ") or text == "-"):
                    break
                item_text = text[1:].strip()
                position += 1
                if not item_text:
                    if position < len(entries) and entries[position][0] > indent:
                        item, position = parse_block(position, entries[position][0])
                    else:
                        item = None
                    result.append(item)
                    continue
                if ":" not in item_text:
                    result.append(parse_scalar(item_text))
                    continue
                key, raw_value = split_key_value(item_text)
                item_map: dict[str, Any] = {}
                if raw_value:
                    item_map[key] = parse_scalar(raw_value)
                elif position < len(entries) and entries[position][0] > indent:
                    item_map[key], position = parse_block(position, entries[position][0])
                else:
                    item_map[key] = None
                if position < len(entries) and entries[position][0] > indent:
                    continuation, position = parse_mapping(position, entries[position][0])
                    if not isinstance(continuation, dict):
                        raise ContractError(
                            f"list item continuation must be a mapping at {path}:{entries[position - 1][2]}"
                        )
                    item_map.update(continuation)
                result.append(item_map)
            return result, position

        return parse_mapping(position, indent)

    def parse_mapping(position: int, indent: int) -> tuple[dict[str, Any], int]:
        result: dict[str, Any] = {}
        while position < len(entries) and entries[position][0] == indent:
            text = entries[position][1]
            if text.startswith("- ") or text == "-":
                break
            key, raw_value = split_key_value(text)
            position += 1
            if raw_value:
                result[key] = parse_scalar(raw_value)
            elif position < len(entries) and entries[position][0] > indent:
                result[key], position = parse_block(position, entries[position][0])
            else:
                result[key] = None
        return result, position

    if not entries:
        raise ContractError(f"empty YAML fixture: {path}")
    value, position = parse_block(0, entries[0][0])
    if position != len(entries):
        raise ContractError(f"unparsed YAML at {path}:{entries[position][2]}")
    return value


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid JSON in {path.relative_to(ROOT)}: {exc}") from exc


def resolve_pointer(document: Any, pointer: str) -> Any:
    value = document
    if pointer in {"", "#"}:
        return value
    for token in pointer.lstrip("#/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def resolve_ref(
    ref: str, schema_path: Path, root_schema: Any
) -> tuple[Any, Path, Any]:
    if ref.startswith("#"):
        return resolve_pointer(root_schema, ref), schema_path, root_schema
    if "#" in ref:
        target, fragment = ref.split("#", 1)
    else:
        target, fragment = ref, ""
    target_path = (schema_path.parent / target).resolve()
    if not target_path.is_file():
        raise ContractError(
            f"schema reference {ref!r} from {schema_path.relative_to(ROOT)} does not exist"
        )
    target_document = load_json(target_path)
    return (
        resolve_pointer(target_document, f"#{fragment}" if fragment else ""),
        target_path,
        target_document,
    )


def type_matches(value: Any, expected: str) -> bool:
    return {
        "null": value is None,
        "boolean": isinstance(value, bool),
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
    }.get(expected, False)


def validate_schema(
    value: Any,
    schema: dict[str, Any],
    schema_path: Path,
    root_schema: Any,
    location: str,
    errors: list[str],
) -> None:
    if "$ref" in schema:
        try:
            target, target_path, target_root = resolve_ref(
                schema["$ref"], schema_path, root_schema
            )
        except (ContractError, KeyError, IndexError) as exc:
            errors.append(f"{location}: {exc}")
            return
        validate_schema(value, target, target_path, target_root, location, errors)
        return

    expected = schema.get("type")
    expected_types = expected if isinstance(expected, list) else [expected] if expected else []
    if expected_types and not any(type_matches(value, item) for item in expected_types):
        errors.append(f"{location}: expected {expected_types}, got {type(value).__name__}")
        return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{location}: {value!r} is not one of {schema['enum']}")
        return
    if value is None:
        return
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{location}: string is shorter than minLength")
        if schema.get("format") == "date-time":
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                errors.append(f"{location}: invalid date-time {value!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{location}: value is below minimum {schema['minimum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{location}: fewer than minItems")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{location}: more than maxItems")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                validate_schema(item, item_schema, schema_path, root_schema, f"{location}[{index}]", errors)
    if isinstance(value, dict):
        for required in schema.get("required", []):
            if required not in value:
                errors.append(f"{location}: missing required property {required!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{location}: unexpected property {key!r}")
        for key, child in value.items():
            if key in properties:
                validate_schema(child, properties[key], schema_path, root_schema, f"{location}.{key}", errors)


def validate_examples(errors: list[str]) -> int:
    validated = 0
    for example_path, schema_path in EXAMPLE_SCHEMAS.items():
        if not example_path.is_file():
            fail(errors, f"missing example: {example_path.relative_to(ROOT)}")
            continue
        if not schema_path.is_file():
            fail(errors, f"missing schema for example: {schema_path.relative_to(ROOT)}")
            continue
        try:
            value = load_fixture_yaml(example_path)
            schema = load_json(schema_path)
            local_errors: list[str] = []
            validate_schema(value, schema, schema_path, schema, str(example_path.relative_to(ROOT)), local_errors)
            for error in local_errors:
                fail(errors, error)
            if not local_errors:
                validated += 1
        except ContractError as exc:
            fail(errors, str(exc))
    return validated


def validate_eval_json_examples(errors: list[str]) -> int:
    validated = 0
    for example_path, schema_path in EVAL_JSON_EXAMPLES.items():
        if not example_path.is_file():
            fail(errors, f"missing evaluation example: {example_path.relative_to(ROOT)}")
            continue
        if not schema_path.is_file():
            fail(errors, f"missing evaluation schema: {schema_path.relative_to(ROOT)}")
            continue
        try:
            value = load_json(example_path)
            schema = load_json(schema_path)
            local_errors: list[str] = []
            validate_schema(
                value,
                schema,
                schema_path,
                schema,
                str(example_path.relative_to(ROOT)),
                local_errors,
            )
            for error in local_errors:
                fail(errors, error)
            if not local_errors:
                validated += 1
        except ContractError as exc:
            fail(errors, str(exc))
    return validated


def canonical_states(errors: list[str]) -> list[str]:
    path = SCHEMA_DIR / "decision-state.schema.json"
    if not path.is_file():
        fail(errors, "missing canonical decision-state schema")
        return []
    try:
        schema = load_json(path)
    except ContractError as exc:
        fail(errors, str(exc))
        return []
    states = schema.get("enum")
    if schema.get("type") != "string" or not isinstance(states, list) or not states:
        fail(errors, "decision-state.schema.json must define a non-empty string enum")
        return []
    if len(states) != len(set(states)):
        fail(errors, "decision-state.schema.json contains duplicate states")
    return states


def validate_state_contract(states: list[str], errors: list[str]) -> None:
    for schema_name, property_name in (("analysis-output.schema.json", "state"), ("decision-record.schema.json", "decision_state")):
        path = SCHEMA_DIR / schema_name
        if not path.is_file():
            continue
        try:
            schema = load_json(path)
        except ContractError:
            continue
        node = schema.get("properties", {}).get(property_name, {})
        if node.get("$ref") != "decision-state.schema.json":
            fail(errors, f"{schema_name}.{property_name} must reference decision-state.schema.json")
    skill_path = SKILL_DIR / "SKILL.md"
    skill_text = skill_path.read_text(encoding="utf-8") if skill_path.is_file() else ""
    for state in states:
        if state not in skill_text:
            fail(errors, f"SKILL.md does not mention canonical state {state}")


def validate_required_files(errors: list[str]) -> None:
    required_files = [
        ROOT / "README.md",
        ROOT / "README.th.md",
        ROOT / "docs" / "architecture.md",
        SKILL_DIR / "SKILL.md",
        SKILL_DIR / "agents" / "openai.yaml",
        ROOT / "scripts" / "validate_repo.py",
        ROOT / "crypto_eval" / "__main__.py",
        ROOT / "crypto_eval" / "cli.py",
        ROOT / "crypto_eval" / "contracts.py",
        ROOT / "crypto_eval" / "dataset.py",
        ROOT / "crypto_eval" / "runner.py",
        ROOT / "crypto_eval" / "providers.py",
        ROOT / "crypto_eval" / "scoring.py",
        ROOT / "crypto_eval" / "baselines.py",
        ROOT / "crypto_eval" / "lifecycle.py",
        ROOT / "crypto_eval" / "reporting.py",
        ROOT / "crypto_eval" / "io.py",
        ROOT / "docs" / "evaluation.md",
        ROOT / "eval" / "specs" / "crypto-market-v1.json",
        SCHEMA_DIR / "eval-spec.schema.json",
        SCHEMA_DIR / "eval-candidates.schema.json",
        SCHEMA_DIR / "eval-dataset.schema.json",
        SCHEMA_DIR / "eval-prediction.schema.json",
        SCHEMA_DIR / "eval-outcomes.schema.json",
        SCHEMA_DIR / "eval-report.schema.json",
        SCHEMA_DIR / "decision-state.schema.json",
        ROOT / ".github" / "workflows" / "validate.yml",
        ROOT / "tests" / "test_contracts.py",
        ROOT / "tests" / "test_eval_contracts.py",
        ROOT / "tests" / "test_eval_scoring.py",
        ROOT / "tests" / "test_eval_baselines_reports.py",
        ROOT / "tests" / "eval_test_support.py",
        *REQUIRED_REFERENCES,
    ]
    for path in required_files:
        if not path.is_file():
            fail(errors, f"missing required file: {path.relative_to(ROOT)}")


def validate_skill_metadata(errors: list[str]) -> None:
    skill_path = SKILL_DIR / "SKILL.md"
    if skill_path.is_file():
        text = skill_path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            fail(errors, "SKILL.md must start with YAML frontmatter")
        else:
            frontmatter = text.split("---\n", 2)[1]
            if not re.search(r"^name:\s*crypto-market-trading-analysis\s*$", frontmatter, re.MULTILINE):
                fail(errors, "SKILL.md frontmatter has the wrong or missing name")
            if not re.search(r"^description:\s*", frontmatter, re.MULTILINE):
                fail(errors, "SKILL.md frontmatter is missing description")
        required_sections = [
            "# 21A. Internal Investment Committee",
            "# 21B. Evidence Ledger and Debate Discipline",
            "# 30A. Point-in-Time Integrity",
            "# 30B. Decision Memory and Outcome Learning",
            "# 31A. Human Response Contract",
            "# 32. Default Human Output Format",
            "# 39. Safety and Epistemic Discipline",
            "# 40. Repository Companion Resources",
        ]
        for section in required_sections:
            if section not in text:
                fail(errors, f"SKILL.md is missing required section: {section}")

    metadata_path = SKILL_DIR / "agents" / "openai.yaml"
    if metadata_path.is_file():
        metadata = metadata_path.read_text(encoding="utf-8")
        for key in ("interface:", "display_name:", "short_description:", "default_prompt:", "policy:", "allow_implicit_invocation:"):
            if key not in metadata:
                fail(errors, f"agents/openai.yaml is missing {key}")
        if "$crypto-market-trading-analysis" not in metadata:
            fail(errors, "agents/openai.yaml default_prompt must mention the skill")


def validate_readme_contract(errors: list[str]) -> None:
    for readme_name in ("README.md", "README.th.md"):
        path = ROOT / readme_name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for example_name in ("analysis-output.yaml", "decision-record.yaml", "evidence-ledger.yaml"):
            if example_name not in text:
                fail(errors, f"{readme_name} does not mention {example_name}")
        if "decision-state.schema.json" not in text:
            fail(errors, f"{readme_name} does not mention decision-state.schema.json")
        if "Validation != Accuracy Evaluation" not in text:
            fail(errors, f"{readme_name} does not distinguish validation from accuracy evaluation")
        if "DEMO / HARNESS VALIDATION" not in text:
            fail(errors, f"{readme_name} does not label deterministic fixture results")


def validate_repository() -> tuple[list[str], int, list[str]]:
    errors: list[str] = []
    validate_required_files(errors)
    validate_skill_metadata(errors)
    schema_paths = sorted(SCHEMA_DIR.glob("*.json"))
    for schema_path in schema_paths:
        try:
            load_json(schema_path)
        except ContractError as exc:
            fail(errors, str(exc))
    states = canonical_states(errors)
    validate_state_contract(states, errors)
    examples_validated = validate_examples(errors)
    validate_eval_json_examples(errors)
    validate_readme_contract(errors)
    return errors, examples_validated, states


def main() -> int:
    errors, examples_validated, states = validate_repository()
    if errors:
        print("Repository validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Repository validation passed.")
    print(f"- skill: {(SKILL_DIR / 'SKILL.md').relative_to(ROOT)}")
    print(f"- schemas: {len(list(SCHEMA_DIR.glob('*.json')))} valid JSON files")
    print(f"- examples: {examples_validated}/{len(EXAMPLE_SCHEMAS)} validated against schemas")
    print(f"- evaluation JSON examples: {len(EVAL_JSON_EXAMPLES)}/{len(EVAL_JSON_EXAMPLES)} validated")
    print(f"- canonical decision states: {len(states)}")
    print("- TradingAgents-inspired evidence, debate, point-in-time, memory, and concise-output sections present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
