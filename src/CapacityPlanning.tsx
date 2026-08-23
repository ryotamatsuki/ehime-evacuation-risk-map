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

type RootCauseKey = 'route_unavailable' | 'unknown_capacity_only' | 'candidate_limit_recoverable' | 'known_capacity_saturation'
type RootCauseSummary = { unserved_demand: number; share_of_k10_unserved: number }
type Step10Summary = {
  release_gate: string
  canonical_analysis_source_sha: string
  baseline_k10_unserved_demand: number
  k30_residual_unserved_demand: number
  decomposition_sum: number
  decomposition_error: number
  root_causes: Record<RootCauseKey, RootCauseSummary>
  candidate_limit_sensitivity: Record<'10' | '20' | '30', { unserved_demand: number; served_demand: number; served_share: number; mean_additional_walking_distance_m_per_served_demand: number }>
  candidate_limit_net_gains: { k10_to_k20: number; k20_to_k30: number; k10_to_k30: number }
  route_unavailable_meshes_k30: number
  unknown_capacity_only_meshes_k30: number
  known_capacity_saturation_meshes_k30: number
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

type CapacityGapRow = {
  gap_rank: number
  shelter_key: string
  shelter_common_id: string
  shelter_name: string
  affected_meshes: number
  residual_unserved_exposure: number
  nearest_gap_unserved_demand: number
  min_candidate_rank: number
}

type MunicipalityRootCauseRow = {
  municipality_code: string
  municipality: string
  k10_unserved_demand: number
  k20_unserved_demand: number
  k30_unserved_demand: number
  candidate_limit_recoverable_k10_to_k30: number
  route_unavailable_residual: number
  unknown_capacity_only_residual: number
  known_capacity_saturation_residual: number
}

type CapacityMetadata = {
  version: string
  canonical_analysis_source_sha: string
  canonical_assets_modified: boolean
  root_cause_candidate_limits?: number[]
}

interface Props { open: boolean; onClose: () => void }

const pct = (value: number) => `${formatNumber(value * 100, 1)}%`
const shelterLabel = (key: string) => key.split('||').slice(1).join('||') || key

const ROOT_CAUSE_LABELS: Record<RootCauseKey, { title: string; note: string }> = {
  route_unavailable: { title: '経路候補なし', note: '道路ネットワーク上で到達候補がないため、容量増強だけでは解消しません。' },
  unknown_capacity_only: { title: '容量不明候補のみ', note: 'K=30まで到達候補はありますが、公表収容人数が不明です。0人扱いはしません。' },
  candidate_limit_recoverable: { title: '遠方候補で回収', note: 'K=10からK=30へ候補を広げることで純減する未収容量です。新設推奨ではありません。' },
  known_capacity_saturation: { title: '既知容量の不足', note: 'K=30でも容量既知候補がある一方、strict配分で未収容が残る部分です。' },
}

export default function CapacityPlanning({ open, onClose }: Props) {
  const [step8, setStep8] = useState<Step8Summary | null>(null)
  const [step9, setStep9] = useState<Step9Summary | null>(null)
  const [step10, setStep10] = useState<Step10Summary | null>(null)
  const [plan, setPlan] = useState<PlanRow[]>([])
  const [gaps, setGaps] = useState<CapacityGapRow[]>([])
  const [municipalityCauses, setMunicipalityCauses] = useState<MunicipalityRootCauseRow[]>([])
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
        const [s8, s9, s10, p, g, municipalities, m] = await Promise.all([
          loadJson<Step8Summary>(`${DATA_BASE}data/capacity-planning/step8-summary.json`),
          loadJson<Step9Summary>(`${DATA_BASE}data/capacity-planning/step9-summary.json`),
          loadJson<Step10Summary>(`${DATA_BASE}data/capacity-planning/step10-summary.json`),
          loadJson<PlanRow[]>(`${DATA_BASE}data/capacity-planning/step9-plan.json`),
          loadJson<CapacityGapRow[]>(`${DATA_BASE}data/capacity-planning/step10-capacity-data-gaps.json`),
          loadJson<MunicipalityRootCauseRow[]>(`${DATA_BASE}data/capacity-planning/step10-municipality-summary.json`),
          loadJson<CapacityMetadata>(`${DATA_BASE}data/capacity-planning/metadata.json`),
        ])
        if (!active) return
        if (s8.release_gate !== 'PASS' || s9.release_gate !== 'PASS' || s10.release_gate !== 'PASS' || m.canonical_assets_modified) throw new Error('STEP 8–10 public release gate failed')
        if (Math.abs(s10.decomposition_sum - s10.baseline_k10_unserved_demand) > 1e-6 || Math.abs(s10.decomposition_error) > 1e-6) throw new Error('STEP 10 root-cause decomposition does not close')
        setStep8(s8); setStep9(s9); setStep10(s10); setPlan(p); setGaps(g); setMunicipalityCauses(municipalities)
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
  const topGaps = useMemo(() => gaps.slice(0, 10), [gaps])
  const topMunicipalities = useMemo(() => municipalityCauses.filter((row) => row.k10_unserved_demand > 0).slice(0, 10), [municipalityCauses])

  if (!open) return null
  return (
    <div className="simulation-backdrop" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <section className="simulation-panel capacity-planning-panel" role="dialog" aria-modal="true" aria-label="容量制約付き避難配分・投資最適化・未収容原因分析">
        <header className="simulation-header capacity-planning-header">
          <div><span className="simulation-eyebrow">STEP 8–10 / CAPACITY & ROOT-CAUSE PLANNING</span><h2>満員を考慮すると、どこへ逃げる？なぜ未収容が残る？</h2><p>複数の到達可能避難先と公表収容人数を使った、canonical v4とは独立した政策シナリオです。</p></div>
          <button type="button" className="simulation-close" onClick={onClose} aria-label="容量配分を閉じる">×</button>
        </header>
        <div className="simulation-warning"><strong>Analysis Core v4 は変更しません。</strong><span>収容人数不明の避難場所は0人扱いせずstrict配分から除外します。需要の分割配分は計画モデルであり、実際の避難誘導を予測するものではありません。STEP 10のK=20/30は候補探索感度であり、新設適地の推奨ではありません。</span></div>
        {loading && <div className="simulation-state">容量制約シナリオを読み込んでいます…</div>}
        {error && <div className="simulation-state error">データを読み込めませんでした：{error}</div>}
        {!loading && !error && area && full && step9 && step10 && (
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

            <div className="capacity-planning-section"><h3>STEP 10　未収容 {formatNumber(step10.baseline_k10_unserved_demand, 1)} の原因分解</h3><p>4つの原因は相互排他的な集計契約で、合計がK=10未収容と完全一致します。K=30でも残る部分と、候補拡張だけで回収できる部分を分離します。</p></div>
            <div className="step10-cause-grid" data-testid="step10-decomposition">
              {(Object.keys(ROOT_CAUSE_LABELS) as RootCauseKey[]).map((key) => {
                const cause = step10.root_causes[key]
                const label = ROOT_CAUSE_LABELS[key]
                return <article key={key} data-cause={key}><span>{label.title}</span><strong>{formatNumber(cause.unserved_demand, 1)}</strong><small>{pct(cause.share_of_k10_unserved)} ／ {label.note}</small></article>
              })}
            </div>
            <div className="capacity-planning-compare step10-sensitivity">
              <article><h4>候補数感度</h4><dl><div><dt>K=10 未収容</dt><dd>{formatNumber(step10.candidate_limit_sensitivity['10'].unserved_demand, 1)}</dd></div><div><dt>K=20 未収容</dt><dd>{formatNumber(step10.candidate_limit_sensitivity['20'].unserved_demand, 1)}</dd></div><div><dt>K=30 未収容</dt><dd data-testid="step10-k30-unserved">{formatNumber(step10.candidate_limit_sensitivity['30'].unserved_demand, 1)}</dd></div><div><dt>K10→K30 純改善</dt><dd>{formatNumber(step10.candidate_limit_net_gains.k10_to_k30, 1)}</dd></div></dl></article>
              <article><h4>K=30残存メッシュ</h4><dl><div><dt>経路候補なし</dt><dd>{step10.route_unavailable_meshes_k30}メッシュ</dd></div><div><dt>容量不明候補のみ</dt><dd>{step10.unknown_capacity_only_meshes_k30}メッシュ</dd></div><div><dt>既知容量不足</dt><dd>{step10.known_capacity_saturation_meshes_k30}メッシュ</dd></div><div><dt>分解誤差</dt><dd>{formatNumber(step10.decomposition_error, 6)}</dd></div></dl></article>
            </div>
            <div className="capacity-planning-section"><h3>容量データを確認すると効果が大きい避難場所</h3><p>「残存する容量不明メッシュ」の候補に現れる施設を、影響未収容需要で順位付けしています。複数候補への露出は重複し得るため、合計値としては使いません。</p></div>
            <div className="simulation-table-wrap"><table className="simulation-table"><thead><tr><th>順位 / 避難場所</th><th>影響メッシュ</th><th>未収容露出</th><th>最寄りギャップ分</th><th>最小候補順位</th></tr></thead><tbody>{topGaps.map((row) => <tr key={row.shelter_key}><td><b>{row.gap_rank}. {row.shelter_name || shelterLabel(row.shelter_key)}</b><small>{row.shelter_common_id}</small></td><td>{row.affected_meshes}</td><td>{formatNumber(row.residual_unserved_exposure, 1)}</td><td>{formatNumber(row.nearest_gap_unserved_demand, 1)}</td><td>{row.min_candidate_rank}</td></tr>)}</tbody></table></div>
            <div className="capacity-planning-section"><h3>市町別の未収容原因</h3><p>K=10の未収容が大きい市町から表示します。K=30残存の原因を経路・容量データ・既知容量に分離します。</p></div>
            <div className="simulation-table-wrap"><table className="simulation-table"><thead><tr><th>市町</th><th>K=10</th><th>K=30</th><th>候補拡張で回収</th><th>経路なし</th><th>容量不明</th><th>既知容量不足</th></tr></thead><tbody>{topMunicipalities.map((row) => <tr key={row.municipality_code}><td><b>{row.municipality}</b></td><td>{formatNumber(row.k10_unserved_demand, 1)}</td><td>{formatNumber(row.k30_unserved_demand, 1)}</td><td>{formatNumber(row.candidate_limit_recoverable_k10_to_k30, 1)}</td><td>{formatNumber(row.route_unavailable_residual, 1)}</td><td>{formatNumber(row.unknown_capacity_only_residual, 1)}</td><td>{formatNumber(row.known_capacity_saturation_residual, 1)}</td></tr>)}</tbody></table></div>

            <div className="capacity-planning-section"><h3>STEP 9　追加収容力の最適配分</h3><p>県全体で追加できる収容力を上限として、既存の容量既知避難場所への配分を同じネットワーク上で大域最適化します。</p></div>
            <div className="simulation-controls"><span>追加収容力budget</span><div className="simulation-option-group">{step9.budgets.map((v) => <button type="button" key={v} className={budget === v ? 'active' : ''} onClick={() => setBudget(v)} aria-pressed={budget === v}>+{formatNumber(v)}人</button>)}</div></div>
            {areaBudget && fullBudget && <div className="simulation-kpis capacity-planning-kpis"><article><span>実際に使う追加容量</span><strong data-testid="step9-capacity-used">{formatNumber(areaBudget.added_capacity_used, 1)}</strong><small>budget上限 {formatNumber(budget)}</small></article><article><span>未収容の削減</span><strong data-testid="step9-unserved-reduction">{formatNumber(areaBudget.unserved_demand_reduction_vs_no_investment, 1)}</strong><small>area-weighted</small></article><article><span>投資対象</span><strong>{areaBudget.investment_shelter_count}施設</strong><small>容量既知の既存施設のみ</small></article><article><span>全人口ケースの未収容削減</span><strong>{formatNumber(fullBudget.unserved_demand_reduction_vs_no_investment, 1)}</strong><small>需要感度</small></article></div>}
            {robustness && <div className="capacity-planning-robustness"><strong>需要仮定を跨ぐ頑健性</strong><span>投資先Jaccard {formatNumber(robustness.shelter_jaccard, 2)} ／ 共通追加容量 {formatNumber(robustness.shared_capacity, 1)}（budgetの{pct(robustness.shared_capacity_share_of_budget)}）</span></div>}
            <div className="simulation-table-wrap"><table className="simulation-table"><thead><tr><th>area-weighted 推奨避難場所</th><th>追加容量</th><th>配分需要</th><th>実効容量</th><th>利用率</th></tr></thead><tbody>{ranked.map((row) => <tr key={`${row.budget}-${row.shelter_key}`}><td><b>{shelterLabel(row.shelter_key)}</b><small>{row.shelter_key.split('||')[0]}</small></td><td>+{formatNumber(row.added_capacity_used, 1)}</td><td>{formatNumber(row.allocated_demand, 1)}</td><td>{formatNumber(row.effective_capacity, 1)}</td><td>{row.utilization == null ? '—' : pct(row.utilization)}</td></tr>)}</tbody></table></div>
            <footer className="simulation-footer"><span>モデル: capacity-constrained min-cost flow</span><span>canonical source: {metadata?.canonical_analysis_source_sha?.slice(0, 8)}</span><span>K=10 production / K=20・30 root-cause sensitivity</span></footer>
          </>
        )}
      </section>
    </div>
  )
}
