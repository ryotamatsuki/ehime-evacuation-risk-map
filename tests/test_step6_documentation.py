from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_readme_uses_corrected_v4_v5_contract():
    text = read("README.md")
    required = [
        "津波分析対象：1,090メッシュ",
        "完全な避難経路：1,062メッシュ",
        "経路未成立：28メッシュ",
        "市町境を越える避難経路：13メッシュ",
        "5要素スコア完全：813メッシュ",
        "収容人数欠損のためcore scoreのみ：128メッシュ",
        "面積按分需要で収容負荷100%超：35施設",
        "full-mesh感度シナリオで収容負荷100%超：72施設",
        "欠損した要素を除外して残りの重みを再配分する処理は行いません",
        "全シナリオで上位10%を維持したメッシュは69件",
    ]
    for phrase in required:
        assert phrase in text, phrase

    forbidden = [
        "欠損要素は利用可能な重みで再正規化",
        "STEP 4で再集計するまで参考表示",
        "STEP 4再計算待ち",
    ]
    for phrase in forbidden:
        assert phrase not in text, phrase


def test_status_and_correction_plan_are_closed_not_pending():
    status = read("STATUS_20260822.md")
    plan = read("docs/analysis-correction-plan.md")

    assert "STEP 1〜4 analysis correction is **CLOSED**" in status
    assert "STEP 5 Sensitivity / Robustness / Final Analytical QA is **COMPLETED and merged**" in status
    assert "corrected production analysis/deploy run: `32598837155`" in status
    assert "STEP 5 production QA run: `32605144352`" in status
    assert "robust top-decile across all scenarios: 69" in status
    assert "native Safari / iPhone Safari" in status

    assert "Analysis correction staged plan — CLOSED" in plan
    assert "STEP 2 — Cross-border mesh-to-shelter routing — CLOSED" in plan
    assert "STEP 3 — Route tsunami exposure — CLOSED" in plan
    assert "STEP 4 — Evacuation demand & shelter-level capacity pressure — CLOSED" in plan

    stale = [
        "STEP 2 — Cross-border Mesh → Shelter Routing\n\nImplementation is staged",
        "STEP 3 — Corrected Route Tsunami Exposure\n\nPending",
        "STEP 4 — Evacuation Demand / Shelter-level Capacity Pressure / Risk Recalculation\n\nPending",
    ]
    for phrase in stale:
        assert phrase not in status


def test_step5_results_document_locks_production_findings():
    text = read("docs/step5-sensitivity-results.md")
    required = [
        "STEP 5 production QA run: `32605144352`",
        "Input corrected production run: `32598837155`",
        "remain in top decile under all 12 scenarios: **69 meshes**",
        "remain in Top 50 under all 12 scenarios: **42 meshes**",
        "equal weights | **0.989418**",
        "additional over-capacity shelters under full-mesh sensitivity: **37**",
    ]
    for phrase in required:
        assert phrase in text, phrase
