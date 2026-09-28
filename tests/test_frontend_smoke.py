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
    "Paper Trading",
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
    "paper-trading",
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
            "modules/paperApi.js",
            "modules/views/paperTrading.js",
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
        self.assertIn("PAPER / FIXTURE", html)
        self.assertIn("read-only public futures data", html)
        self.assertIn("No real-money execution", html)

    def test_nav_declares_research_and_paper_destinations(self) -> None:
        nav_source = read(FRONTEND_DIR / "modules/components/nav.js")
        for label in EXPECTED_DESTINATIONS:
            self.assertIn(label, nav_source, f"navigation is missing destination: {label}")
        for route in EXPECTED_ROUTES:
            self.assertIn(f'"{route}"', nav_source, f"navigation is missing route: {route}")
        self.assertIn("nav-link--active", nav_source)

    def test_app_registers_all_routes(self) -> None:
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
        sparkline_source = read(FRONTEND_DIR / "modules/components/sparkline.js")
        app_source = read(FRONTEND_DIR / "app.js")
        self.assertIn("Market snapshot", overview_source)
        self.assertIn("24h · fixture", overview_source)
        self.assertIn("renderSparkline", overview_source)
        self.assertIn("renderMarketOverviewChart", overview_source)
        self.assertIn("synthetic 24 hour price trend", sparkline_source)
        self.assertIn("Demo chart", chart_source)
        self.assertIn("Values are synthetic and not live prices.", chart_source)
        self.assertIn("overview fixture cards remain synthetic", app_source)
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

    def test_live_runtime_uses_same_origin_api_and_keeps_credentials_server_side(self) -> None:
        services_source = read(FRONTEND_DIR / "modules/services.js")
        analysis_source = read(FRONTEND_DIR / "modules/views/newAnalysis.js")
        for endpoint in [
            "/api/status",
            "/api/analyze",
            "/api/evaluations",
            "/api/forward/",
        ]:
            self.assertIn(endpoint, services_source)
        self.assertIn("Server-side OpenAI Responses API", analysis_source)
        self.assertIn("OPENAI_API_KEY", analysis_source)
        self.assertNotIn("Authorization", services_source)
        self.assertNotIn("OPENAI_API_KEY", services_source)

    def test_live_run_displays_frozen_forward_evaluation_state(self) -> None:
        run_source = read(FRONTEND_DIR / "modules/views/runDetail.js")
        chart_source = read(FRONTEND_DIR / "modules/components/priceChart.js")
        evaluations_source = read(FRONTEND_DIR / "modules/views/evaluations.js")
        self.assertIn("Forward evaluation", run_source)
        self.assertIn("outcome remains pending", run_source)
        self.assertIn("data-chart-fit-levels", chart_source)
        self.assertIn("fitAllLevels = !fitAllLevels", run_source)
        self.assertIn("Fetch & score outcome", evaluations_source)
        self.assertIn("horizon_closes_at", evaluations_source)

    def test_frontend_readme_documents_run_command_and_limitations(self) -> None:
        readme = read(FRONTEND_DIR / "README.md")
        self.assertIn("python3 -m http.server", readme)
        self.assertIn("demo", readme.lower())
        self.assertIn("limitation", readme.lower())


if __name__ == "__main__":
    unittest.main()
