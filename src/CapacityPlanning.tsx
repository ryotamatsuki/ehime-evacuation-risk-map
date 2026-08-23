import { useEffect, useMemo, useState } from 'react'
import { DATA_BASE, formatNumber, loadJson } from './dataContract'
import './capacity-planning.css'

type ScenarioSummary = {
  baseline_fixed_assignment_over_capacity_shelters: number
  served_demand: number
  unserved_demand: number
  served_share: number
  meshes_dominant_destination_changed: number
  meshes_split_across_shelters: number
  meshes_with_unknown_capacity_only: number
  demand_allocated_to_rank_gt1_candidate: number
  mean_additional_walking_distance_m_per_served_demand: number
  candidate_limit_sensitivity?: {
    k5_unserved_demand: number
    k10_unserved_demand: number
  }
}

type Step8Summary = {
  release_gate: string
  canonical_analysis_source_sha: string
  scenarios: Record<'area_weighted' | 'full_mesh', ScenarioSummary>
}

type BudgetSummary = {
  budget: number
  added_capacity_used: number
  served_demand: number
  unserved_demand: number
  served_demand_gain_vs_no_investment: number
  unserved_demand_reduction_vs_no_investment: number
  investment_shelter_count: number
}

type Step9Summary = {
  release_gate: string
  budgets: number[]
  scenarios: Record<'area_weighted' | 'full_mesh', { budgets: BudgetSummary[] }>
  cross_scenario_plan_robustness: Record<string, { shelter_jaccard: number; shared_capacity: number; shared_capacity_share_of_budget: number }>
}

type PlanRow = {
  scenario: 'area_weighted' | 'full_mesh'
  budget: number
  shelter_key: string
  added_capacity_used: number
  allocated_demand: number
  effective_capacity: number
  utilization: number | null
  plan_rank: number
}

type CapacityMetadata = {
  version: string
  canonical_analysis_source_sha: string
  canonical_assets_modified: boolean
}

interface Props { open: boolean; onClose: () => void }

const pct = (value: number) => `${formatNumber(value * 100, 1)}%`
const shelterLabel = (key: string) => key.split('||').slice(1).join('||') || key

