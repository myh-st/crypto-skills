#!/usr/bin/env python3
"""Dependency-free structural checks for the crypto-skills repository."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "crypto-market-trading-analysis"


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def main() -> int:
    errors: list[str] = []

    required_files = [
        ROOT / "README.md",
        ROOT / "docs" / "architecture.md",
        SKILL_DIR / "SKILL.md",
        SKILL_DIR / "agents" / "openai.yaml",
        SKILL_DIR / "references" / "evidence-ledger.md",
        SKILL_DIR / "references" / "point-in-time.md",
        SKILL_DIR / "references" / "decision-memory.md",
        SKILL_DIR / "references" / "data-contract.md",
        SKILL_DIR / "examples" / "concise-response.md",
        ROOT / "scripts" / "validate_repo.py",
    ]
    for path in required_files:
        if not path.is_file():
            fail(errors, f"missing required file: {path.relative_to(ROOT)}")

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

    for schema_path in sorted((ROOT / "schemas").glob("*.json")):
        try:
            json.loads(schema_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            fail(errors, f"invalid JSON in {schema_path.relative_to(ROOT)}: {exc}")

    if errors:
        print("Repository validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Repository validation passed.")
    print(f"- skill: {skill_path.relative_to(ROOT)}")
    print(f"- schemas: {len(list((ROOT / 'schemas').glob('*.json')))} valid JSON files")
    print("- TradingAgents-inspired evidence, debate, point-in-time, memory, and concise-output sections present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
