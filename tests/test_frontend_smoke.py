"""Static, dependency-free smoke checks for the Crypto Research frontend.

These tests do not execute JavaScript or require a browser; they verify the
frontend's structural contract (navigation destinations, demo disclaimers, and
alignment with the repository's analysis-output/decision-record/evidence-ledger
field names) directly from the source files. Full interactive verification
(navigation, composer submission, evidence drawer, decision save) still
requires a manual browser check — see frontend/README.md.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "frontend"

EXPECTED_DESTINATIONS = [
    "Overview",
    "New Analysis",
    "Runs",
    "Decisions",
    "Watchlist",
    "Evaluations",
    "Data Sources",
    "Settings",
]

EXPECTED_ROUTES = [
    "overview",
    "new-analysis",
    "runs",
    "decisions",
    "watchlist",
    "evaluations",
    "data-sources",
    "settings",
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class FrontendSmokeTests(unittest.TestCase):
    def test_frontend_directory_exists_with_core_files(self) -> None:
        for relative in [
            "index.html",
            "styles.css",
            "app.js",
            "README.md",
            "modules/state.js",
            "modules/services.js",
            "modules/router.js",
            "modules/generator.js",
            "modules/demoData.js",
            "modules/contracts.js",
            "modules/components/nav.js",
            "modules/components/marketOverviewChart.js",
            "modules/components/priceChart.js",
            "modules/components/sparkline.js",
            "modules/components/evidenceDrawer.js",
        ]:
            self.assertTrue(
                (FRONTEND_DIR / relative).is_file(),
                f"expected frontend file missing: {relative}",
            )

    def test_no_external_script_or_style_dependencies(self) -> None:
        html = read(FRONTEND_DIR / "index.html")
        self.assertNotIn("cdn.", html.lower())
        for match in re.findall(r'src="([^"]+)"', html):
            self.assertFalse(
                match.startswith("http"), f"index.html references an external script: {match}"
            )
        for match in re.findall(r'href="([^"]+)"', html):
            if match.startswith("#"):
                continue
            self.assertFalse(
                match.startswith("http"), f"index.html references an external stylesheet: {match}"
            )

    def test_index_declares_demo_disclaimer(self) -> None:
        html = read(FRONTEND_DIR / "index.html")
        self.assertIn("FIXTURE", html)
        self.assertIn("Synthetic market data", html)
        self.assertIn("No live providers or trading", html)

    def test_nav_declares_eight_destinations(self) -> None:
        nav_source = read(FRONTEND_DIR / "modules/components/nav.js")
        for label in EXPECTED_DESTINATIONS:
            self.assertIn(label, nav_source, f"navigation is missing destination: {label}")
        for route in EXPECTED_ROUTES:
            self.assertIn(f'"{route}"', nav_source, f"navigation is missing route: {route}")
        self.assertIn("nav-link--active", nav_source)

    def test_app_registers_all_eight_routes(self) -> None:
        app_source = read(FRONTEND_DIR / "app.js")
        for route in EXPECTED_ROUTES:
            # Object keys that are valid identifiers (e.g. "runs") are written
            # unquoted; others (e.g. "new-analysis") are quoted.
            self.assertTrue(
                f'"{route}"' in app_source or f"{route}:" in app_source,
                f"app.js does not register route: {route}",
            )

    def test_composer_view_has_required_fields(self) -> None:
        composer_source = read(FRONTEND_DIR / "modules/views/newAnalysis.js")
        for field in [
            'name="asset"',
            'name="analysisType"',
            'name="horizon"',
            'name="question"',
            'name="capital"',
            'name="riskStyle"',
            'name="venue"',
            'name="instrument"',
            'name="asOf"',
            'name="providers"',
            "composer-advanced",
        ]:
            self.assertIn(field, composer_source, f"composer is missing field: {field}")

    def test_overview_contains_market_snapshot_and_interactive_labeled_chart(self) -> None:
        overview_source = read(FRONTEND_DIR / "modules/views/overview.js")
        chart_source = read(FRONTEND_DIR / "modules/components/marketOverviewChart.js")
        self.assertIn("Market snapshot", overview_source)
        self.assertIn("renderSparkline", overview_source)
        self.assertIn("renderMarketOverviewChart", overview_source)
        for label in ["Price change (%)", "Market cap change (%)", "Volume change (%)", "Time ("]:
            self.assertIn(label, chart_source)
        self.assertIn("data-market-range", chart_source)
        self.assertIn("data-market-asset", chart_source)

    def test_report_view_follows_required_hierarchy_and_uses_contract_fields(self) -> None:
        report_source = read(FRONTEND_DIR / "modules/views/runDetail.js")
        ordered_markers = [
            "pill--${tone} pill--large",  # decision + confidence
            "Entry zone",
            "report-level-label\">Invalidation",
            "report-level-label\">Targets",
            "Price-level chart",
            ">Rationale<",
            ">Market structure<",
            ">Leverage<",
            ">Scenario map<",
        ]
        positions = [report_source.index(marker) for marker in ordered_markers]
        self.assertEqual(positions, sorted(positions), "report sections are out of the required order")
        self.assertIn("evidence-panel", report_source)
        self.assertIn("save-decision", report_source)

    def test_generator_output_uses_contract_field_names(self) -> None:
        generator_source = read(FRONTEND_DIR / "modules/generator.js")
        for field in [
            "preferred_entry",
            "secondary_entry",
            "invalidation",
            "targets",
            "reasons",
            "scenario_map",
            "monitoring_conditions",
            "evidence_ledger",
            "decision_state",
            "entry_zone",
            "thesis_result",
        ]:
            self.assertIn(field, generator_source, f"generator does not reference contract field: {field}")

    def test_generator_reasons_are_capped_at_five(self) -> None:
        generator_source = read(FRONTEND_DIR / "modules/generator.js")
        self.assertIn("reasonsPool.slice(0, 4", generator_source)

    def test_evidence_drawer_supports_filtering(self) -> None:
        drawer_source = read(FRONTEND_DIR / "modules/components/evidenceDrawer.js")
        self.assertIn('name="type"', drawer_source)
        self.assertIn('name="supports"', drawer_source)
        self.assertIn('name="quality"', drawer_source)

    def test_no_trading_or_pnl_claims(self) -> None:
        forbidden_terms = ["place order", "portfolio pnl", "live trading", "execute trade"]
        for path in FRONTEND_DIR.rglob("*.js"):
            content = read(path).lower()
            for term in forbidden_terms:
                self.assertNotIn(term, content, f"{path} references disallowed term: {term}")

    def test_frontend_readme_documents_run_command_and_limitations(self) -> None:
        readme = read(FRONTEND_DIR / "README.md")
        self.assertIn("python3 -m http.server", readme)
        self.assertIn("Demo", readme)
        self.assertIn("limitation", readme.lower())


if __name__ == "__main__":
    unittest.main()