export default function CapacityPlanning({ open, onClose }: Props) {
  const [step8, setStep8] = useState<Step8Summary | null>(null)
  const [step9, setStep9] = useState<Step9Summary | null>(null)
  const [plan, setPlan] = useState<PlanRow[]>([])
  const [metadata, setMetadata] = useState<CapacityMetadata | null>(null)
  const [budget, setBudget] = useState(1000)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open || step8) return
    let active = true
    const load = async () => {
      setLoading(true); setError(null)
      try {
        const [s8, s9, p, m] = await Promise.all([
          loadJson<Step8Summary>(`${DATA_BASE}data/capacity-planning/step8-summary.json`),
          loadJson<Step9Summary>(`${DATA_BASE}data/capacity-planning/step9-summary.json`),
          loadJson<PlanRow[]>(`${DATA_BASE}data/capacity-planning/step9-plan.json`),
          loadJson<CapacityMetadata>(`${DATA_BASE}data/capacity-planning/metadata.json`),
        ])
        if (!active) return
        if (s8.release_gate !== 'PASS' || s9.release_gate !== 'PASS' || m.canonical_assets_modified) throw new Error('STEP 8/9 public release gate failed')
        setStep8(s8); setStep9(s9); setPlan(p)
        setMetadata(m)
        if (s9.budgets.length && !s9.budgets.includes(budget)) setBudget(s9.budgets[0])
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : '容量配分データの読み込みに失敗しました')
      } finally {
        if (active) setLoading(false)
      }
    }
    load()
    return () => { active = false }
  }, [open, step8, budget])

  const area = step8?.scenarios.area_weighted
  const full = step8?.scenarios.full_mesh
  const areaBudget = step9?.scenarios.area_weighted.budgets.find((row) => row.budget === budget)
  const fullBudget = step9?.scenarios.full_mesh.budgets.find((row) => row.budget === budget)
  const robustness = step9?.cross_scenario_plan_robustness[String(budget)]
  const ranked = useMemo(() => plan.filter((row) => row.scenario === 'area_weighted' && row.budget === budget).sort((a, b) => a.plan_rank - b.plan_rank).slice(0, 12), [plan, budget])

  if (!open) return null
  return (
    <div className="simulation-backdrop" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <section className="simulation-panel capacity-planning-panel" role="dialog" aria-modal="true" aria-label="容量制約付き避難配分・投資最適化">
        <header className="simulation-header capacity-planning-header">
          <div><span className="simulation-eyebrow">STEP 8–9 / CAPACITY-CONSTRAINED PLANNING</span><h2>満員を考慮すると、どこへ逃げる？どこを増強する？</h2><p>複数の到達可能避難先と公表収容人数を使った、canonical v4とは独立した政策シナリオです。</p></div>
          <button type="button" className="simulation-close" onClick={onClose} aria-label="容量配分を閉じる">×</button>
        </header>
        <div className="simulation-warning"><strong>Analysis Core v4 は変更しません。</strong><span>収容人数不明の避難場所は0人扱いせずstrict配分から除外します。需要の分割配分は計画モデルであり、実際の避難誘導を予測するものではありません。STEP 9は既存・容量既知施設への追加収容力だけを最適化し、新設適地や工事費は推測しません。</span></div>
        {loading && <div className="simulation-state">容量制約シナリオを読み込んでいます…</div>}
        {error && <div className="simulation-state error">データを読み込めませんでした：{error}</div>}
        {!loading && !error && area && full && step9 && (
          <>
            <div className="capacity-planning-section"><h3>STEP 8　容量制約付き避難配分</h3><p>まず「できるだけ多く収容」、次に「総徒歩距離を短く」の順でmin-cost flowを解きます。</p></div>
            <div className="simulation-kpis capacity-planning-kpis">
              <article><span>固定配分で容量超過</span><strong data-testid="step8-baseline-overload">{area.baseline_fixed_assignment_over_capacity_shelters}施設</strong><small>従来STEP 4 / area-weighted</small></article>
              <article><span>容量制約後の未収容</span><strong data-testid="step8-unserved">{formatNumber(area.unserved_demand, 1)}</strong><small>people-equivalent</small></article>
              <article><span>第2候補以降へ配分</span><strong>{formatNumber(area.demand_allocated_to_rank_gt1_candidate, 1)}</strong><small>people-equivalent</small></article>
              <article><span>主避難先が変化</span><strong>{area.meshes_dominant_destination_changed}メッシュ</strong><small>容量制約による再配分</small></article>
            </div>
            <div className="capacity-planning-compare">
              <article><h4>面積按分需要</h4><dl><div><dt>収容率</dt><dd>{pct(area.served_share)}</dd></div><div><dt>分割配分</dt><dd>{area.meshes_split_across_shelters}メッシュ</dd></div><div><dt>容量不明候補のみ</dt><dd>{area.meshes_with_unknown_capacity_only}メッシュ</dd></div><div><dt>平均追加徒歩</dt><dd>{formatNumber(area.mean_additional_walking_distance_m_per_served_demand, 1)}m</dd></div></dl></article>
              <article><h4>全メッシュ人口感度</h4><dl><div><dt>収容率</dt><dd>{pct(full.served_share)}</dd></div><div><dt>未収容</dt><dd>{formatNumber(full.unserved_demand, 1)}</dd></div><div><dt>主避難先変化</dt><dd>{full.meshes_dominant_destination_changed}メッシュ</dd></div><div><dt>平均追加徒歩</dt><dd>{formatNumber(full.mean_additional_walking_distance_m_per_served_demand, 1)}m</dd></div></dl></article>
            </div>
            <div className="capacity-planning-section"><h3>STEP 9　追加収容力の最適配分</h3><p>県全体で追加できる収容力を上限として、既存の容量既知避難場所への配分を同じネットワーク上で大域最適化します。</p></div>
            <div className="simulation-controls"><span>追加収容力budget</span><div className="simulation-option-group">{step9.budgets.map((v) => <button type="button" key={v} className={budget === v ? 'active' : ''} onClick={() => setBudget(v)} aria-pressed={budget === v}>+{formatNumber(v)}人</button>)}</div></div>
            {areaBudget && fullBudget && <div className="simulation-kpis capacity-planning-kpis"><article><span>実際に使う追加容量</span><strong data-testid="step9-capacity-used">{formatNumber(areaBudget.added_capacity_used, 1)}</strong><small>budget上限 {formatNumber(budget)}</small></article><article><span>未収容の削減</span><strong data-testid="step9-unserved-reduction">{formatNumber(areaBudget.unserved_demand_reduction_vs_no_investment, 1)}</strong><small>area-weighted</small></article><article><span>投資対象</span><strong>{areaBudget.investment_shelter_count}施設</strong><small>容量既知の既存施設のみ</small></article><article><span>全人口ケースの未収容削減</span><strong>{formatNumber(fullBudget.unserved_demand_reduction_vs_no_investment, 1)}</strong><small>需要感度</small></article></div>}
            {robustness && <div className="capacity-planning-robustness"><strong>需要仮定を跨ぐ頑健性</strong><span>投資先Jaccard {formatNumber(robustness.shelter_jaccard, 2)} ／ 共通追加容量 {formatNumber(robustness.shared_capacity, 1)}（budgetの{pct(robustness.shared_capacity_share_of_budget)}）</span></div>}
            <div className="simulation-table-wrap"><table className="simulation-table"><thead><tr><th>area-weighted 推奨避難場所</th><th>追加容量</th><th>配分需要</th><th>実効容量</th><th>利用率</th></tr></thead><tbody>{ranked.map((row) => <tr key={`${row.budget}-${row.shelter_key}`}><td><b>{shelterLabel(row.shelter_key)}</b><small>{row.shelter_key.split('||')[0]}</small></td><td>+{formatNumber(row.added_capacity_used, 1)}</td><td>{formatNumber(row.allocated_demand, 1)}</td><td>{formatNumber(row.effective_capacity, 1)}</td><td>{row.utilization == null ? '—' : pct(row.utilization)}</td></tr>)}</tbody></table></div>
            <footer className="simulation-footer"><span>モデル: capacity-constrained min-cost flow</span><span>canonical source: {metadata?.canonical_analysis_source_sha?.slice(0, 8)}</span><span>K=10 / K=5 sensitivity gate</span></footer>
          </>
        )}
      </section>
    </div>
  )
}
