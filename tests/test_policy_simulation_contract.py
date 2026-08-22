from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_policy_simulation_is_wired_into_active_app():
    main = read("src/main.tsx")
    wrapper = read("src/AppWithSimulation.tsx")
    panel = read("src/PolicySimulation.tsx")

    assert "import App from './AppWithSimulation'" in main
    assert "<AppCorrected />" in wrapper
    assert "対策シミュレーション" in wrapper
    assert "<PolicySimulation" in wrapper
    assert 'aria-label="対策シミュレーション"' in panel
    assert "Canonical Analysis Core v4 は変更しません" in panel
    assert "経路の再計算" in panel
    assert "収容人数未公表の施設もシミュレーション対象外" in panel


def test_simulation_contract_keeps_canonical_v4_read_only():
    source = read("src/policySimulation.ts")
    assert "row.score_status) !== 'complete'" in source
    assert "row.evacuation_difficulty_score" in source
    assert "capacityComponentFromPressure" in source
    assert "Math.min(Math.max(pressure * 100, 0), 100)" in source
    assert "selected_shelter_key" in source
    assert "assigned_demand_area_weighted" in source
    assert "simulatedCapacity = baselineCapacity + delta" in source
    assert "simulatedPressure = assignedDemand / simulatedCapacity" in source

    forbidden = [
        "row.evacuation_difficulty_score =",
        "row.capacity_pressure_area_weighted =",
        "selected_shelter_key =",
        "reroute",
        "overflowReassignment",
    ]
    for phrase in forbidden:
        assert phrase not in source, phrase


def test_simulation_ui_exposes_three_bounded_capacity_scenarios():
    panel = read("src/PolicySimulation.tsx")
    assert "const CAPACITY_OPTIONS = [100, 500, 1000]" in panel
    assert 'data-testid="simulation-baseline-overload"' in panel
    assert 'data-testid="simulation-after-overload"' in panel
    assert 'data-testid="simulation-resolved"' in panel
    assert "政策効果候補ランキング" in panel
    assert "公式な便益評価ではありません" in panel
