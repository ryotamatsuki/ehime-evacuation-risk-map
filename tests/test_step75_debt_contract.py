from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LEGACY_SCRIPTS = [
    "scripts/calculate_evacuation_routes.py",
    "scripts/calculate_route_exposure.py",
    "scripts/calculate_capacity_pressure.py",
    "scripts/calculate_risk_score.py",
    "scripts/export_web_data.py",
    "scripts/validate_data.py",
]

RETIRED_WORKFLOWS = [
    ".github/workflows/deploy-pages.yml",
    ".github/workflows/frontend-ui-check.yml",
    ".github/workflows/frontend-v4-production-regression.yml",
    ".github/workflows/step6-webkit-smoke.yml",
    ".github/workflows/step7-policy-simulation.yml",
]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_retired_parallel_analysis_entrypoints_are_removed():
    for path in LEGACY_SCRIPTS:
        assert not (ROOT / path).exists(), f"retired pre-v4 entrypoint still exists: {path}"

    scripts_readme = read("scripts/README.md")
    for name in (
        "calculate_evacuation_routes.py",
        "calculate_route_exposure.py",
        "calculate_capacity_pressure.py",
        "calculate_risk_score.py",
        "export_web_data.py",
    ):
        assert name not in scripts_readme, f"scripts README still points to legacy entrypoint: {name}"
    assert "欠損要素がある場合は利用可能な重みで再正規化" not in scripts_readme
    assert "calculate_evacuation_routes_v2.py" in scripts_readme
    assert "calculate_step4_demand_capacity_risk.py" in scripts_readme
    assert "export_corrected_public_data.py" in scripts_readme


def test_source_data_qa_no_longer_validates_legacy_risk_schema():
    package = read("package.json")
    workflow = read(".github/workflows/data-qa.yml")
    validator = read("scripts/validate_source_data.py")
    assert "validate_source_data.py" in package
    assert "validate_source_data.py" in workflow
    assert "route_exposure.csv" not in package
    assert "risk_mesh.csv" not in package
    assert "available weights are renormalized" not in validator
    assert "--routes-csv" not in validator
    assert "--risk-csv" not in validator


def test_frontend_has_one_live_simulation_entrypoint_and_shared_primitive_contract():
    app = read("src/AppCorrected.tsx")
    wrapper = read("src/AppWithSimulation.tsx")
    simulation_css = read("src/policy-simulation.css")
    simulation = read("src/policySimulation.ts")
    panel = read("src/PolicySimulation.tsx")

    assert "simulation: '対策シミュレーション'" not in app
    assert "id === 'simulation'" not in app
    assert "next === 'simulation'" not in app
    assert "button:nth-child(4)" not in simulation_css
    assert "対策シミュレーション" in wrapper
    assert "DATA_BASE" in app and "recordNumber" in app and "recordText" in app
    assert "POLICY_WEIGHTS" in simulation
    assert "finiteNumber" in simulation
    assert "loadJson" in panel
    assert "assignedDemand / baselineCapacity" not in simulation


def test_typescript_unused_checks_are_enabled():
    tsconfig = read("tsconfig.app.json")
    assert '"noUnusedLocals": true' in tsconfig
    assert '"noUnusedParameters": true' in tsconfig


def test_duplicate_application_workflows_are_consolidated():
    for path in RETIRED_WORKFLOWS:
        assert not (ROOT / path).exists(), f"retired duplicate workflow still exists: {path}"
    quality = read(".github/workflows/application-quality.yml")
    assert "STEP 7.5 Application Quality" in quality
    assert "verify_production_regression.py" in quality
    assert "webkit_smoke.mjs" in quality
    assert "analyze_step5_sensitivity.py" in quality
    assert "validate_policy_simulation.py" in quality


def test_expensive_analysis_pr_workflows_are_path_scoped_but_main_release_is_not():
    analysis = read(".github/workflows/routing-step2.yml")
    foundation = read(".github/workflows/routing-foundation-step1.yml")
    pull_request_block, push_block = analysis.split("  push:\n", 1)
    assert "    paths:" in pull_request_block
    assert "    paths:" not in push_block.split("  workflow_dispatch:", 1)[0]
    assert "    paths:" in foundation.split("  workflow_dispatch:", 1)[0]
