import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'
import { bbox, centroid } from '@turf/turf'

type JsonRecord = Record<string, unknown>
type MetricId = 'score' | 'tsunami' | 'aging65' | 'aging75' | 'walking' | 'route' | 'capacity'
type WeightKey = 'tsunami_exposure' | 'vulnerable_population' | 'walking_accessibility' | 'route_inundation_exposure' | 'shelter_capacity_pressure'
type Coordinate = [number, number]

interface GeoFeature {
  type: 'Feature'
  geometry: { type: string; coordinates: unknown }
  properties: JsonRecord
}

interface GeoCollection {
  type: 'FeatureCollection'
  features: GeoFeature[]
}

interface AssetIndexItem {
  municipality_code: string
  municipality: string
  file: string
  feature_count: number
}

interface RiskRow extends JsonRecord {
  mesh_id: string
  municipality_code: string
  municipality: string
}

const BASE = import.meta.env.BASE_URL
const HIGH_RISK_THRESHOLD = 50
const GSI_TSUNAMI_TILES = 'https://disaportaldata.gsi.go.jp/raster/04_tsunami_newlegend_pref_data/38/{z}/{x}/{y}.png'

const DEFAULT_WEIGHTS: Record<WeightKey, number> = {
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

const METRICS: Record<MetricId, { label: string; property: string; unit: string; description: string }> = {
  score: { label: '総合避難困難度', property: 'map_score', unit: '点', description: '5要素をPoC用探索的重みで合成' },
  tsunami: { label: '津波浸水曝露', property: 'tsunami_exposure_score', unit: '点', description: '500mメッシュ内の津波浸水サンプル割合' },
  aging65: { label: '65歳以上人口割合', property: 'aging_rate_65plus_pct', unit: '%', description: '2020年国勢調査500mメッシュ' },
  aging75: { label: '75歳以上人口割合', property: 'aging_rate_75plus_pct', unit: '%', description: '2020年国勢調査500mメッシュ' },
  walking: { label: '避難場所までの徒歩時間', property: 'walking_accessibility_component', unit: '点', description: '徒歩距離を全国ではなく本分析範囲内で正規化' },
  route: { label: '避難経路津波曝露', property: 'route_inundation_exposure_component', unit: '点', description: '経路ポリラインを津波タイルへサンプル' },
  capacity: { label: '避難場所収容負荷', property: 'shelter_capacity_pressure_component', unit: '点', description: '仮割当人口 / 想定収容人数' },
}

const componentColumns: Record<WeightKey, string> = {
  tsunami_exposure: 'tsunami_exposure_component',
  vulnerable_population: 'vulnerable_population_component',
  walking_accessibility: 'walking_accessibility_component',
  route_inundation_exposure: 'route_inundation_exposure_component',
  shelter_capacity_pressure: 'shelter_capacity_pressure_component',
}

const toNumber = (row: JsonRecord | undefined, key: string): number | null => {
  if (!row) return null
  const value = row[key]
  if (value === null || value === undefined || value === '') return null
  const number = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(number) ? number : null
}

const toText = (row: JsonRecord | undefined, key: string): string | null => {
  if (!row) return null
  const value = row[key]
  return value === null || value === undefined || value === '' ? null : String(value)
}

const formatNumber = (value: number | null, digits = 0): string => {
  if (value === null || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('ja-JP', { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(value)
}

const formatPercent = (value: number | null, digits = 1): string => value === null ? '—' : `${formatNumber(value * 100, digits)}%`
const formatMinutes = (seconds: number | null): string => seconds === null ? '—' : `${formatNumber(seconds / 60, 1)}分`

async function loadJson<T>(path: string): Promise<T> {
  const response = await fetch(path)
  if (!response.ok) throw new Error(`${response.status} ${path}`)
  return response.json() as Promise<T>
}

const scoreForRow = (row: RiskRow, weights: Record<WeightKey, number>): number | null => {
  let numerator = 0
  let denominator = 0
  for (const key of Object.keys(componentColumns) as WeightKey[]) {
    const value = toNumber(row, componentColumns[key])
    if (value === null) continue
    numerator += value * weights[key]
    denominator += weights[key]
  }
  return denominator > 0 ? numerator / denominator : null
}

const reasonsForRow = (row: RiskRow, weights: Record<WeightKey, number>) => {
  return (Object.keys(componentColumns) as WeightKey[])
    .map((key) => {
      const value = toNumber(row, componentColumns[key])
      return value === null ? null : {
        key,
        label: WEIGHT_LABELS[key],
        component: value,
        contribution: value * weights[key] / 100,
      }
    })
    .filter((item): item is { key: WeightKey; label: string; component: number; contribution: number } => item !== null)
    .sort((a, b) => b.contribution - a.contribution)
}

const centerOfFeature = (feature: GeoFeature): Coordinate | null => {
  try {
    const result = centroid(feature as never).geometry.coordinates as unknown as Coordinate
    return result
  } catch {
    return null
  }
}

function App() {
  const mapElement = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const [mapReady, setMapReady] = useState(false)
  const [mapError, setMapError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [populationFeatures, setPopulationFeatures] = useState<GeoFeature[]>([])
  const [riskRows, setRiskRows] = useState<RiskRow[]>([])
  const [shelters, setShelters] = useState<GeoCollection>({ type: 'FeatureCollection', features: [] })
  const [municipalities, setMunicipalities] = useState<AssetIndexItem[]>([])
  const [municipality, setMunicipality] = useState('all')
  const [metric, setMetric] = useState<MetricId>('score')
  const [selectedMeshId, setSelectedMeshId] = useState<string | null>(null)
  const [showTsunami, setShowTsunami] = useState(false)
  const [showShelters, setShowShelters] = useState(true)
  const [showRoute, setShowRoute] = useState(true)
  const [weights, setWeights] = useState(DEFAULT_WEIGHTS)
  const [mobilePanelOpen, setMobilePanelOpen] = useState(false)

  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        setLoading(true)
        const [populationIndex, riskIndex, routeIndex, shelterData] = await Promise.all([
          loadJson<AssetIndexItem[]>(`${BASE}data/population/index.json`),
          loadJson<AssetIndexItem[]>(`${BASE}data/risk/index.json`),
          loadJson<AssetIndexItem[]>(`${BASE}data/routes/index.json`),
          loadJson<GeoCollection>(`${BASE}data/shelters/shelters.geojson`),
        ])
        const populationDocuments = await Promise.all(populationIndex.map((item) => loadJson<GeoCollection>(`${BASE}data/${item.file}`)))
        const riskDocuments = await Promise.all(riskIndex.map((item) => loadJson<RiskRow[]>(`${BASE}data/${item.file}`)))
        const routeDocuments = await Promise.all(routeIndex.map((item) => loadJson<RiskRow[]>(`${BASE}data/${item.file}`)))
        const routeByMesh = new Map(routeDocuments.flat().map((row) => [String(row.mesh_id), row]))
        if (!active) return
        setMunicipalities(populationIndex)
        setPopulationFeatures(populationDocuments.flatMap((document) => document.features))
        setRiskRows(riskDocuments.flat().map((row) => ({ ...row, ...(routeByMesh.get(String(row.mesh_id)) ?? {}) })))
        setShelters(shelterData)
      } catch (error) {
        if (active) setLoadError(error instanceof Error ? error.message : 'データの読み込みに失敗しました')
      } finally {
        if (active) setLoading(false)
      }
    }
    load()
    return () => { active = false }
  }, [])

  const riskByMesh = useMemo(() => new Map(riskRows.map((row) => [String(row.mesh_id), row])), [riskRows])
  const mapData = useMemo<GeoCollection>(() => ({
    type: 'FeatureCollection',
    features: populationFeatures.map((feature) => {
      const meshId = String(feature.properties.mesh_id)
      const risk = riskByMesh.get(meshId)
      const properties: JsonRecord = { ...feature.properties }
      if (risk) {
        for (const key of ['tsunami_exposure_score', 'aging_rate_65plus', 'aging_rate_75plus', 'network_distance_m', 'walking_accessibility_component', 'route_inundation_exposure_component', 'shelter_capacity_pressure_component', 'data_completeness', 'municipality_code']) {
          properties[key] = risk[key] ?? null
        }
        properties.map_score = scoreForRow(risk, weights)
        properties.aging_rate_65plus_pct = (toNumber(risk, 'aging_rate_65plus') ?? 0) * 100
        properties.aging_rate_75plus_pct = (toNumber(risk, 'aging_rate_75plus') ?? 0) * 100
      }
      return { ...feature, properties }
    }),
  }), [populationFeatures, riskByMesh, weights])

  const filteredRows = useMemo(() => municipality === 'all' ? riskRows : riskRows.filter((row) => String(row.municipality_code) === municipality), [riskRows, municipality])
  const selectedRow = selectedMeshId ? riskByMesh.get(selectedMeshId) ?? null : null
  const selectedFeature = selectedMeshId ? populationFeatures.find((feature) => String(feature.properties.mesh_id) === selectedMeshId) ?? null : null

  const summary = useMemo(() => {
    const population = filteredRows.reduce((sum, row) => sum + (toNumber(row, 'total_population') ?? 0), 0)
    const highRiskRows = filteredRows.filter((row) => (scoreForRow(row, weights) ?? 0) >= HIGH_RISK_THRESHOLD)
    const highRiskPopulation = highRiskRows.reduce((sum, row) => sum + (toNumber(row, 'total_population') ?? 0), 0)
    const over500Rows = filteredRows.filter((row) => (toNumber(row, 'network_distance_m') ?? Infinity) > 500)
    const over500Population = over500Rows.reduce((sum, row) => sum + (toNumber(row, 'total_population') ?? 0), 0)
    const timePairs = filteredRows.map((row) => ({ time: toNumber(row, 'walking_time_1_0_s'), population: toNumber(row, 'total_population') ?? 0 })).filter((item) => item.time !== null)
    const weightedTime = timePairs.length ? timePairs.reduce((sum, item) => sum + (item.time as number) * item.population, 0) / Math.max(1, timePairs.reduce((sum, item) => sum + item.population, 0)) : null
    const shelterPressure = new Set(filteredRows.filter((row) => (toNumber(row, 'capacity_pressure') ?? 0) > 1).map((row) => toText(row, 'nearest_shelter_id')).filter((id): id is string => id !== null))
    const completeness = filteredRows.length ? filteredRows.reduce((sum, row) => sum + (toNumber(row, 'data_completeness') ?? 0), 0) / filteredRows.length : null
    return { population, meshCount: filteredRows.length, highRiskPopulation, highRiskRate: population ? highRiskPopulation / population : null, over500Population, weightedTime, shelterPressure: shelterPressure.size, highRiskMeshCount: highRiskRows.length, completeness }
  }, [filteredRows, weights])

  const topRows = useMemo(() => [...filteredRows].sort((a, b) => (scoreForRow(b, weights) ?? -1) - (scoreForRow(a, weights) ?? -1)).slice(0, 6), [filteredRows, weights])

  useEffect(() => {
    if (!mapElement.current) return
    const webglAvailable = (() => {
      try {
        const canvas = document.createElement('canvas')
        return Boolean(canvas.getContext('webgl2') || canvas.getContext('webgl'))
      } catch {
        return false
      }
    })()
    if (!webglAvailable) {
      setMapError('このブラウザではWebGLが無効のため、インタラクティブ地図を表示できません。データ詳細とランキングは利用できます。')
      return
    }
    let map: maplibregl.Map | null = null
    try {
      map = new maplibregl.Map({
        container: mapElement.current,
        style: {
          version: 8,
          sources: {},
          layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#dce8ee' } }],
        },
        center: [132.8, 33.8],
        zoom: 8.4,
        minZoom: 6,
        maxZoom: 18,
        renderWorldCopies: false,
        attributionControl: false,
      })
      mapRef.current = map
      map.on('load', () => setMapReady(true))
      map.on('error', (event) => {
        if (event.error?.message?.includes('04_tsunami')) return
        console.warn('MapLibre error', event.error)
      })
    } catch {
      setMapError('地図エンジンを初期化できませんでした。WebGLを有効にしたブラウザで再度お試しください。')
    }
    return () => {
      map?.remove()
      mapRef.current = null
      setMapReady(false)
    }
  }, [])

  const fillExpression = useCallback((metricId: MetricId) => {
    const property = METRICS[metricId].property
    return ['case', ['==', ['get', property], null], '#b8c4ce', ['interpolate', ['linear'], ['coalesce', ['get', property], -1], 0, '#1d9b83', 25, '#8bc34a', 50, '#facc15', 75, '#f97316', 100, '#bf2f3e']]
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady) return
    if (!map.getSource('mesh')) {
      map.addSource('mesh', { type: 'geojson', data: mapData as never })
      map.addSource('shelters', { type: 'geojson', data: shelters as never })
      map.addSource('route', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } as never })
      map.addSource('tsunami', { type: 'raster', tiles: [GSI_TSUNAMI_TILES], tileSize: 256, attribution: '出典：ハザードマップポータルサイト' })
      map.addLayer({ id: 'tsunami-raster', type: 'raster', source: 'tsunami', layout: { visibility: showTsunami ? 'visible' : 'none' }, paint: { 'raster-opacity': 0.58 } })
      map.addLayer({ id: 'mesh-fill', type: 'fill', source: 'mesh', paint: { 'fill-color': fillExpression(metric) as never, 'fill-opacity': 0.76, 'fill-outline-color': '#ffffff' } })
      map.addLayer({ id: 'mesh-selected', type: 'line', source: 'mesh', paint: { 'line-color': '#102a43', 'line-width': 3.2 }, filter: ['==', ['get', 'mesh_id'], ''] })
      map.addLayer({ id: 'shelters-circle', type: 'circle', source: 'shelters', layout: { visibility: showShelters ? 'visible' : 'none' }, paint: { 'circle-color': '#075985', 'circle-radius': ['interpolate', ['linear'], ['zoom'], 7, 3, 13, 6], 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.2, 'circle-opacity': 0.94 } })
      map.addLayer({ id: 'route-line', type: 'line', source: 'route', layout: { visibility: showRoute ? 'visible' : 'none' }, paint: { 'line-color': '#102a43', 'line-width': 4, 'line-opacity': 0.92, 'line-dasharray': [1.2, 1.1] } })
      map.on('click', 'mesh-fill', (event) => {
        const feature = event.features?.[0]
        const id = feature?.properties?.mesh_id
        if (id !== undefined && id !== null) {
          setSelectedMeshId(String(id))
          setMobilePanelOpen(true)
        }
      })
      map.on('mouseenter', 'mesh-fill', () => { map.getCanvas().style.cursor = 'pointer' })
      map.on('mouseleave', 'mesh-fill', () => { map.getCanvas().style.cursor = '' })
    } else {
      (map.getSource('mesh') as maplibregl.GeoJSONSource).setData(mapData as never)
    }
    const meshSource = map.getSource('mesh') as maplibregl.GeoJSONSource | undefined
    if (meshSource) meshSource.setData(mapData as never)
    map.setPaintProperty('mesh-fill', 'fill-color', fillExpression(metric) as never)
    map.setFilter('mesh-fill', municipality === 'all' ? null : ['==', ['get', 'municipality_code'], municipality])
    map.setFilter('mesh-selected', selectedMeshId ? ['==', ['get', 'mesh_id'], selectedMeshId] : ['==', ['get', 'mesh_id'], ''])
    map.setLayoutProperty('tsunami-raster', 'visibility', showTsunami ? 'visible' : 'none')
    map.setLayoutProperty('shelters-circle', 'visibility', showShelters ? 'visible' : 'none')
    map.setLayoutProperty('route-line', 'visibility', showRoute && selectedRow ? 'visible' : 'none')
    const coordinates = selectedRow?.route_coordinates
    const routeCoordinates = Array.isArray(coordinates) ? coordinates : typeof coordinates === 'string' ? (() => { try { return JSON.parse(coordinates) } catch { return [] } })() : []
    const routeSource = map.getSource('route') as maplibregl.GeoJSONSource | undefined
    if (routeSource) {
      routeSource.setData({ type: 'FeatureCollection', features: routeCoordinates.length >= 2 ? [{ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: routeCoordinates } }] : [] } as never)
    }
  }, [fillExpression, mapData, mapReady, metric, municipality, selectedMeshId, selectedRow, showRoute, showShelters, showTsunami, shelters])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady || !selectedFeature) return
    const center = centerOfFeature(selectedFeature)
    if (center) map.flyTo({ center, zoom: Math.max(map.getZoom(), 11), duration: 550 })
  }, [mapReady, selectedFeature])

  useEffect(() => {
    if (municipality !== 'all' && selectedRow && String(selectedRow.municipality_code) !== municipality) setSelectedMeshId(null)
  }, [municipality, selectedRow])

  const selectRow = (row: RiskRow) => {
    setSelectedMeshId(String(row.mesh_id))
    setMobilePanelOpen(true)
  }

  const selectedReasons = selectedRow ? reasonsForRow(selectedRow, weights) : []
  const currentScore = selectedRow ? scoreForRow(selectedRow, weights) : null
  const currentMetric = METRICS[metric]

  if (loadError) {
    return <div className="fatal-state"><strong>データを読み込めませんでした</strong><span>{loadError}</span><button onClick={() => window.location.reload()}>再読み込み</button></div>
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <div className="eyebrow">EHIME / NANKAI TROUGH · POLICY EXPLORER</div>
          <h1>南海トラフ・本当に逃げられるかマップ</h1>
          <p>人口・避難距離・経路曝露・避難場所容量から見る愛媛の避難困難度</p>
        </div>
        <div className="header-status"><span className="status-dot" />{loading ? 'データ読込中' : '14市町 / 5,821メッシュ'}</div>
      </header>

      <main className="workspace">
        <section className="map-panel" aria-label="避難困難度マップ">
          {mapError ? <div className="map-fallback"><div className="map-fallback-card"><div className="section-kicker">MAP ENGINE FALLBACK</div><h2>地図表示はWebGL対応ブラウザでご利用ください</h2><p>{mapError}</p><div className="fallback-stat"><strong>{formatNumber(summary.meshCount)}</strong><span>対象500mメッシュ</span><strong>{formatNumber(summary.highRiskMeshCount)}</strong><span>高リスクメッシュ</span></div><p className="micro-note">右側の条件・ランキング・メッシュ詳細はこの環境でも利用できます。</p></div></div> : <div ref={mapElement} className="map-canvas" />}
          <div className="map-overlay top-left">
            <label className="map-select-label" htmlFor="municipality">対象市町</label>
            <select id="municipality" value={municipality} onChange={(event) => setMunicipality(event.target.value)}>
              <option value="all">愛媛県沿岸14市町（全体）</option>
              {municipalities.map((item) => <option value={item.municipality_code} key={item.municipality_code}>{item.municipality}</option>)}
            </select>
          </div>
          <div className="map-overlay top-right map-actions">
            <label><input type="checkbox" checked={showTsunami} onChange={(event) => setShowTsunami(event.target.checked)} />津波浸水想定</label>
            <label><input type="checkbox" checked={showShelters} onChange={(event) => setShowShelters(event.target.checked)} />津波対応避難場所</label>
            <label><input type="checkbox" checked={showRoute} onChange={(event) => setShowRoute(event.target.checked)} />選択メッシュの経路</label>
          </div>
          <div className="map-overlay bottom-left legend-box">
            <div className="legend-title">{currentMetric.label} <span>{currentMetric.unit}</span></div>
            <div className="legend-gradient" />
            <div className="legend-scale"><span>0</span><span>25</span><span>50</span><span>75</span><span>100</span></div>
            <div className="legend-note">{currentMetric.description}</div>
          </div>
          <div className="map-attribution">出典：ハザードマップポータルサイト　/　© OpenStreetMap contributors · ODbL</div>
        </section>

        <aside className={`control-panel ${mobilePanelOpen ? 'mobile-open' : ''}`}>
          <button className="mobile-panel-toggle" onClick={() => setMobilePanelOpen((open) => !open)}>{mobilePanelOpen ? '詳細パネルを閉じる' : '詳細・条件を表示'}</button>
          <div className="panel-scroll">
            <div className="panel-block metric-block">
              <div className="section-kicker">VIEW / LAYER</div>
              <h2>何を重ねて見るか</h2>
              <div className="metric-grid">
                {(Object.keys(METRICS) as MetricId[]).map((id) => <button key={id} className={`metric-button ${metric === id ? 'active' : ''}`} onClick={() => setMetric(id)}>{METRICS[id].label}</button>)}
              </div>
            </div>

            <div className="panel-block summary-block">
              <div className="section-kicker">MUNICIPALITY SUMMARY</div>
              <h2>{municipality === 'all' ? '沿岸14市町の概況' : municipalities.find((item) => item.municipality_code === municipality)?.municipality}</h2>
              <div className="stat-grid">
                <div><span>対象人口</span><strong>{formatNumber(summary.population)}<small>人</small></strong></div>
                <div><span>高リスク人口<em>※</em></span><strong>{formatNumber(summary.highRiskPopulation)}<small>人</small></strong></div>
                <div><span>500m超避難人口</span><strong>{formatNumber(summary.over500Population)}<small>人</small></strong></div>
                <div><span>標準速度・人口加重</span><strong>{formatMinutes(summary.weightedTime)}</strong></div>
                <div><span>収容不足候補施設</span><strong>{formatNumber(summary.shelterPressure)}<small>施設</small></strong></div>
                <div><span>データ完全度</span><strong>{formatPercent(summary.completeness)}</strong></div>
              </div>
              <p className="micro-note">※高リスク人口＝総合スコア50点以上（PoC表示閾値） / 対象メッシュ {formatNumber(summary.meshCount)}件</p>
            </div>

            <details className="panel-block weight-block" open>
              <summary><span><span className="section-kicker">MODEL SETTINGS</span><b>スコアの重みを試す</b></span><span className="summary-caret">⌄</span></summary>
              <div className="exploratory-note">PoC用探索的重み <span>公式な政策基準ではありません</span></div>
              {(Object.keys(weights) as WeightKey[]).map((key) => <label className="weight-row" key={key}><span>{WEIGHT_LABELS[key]}</span><input type="range" min="0" max="50" step="1" value={weights[key]} onChange={(event) => setWeights((current) => ({ ...current, [key]: Number(event.target.value) }))} /><output>{weights[key]}</output></label>)}
              <button className="reset-button" onClick={() => setWeights(DEFAULT_WEIGHTS)}>初期重みに戻す</button>
              <p className="micro-note">欠損要素があるメッシュは、利用可能な重みで再正規化します。データ完全度を必ず併読してください。</p>
            </details>

            <div className="panel-block ranking-block">
              <div className="section-kicker">TOP MESHES</div>
              <h2>スコアの高いメッシュ</h2>
              <div className="ranking-list">
                {topRows.map((row, index) => <button className="ranking-row" key={String(row.mesh_id)} onClick={() => selectRow(row)}><span className="rank">{String(index + 1).padStart(2, '0')}</span><span className="rank-name">{toText(row, 'municipality')} / {String(row.mesh_id)}</span><strong>{formatNumber(scoreForRow(row, weights), 1)}</strong></button>)}
              </div>
            </div>

            <div className="panel-block detail-block">
              <div className="section-kicker">MESH DETAIL</div>
              <h2>{selectedRow ? `${toText(selectedRow, 'municipality')} / ${selectedRow.mesh_id}` : 'メッシュをクリックしてください'}</h2>
              {selectedRow ? <>
                <div className="score-hero"><div><span>避難困難度</span><strong>{formatNumber(currentScore, 1)}<small> / 100</small></strong></div><span className={`completeness-badge ${toNumber(selectedRow, 'data_completeness') === 1 ? 'complete' : ''}`}>データ完全度 {formatPercent(toNumber(selectedRow, 'data_completeness'))}</span></div>
                <div className="why-box"><h3>なぜ危険？</h3><p>このメッシュでは、現在の重みで寄与度が大きい順に次の要素が表示されています。</p>{selectedReasons.slice(0, 5).map((reason) => <div className="reason-row" key={reason.key}><span>{reason.label}</span><strong>{formatNumber(reason.component, 1)}</strong><i style={{ width: `${Math.min(100, reason.component)}%` }} /></div>)}</div>
                <dl className="detail-list">
                  <Detail label="人口" value={`${formatNumber(toNumber(selectedRow, 'total_population'))}人`} />
                  <Detail label="65歳以上人口 / 割合" value={`${formatNumber(toNumber(selectedRow, 'population_65plus'))}人 / ${formatPercent(toNumber(selectedRow, 'aging_rate_65plus'))}`} />
                  <Detail label="75歳以上人口 / 割合" value={`${formatNumber(toNumber(selectedRow, 'population_75plus'))}人 / ${formatPercent(toNumber(selectedRow, 'aging_rate_75plus'))}`} />
                  <Detail label="津波浸水曝露" value={`${formatPercent(toNumber(selectedRow, 'tsunami_inundation_ratio'))} / 深さクラス ${formatNumber(toNumber(selectedRow, 'tsunami_max_depth_class'))}`} />
                  <Detail label="最寄り津波対応避難場所" value={toText(selectedRow, 'nearest_shelter_id') ?? '—'} />
                  <Detail label="道路距離" value={`${formatNumber(toNumber(selectedRow, 'network_distance_m'), 0)}m`} />
                  <Detail label="徒歩時間（1.0 / 0.62 / 0.5m/s）" value={`${formatMinutes(toNumber(selectedRow, 'walking_time_1_0_s'))} / ${formatMinutes(toNumber(selectedRow, 'walking_time_0_62_s'))} / ${formatMinutes(toNumber(selectedRow, 'walking_time_0_5_s'))}`} />
                  <Detail label="経路の津波浸水距離 / 曝露率" value={`${formatNumber(toNumber(selectedRow, 'route_inundation_distance_m'), 0)}m / ${formatPercent(toNumber(selectedRow, 'route_inundation_ratio'))}`} />
                  <Detail label="仮割当人口 / 想定収容人数" value={`${formatNumber(toNumber(selectedRow, 'assigned_population'))}人 / ${formatNumber(toNumber(selectedRow, 'shelter_capacity'))}人`} />
                  <Detail label="収容負荷" value={toNumber(selectedRow, 'capacity_pressure') === null ? '欠損（0として扱っていません）' : formatNumber(toNumber(selectedRow, 'capacity_pressure'), 2)} />
                </dl>
                <p className="micro-note">収容負荷は「住民がネットワーク距離で最寄りの津波対応避難場所へ避難する」仮定です。実際の避難者数予測ではありません。経路曝露は道路が寸断される確率ではありません。</p>
              </> : <div className="empty-detail">地図上の500mメッシュ、または上のランキングを選択すると、人口・経路・容量・データ完全度を確認できます。</div>}
            </div>

            <div className="panel-block source-block">
              <div className="section-kicker">DATA NOTES</div>
              <h2>出典と読み方</h2>
              <p><strong>人口：</strong>2020年国勢調査500mメッシュ。秘匿・欠損値は0補完せずnullと品質フラグを保持。</p>
              <p><strong>津波：</strong>出典：ハザードマップポータルサイト / ハザードマップポータルサイトを加工して作成。愛媛県の最新法定津波浸水想定は2025年9月2日に変更されていますが、ポータル配信データの愛媛県分の個別反映日は公開ページ上で明示されていません。</p>
              <p><strong>避難場所：</strong>属性は愛媛県「指定緊急避難場所一覧」、座標は国土地理院。未照合施設は地図へ推測表示していません。</p>
              <p><strong>歩行ネットワーク：</strong>OpenStreetMap pedestrian network / © OpenStreetMap contributors / ODbL。標準1.0m/s、観測参考0.62m/s、移動制約シナリオ0.5m/s。</p>
              <p><strong>参照資料：</strong>2026年愛媛県地震被害想定GISは最新公式資料として参照しますが、P0複合リスク演算には使用していません。</p>
            </div>
          </div>
        </aside>
      </main>

      <footer className="disclaimer">本サイトは公開データを用いた政策分析・可視化PoCです。実際の避難行動や避難経路の安全を保証するものではありません。愛媛県の最新津波浸水想定は2025年9月2日に変更されています。災害時及び詳細な防災判断には、愛媛県・各市町等が公表する最新の防災情報・ハザードマップを確認してください。</footer>
    </div>
  )
}

function Detail({ label, value }: { label: string; value: string }) {
  return <div className="detail-row"><dt>{label}</dt><dd>{value}</dd></div>
}

export default App
