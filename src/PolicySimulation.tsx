import { useEffect, useMemo, useState } from 'react'
import { DATA_BASE, formatNumber, loadJson } from './dataContract'
import {
  simulateCapacityAugmentation,
  type SimulationRiskRow,
  type SimulationShelterRow,
} from './policySimulation'
import './policy-simulation.css'

interface AssetIndexItem {
  file: string
}

interface PolicySimulationProps {
  open: boolean
  onClose: () => void
}

const CAPACITY_OPTIONS = [100, 500, 1000]
const formatPressure = (value: number): string => `${formatNumber(value * 100)}%`

export default function PolicySimulation({ open, onClose }: PolicySimulationProps) {
  const [capacityDelta, setCapacityDelta] = useState(500)
  const [riskRows, setRiskRows] = useState<SimulationRiskRow[]>([])
  const [shelters, setShelters] = useState<SimulationShelterRow[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open || riskRows.length) return
    let active = true
    const load = async () => {
      try {
        setLoading(true)
        setError(null)
        const [riskIndex, shelterRows] = await Promise.all([
          loadJson<AssetIndexItem[]>(`${DATA_BASE}data/risk/index.json`),
          loadJson<SimulationShelterRow[]>(`${DATA_BASE}data/shelters/capacity_pressure.json`),
        ])
        const documents = await Promise.all(
          riskIndex.map((item) => loadJson<SimulationRiskRow[]>(`${DATA_BASE}data/${item.file}`)),
        )
        if (!active) return
        setRiskRows(documents.flat())
        setShelters(shelterRows)
      } catch (loadError) {
        if (active) setError(loadError instanceof Error ? loadError.message : 'シミュレーションデータの読み込みに失敗しました')
      } finally {
        if (active) setLoading(false)
      }
    }
    load()
    return () => { active = false }
  }, [open, riskRows.length])

  const simulation = useMemo(
    () => simulateCapacityAugmentation(riskRows, shelters, capacityDelta),
    [capacityDelta, riskRows, shelters],
  )
  const ranked = simulation.results.filter((row) => row.baselineOverCapacity || row.totalScoreReduction > 0).slice(0, 12)

  if (!open) return null

  return (
    <div className="simulation-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <section className="simulation-panel" aria-label="対策シミュレーション" role="dialog" aria-modal="true">
        <header className="simulation-header">
          <div>
            <span className="simulation-eyebrow">STEP 7 / POLICY SIMULATION</span>
            <h2>避難場所の収容人数を増強したら？</h2>
            <p>選択避難先は変えず、仮想的な収容人数増強だけを反映した診断シナリオです。</p>
          </div>
          <button type="button" className="simulation-close" onClick={onClose} aria-label="シミュレーションを閉じる">×</button>
        </header>

        <div className="simulation-warning">
          <strong>Canonical Analysis Core v4 は変更しません。</strong>
          <span>経路の再計算、容量超過者の別避難所への再配分、新設施設の推測は行いません。収容人数未公表の施設もシミュレーション対象外です。</span>
        </div>

        <div className="simulation-controls" aria-label="収容人数増強幅">
          <span>各避難場所の仮想増強</span>
          <div className="simulation-option-group">
            {CAPACITY_OPTIONS.map((option) => (
              <button
                type="button"
                key={option}
                className={capacityDelta === option ? 'active' : ''}
                onClick={() => setCapacityDelta(option)}
                aria-pressed={capacityDelta === option}
              >
                +{formatNumber(option)}人
              </button>
            ))}
          </div>
        </div>

        {loading && <div className="simulation-state">productionデータを読み込んでいます…</div>}
        {error && <div className="simulation-state error">データを読み込めませんでした：{error}</div>}

        {!loading && !error && riskRows.length > 0 && (
          <>
            <div className="simulation-kpis" aria-label="シミュレーション主要指標">
              <article><span>現状の容量超過</span><strong data-testid="simulation-baseline-overload">{simulation.baselineOverCapacityShelters}施設</strong><small>面積按分需要 / 公表容量</small></article>
              <article><span>増強後も容量超過</span><strong data-testid="simulation-after-overload">{simulation.simulatedOverCapacityShelters}施設</strong><small>全施設に+{formatNumber(capacityDelta)}人を仮定</small></article>
              <article><span>100%以下へ改善</span><strong data-testid="simulation-resolved">{simulation.resolvedOverCapacityShelters}施設</strong><small>現状&gt;100% → 増強後≤100%</small></article>
              <article><span>完全スコア対象</span><strong>{simulation.affectedCompleteMeshes}メッシュ</strong><small>欠損277件は順位計算に入れない</small></article>
            </div>

            <div className="simulation-explanation">
              <h3>政策効果候補ランキング</h3>
              <p>5要素が揃うメッシュについて、容量要素だけを増強後の値に置き換えた場合の「政策優先度スコア低下量」の合計で並べています。これは施策比較用の仮想差分で、公式な便益評価ではありません。</p>
            </div>

            <div className="simulation-table-wrap">
              <table className="simulation-table">
                <thead>
                  <tr>
                    <th>避難場所</th>
                    <th>需要</th>
                    <th>現容量</th>
                    <th>現負荷</th>
                    <th>増強後</th>
                    <th>対象メッシュ</th>
                    <th>スコア改善計</th>
                  </tr>
                </thead>
                <tbody>
                  {ranked.map((row) => (
                    <tr key={row.shelterKey} className={row.resolvedOverCapacity ? 'resolved' : ''}>
                      <td><b>{row.name}</b><small>{row.municipalityCode ?? '市町コード不明'}{row.resolvedOverCapacity ? ' · 100%以下へ改善' : ''}</small></td>
                      <td>{formatNumber(row.assignedDemand, 1)}</td>
                      <td>{formatNumber(row.baselineCapacity)} → {formatNumber(row.simulatedCapacity)}</td>
                      <td>{formatPressure(row.baselinePressure)}</td>
                      <td>{formatPressure(row.simulatedPressure)}</td>
                      <td>{row.affectedCompleteMeshes}</td>
                      <td>{formatNumber(row.totalScoreReduction, 2)}点</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <footer className="simulation-footer">
              <span>既知容量の選択避難場所：{simulation.knownCapacityShelters}施設</span>
              <span>表示：政策効果候補 上位{ranked.length}施設</span>
              <span>需要はSTEP 4 area-weighted proxyを固定</span>
            </footer>
          </>
        )}
      </section>
    </div>
  )
}
