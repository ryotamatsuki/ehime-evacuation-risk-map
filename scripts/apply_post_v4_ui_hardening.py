#!/usr/bin/env python3
from pathlib import Path

path = Path('src/AppCorrected.tsx')
text = path.read_text(encoding='utf-8')


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected exactly 1 match, found {count}')
    text = text.replace(old, new, 1)

replace_once(
    "type WeightKey = 'tsunami_exposure' | 'vulnerable_population' | 'walking_accessibility' | 'route_inundation_exposure' | 'shelter_capacity_pressure'\n",
    "",
    'remove WeightKey',
)

replace_once(
"""const DEFAULT_WEIGHTS: Record<WeightKey, number> = {
  tsunami_exposure: 25,
  vulnerable_population: 20,
  walking_accessibility: 25,
  route_inundation_exposure: 15,
  shelter_capacity_pressure: 15,
}

const WEIGHT_LABELS: Record<WeightKey, string> = {
  tsunami_exposure: '津波浸水曝露',
  vulnerable_population: '要配慮人口',
  walking_accessibility: '徒歩アクセス',
  route_inundation_exposure: '経路津波曝露',
  shelter_capacity_pressure: '収容負荷',
}

""",
    "",
    'remove browser-side weights',
)

replace_once(
    "  score: { label: '政策優先度', unit: '点', note: '探索的5要素合成指標。5要素が揃うメッシュのみ表示し、欠損要素を再配分しません' },",
    "  score: { label: '政策優先度', unit: '点', note: 'STEP 4で確定した公開済み5要素スコアを直接表示。欠損要素の再配分・ブラウザ再計算は行いません' },",
    'update score methodology copy',
)

replace_once(
"""const componentColumns: Record<WeightKey, string> = {
  tsunami_exposure: 'tsunami_exposure_component',
  vulnerable_population: 'vulnerable_population_component',
  walking_accessibility: 'walking_accessibility_component',
  route_inundation_exposure: 'route_inundation_exposure_component',
  shelter_capacity_pressure: 'shelter_capacity_pressure_component_area_weighted',
}

""",
    "",
    'remove component recalculation map',
)

replace_once(
"""const scoreForRow = (row: RiskRow, weights: Record<WeightKey, number>): number | null => {
  // STEP 4 definition: a five-component score is only comparable when every
  // component exists. Missing capacity/age data must never be silently removed
  // from the denominator.
  if (toText(row, 'score_status') !== 'complete') return null
  const values = (Object.keys(componentColumns) as WeightKey[]).map((key) => ({ key, value: toNumber(row, componentColumns[key]) }))
  if (values.some((item) => item.value === null)) return null
  const denominator = values.reduce((sum, item) => sum + weights[item.key], 0)
  if (denominator <= 0) return null
  return values.reduce((sum, item) => sum + (item.value ?? 0) * weights[item.key], 0) / denominator
}
""",
"""const canonicalPolicyScore = (row: RiskRow): number | null => {
  // STEP 4 is the single source of truth for the five-component policy score.
  // The browser never recomputes or renormalizes weights. Incomplete rows stay
  // null exactly as exported by Analysis Core v4.
  if (toText(row, 'score_status') !== 'complete') return null
  return toNumber(row, 'evacuation_difficulty_score')
}
""",
    'replace browser score calculation',
)

replace_once(
    "  const [weights, setWeights] = useState(DEFAULT_WEIGHTS)\n",
    "",
    'remove weights state',
)

replace_once(
    "        properties.policy_score = scoreForRow(risk, weights)",
    "        properties.policy_score = canonicalPolicyScore(risk)",
    'use canonical exported score',
)

replace_once(
    "  }), [populationFeatures, riskByMesh, weights])",
    "  }), [populationFeatures, riskByMesh])",
    'remove weights dependency',
)

replace_once(
"""            <details className=\"advanced-card\">
              <summary><span><span className=\"section-kicker\">ADVANCED</span><b>高度な分析</b></span><span>⌄</span></summary>
              <div className=\"advanced-body\">
                <div className=\"advanced-note\">政策優先度の探索的重み。公式な政策基準ではありません。重み変更による再計算は5要素が揃うメッシュだけを対象とし、欠損要素の重みは他要素へ再配分しません。</div>
                {(Object.keys(weights) as WeightKey[]).map((key) => <label className=\"weight-row\" key={key}><span>{WEIGHT_LABELS[key]}</span><input type=\"range\" min=\"0\" max=\"50\" step=\"1\" value={weights[key]} onChange={(event) => setWeights((current) => ({ ...current, [key]: Number(event.target.value) }))} /><output>{weights[key]}%</output></label>)}
                <button className=\"reset-button\" onClick={() => setWeights(DEFAULT_WEIGHTS)}>デフォルトに戻す</button>
              </div>
            </details>
""",
"""            <details className=\"advanced-card\">
              <summary><span><span className=\"section-kicker\">ADVANCED</span><b>高度な分析</b></span><span>⌄</span></summary>
              <div className=\"advanced-body\">
                <div className=\"advanced-note\">政策優先度はAnalysis Core v4で確定した公開済み <code>evacuation_difficulty_score</code> をそのまま表示します。探索的固定重みは「津波25 / 要配慮人口20 / 徒歩25 / 経路曝露15 / 収容負荷15」で、公式基準ではありません。ブラウザ側で重み変更・再正規化は行いません。容量・年齢等が欠損するメッシュは <code>score_status</code> に従い数値スコアを表示しません。</div>
              </div>
            </details>
""",
    'replace misleading weight controls',
)

for forbidden in [
    'scoreForRow(',
    'setWeights(',
    "toNumber(row, 'capacity_pressure')",
    'STEP 4再計算待ち',
    '浸水区間の部分着色はSTEP 3後',
]:
    if forbidden in text:
        raise SystemExit(f'forbidden legacy UI contract remains: {forbidden}')

for required in [
    "toNumber(row, 'evacuation_difficulty_score')",
    "toNumber(row, 'capacity_pressure_area_weighted')",
    'route_inundated_segments',
    "id: 'route-inundated-line'",
    '赤線＝STEP 3で判定した津波浸水区間',
]:
    if required not in text:
        raise SystemExit(f'required v4 UI contract missing: {required}')

path.write_text(text, encoding='utf-8')
print('post-v4 UI hardening patch applied')
