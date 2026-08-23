import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'
import { centroid } from '@turf/turf'
import { DATA_BASE, formatNumber, loadJson, recordNumber, recordText, type JsonRecord } from './dataContract'

type ModeId = 'current' | 'elderly' | 'capacity'
type MetricId = 'walking' | 'tsunami' | 'aging65' | 'route' | 'score' | 'capacity'
type Coordinate = [number, number]
type IconName = 'people' | 'clock' | 'elderly' | 'warning' | 'shelter' | 'route' | 'wave' | 'database' | 'distance' | 'walk' | 'school' | 'info'

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

interface AnalysisMetadata extends JsonRecord {
  analysis_version?: string
  target_meshes?: number
  complete_routes?: number
  route_unavailable?: number
  cross_border_routes?: number
}

interface Reason {
  key: string
  label: string
  valueLabel: string
  severity: number
}

interface InundatedSegment {
  depth_class?: number
  coordinates?: Coordinate[]
}

const BASE = DATA_BASE
const GSI_PALE_TILES = 'https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png'
const GSI_TSUNAMI_TILES = 'https://disaportaldata.gsi.go.jp/raster/04_tsunami_newlegend_pref_data/38/{z}/{x}/{y}.png'

const MODE_LABELS: Record<ModeId, string> = {
  current: '現状',
  elderly: '高齢者想定',
  capacity: '避難場所容量',
}

const METRICS: Record<MetricId, { label: string; unit: string; note: string }> = {
  walking: { label: '避難時間', unit: '分', note: '選択中の歩行シナリオで避難場所までに要する時間。経路未成立はグレー表示' },
  tsunami: { label: '津波浸水曝露', unit: '%', note: '500mメッシュ内の津波浸水サンプル割合' },
  aging65: { label: '65歳以上人口割合', unit: '%', note: '2020年国勢調査500mメッシュ。年齢データ欠損はグレー表示' },
  route: { label: '経路津波曝露', unit: '%', note: 'STEP 3の分類済み道路ネットワーク経路における津波浸水割合' },
  score: { label: '政策優先度', unit: '点', note: 'STEP 4で確定した公開済み5要素スコアを直接表示。欠損要素の再配分・ブラウザ再計算は行いません' },
  capacity: { label: '収容負荷', unit: '%', note: 'STEP 4の面積按分需要を避難場所単位に集約し、公表収容人数で除した収容負荷。収容人数未公表は不明のまま表示' },
}

const SCORE_STATUS_LABELS: Record<string, string> = {
  complete: '5要素完全',
  core_only_missing_capacity: '容量不明：4要素coreのみ',
  core_data_incomplete: '年齢構成データ欠損',
  route_unavailable: '経路未成立：数値スコアなし',
}

const ROUTE_STATUS_LABELS: Record<string, string> = {
  complete: '経路成立',
  no_network_path: '道路ネットワーク上の到達経路なし',
  network_coverage_gap: '道路ネットワークcoverage gap',
  no_candidate_shelter_in_aoi: '候補避難場所なし',
  all_candidate_shelters_snap_excluded: '候補避難場所を接続距離基準で除外',
}

const toNumber = recordNumber
const toText = recordText

const formatPercent = (value: number | null, digits = 1): string => value === null ? '—' : `${formatNumber(value * 100, digits)}%`
const formatPct100 = (value: number | null, digits = 0): string => value === null ? '—' : `${formatNumber(value, digits)}%`
const formatMeters = (value: number | null): string => value === null ? '—' : value >= 1000 ? `${formatNumber(value / 1000, 2)}km` : `${formatNumber(value, 0)}m`


const canonicalPolicyScore = (row: RiskRow): number | null => {
  // STEP 4 is the single source of truth for the five-component policy score.
  // The browser never recomputes or renormalizes weights. Incomplete rows stay
  // null exactly as exported by Analysis Core v4.
  if (toText(row, 'score_status') !== 'complete') return null
  return toNumber(row, 'evacuation_difficulty_score')
}

const walkingMinutes = (row: JsonRecord | undefined | null, speed: '1.0' | '0.62' | '0.5'): number | null => {
  if (!row) return null
  const newKey = speed === '1.0' ? 'walking_time_min_1p0mps' : speed === '0.62' ? 'walking_time_min_0p62mps' : 'walking_time_min_0p5mps'
  const direct = toNumber(row, newKey)
  if (direct !== null) return direct
  const oldKey = speed === '1.0' ? 'walking_time_1_0_s' : speed === '0.62' ? 'walking_time_0_62_s' : 'walking_time_0_5_s'
  const seconds = toNumber(row, oldKey)
  return seconds === null ? null : seconds / 60
}

const capacityPressure = (row: JsonRecord | undefined | null): number | null => {
  const corrected = toNumber(row, 'capacity_pressure_area_weighted')
  if (corrected !== null) return corrected
  return null
}

const routeExposureRatio = (row: JsonRecord | undefined | null): number | null => {
  const classified = toNumber(row, 'route_inundation_ratio_classified')
  if (classified !== null) return classified
  return toNumber(row, 'route_inundation_ratio')
}

const routeCoordinatesForRow = (row: JsonRecord | undefined | null): Coordinate[] => {
  if (!row || toText(row, 'route_status') !== 'complete') return []
  const value = row.route_network_coordinates ?? row.route_coordinates
  if (Array.isArray(value)) return value as Coordinate[]
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value)
      return Array.isArray(parsed) ? parsed as Coordinate[] : []
    } catch {
      return []
    }
  }
  return []
}

const inundatedSegmentsForRow = (row: JsonRecord | undefined | null): InundatedSegment[] => {
  if (!row || toText(row, 'route_status') !== 'complete') return []
  const value = row.route_inundated_segments
  if (Array.isArray(value)) return value as InundatedSegment[]
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value)
      return Array.isArray(parsed) ? parsed as InundatedSegment[] : []
    } catch {
      return []
    }
  }
  return []
}

const inundatedFeatureCollection = (row: JsonRecord | undefined | null): GeoCollection => ({
  type: 'FeatureCollection',
  features: inundatedSegmentsForRow(row)
    .filter((segment) => Array.isArray(segment.coordinates) && (segment.coordinates?.length ?? 0) >= 2)
    .map((segment) => ({
      type: 'Feature',
      properties: { depth_class: segment.depth_class ?? null },
      geometry: { type: 'LineString', coordinates: segment.coordinates as Coordinate[] },
    })),
})

const centerOfFeature = (feature: GeoFeature): Coordinate | null => {
  try {
    return centroid(feature as never).geometry.coordinates as unknown as Coordinate
  } catch {
    return null
  }
}

const reasonRows = (row: RiskRow, mode: ModeId): Reason[] => {
  const walk = walkingMinutes(row, mode === 'elderly' ? '0.62' : '1.0')
  const aging = toNumber(row, 'aging_rate_65plus')
  const route = routeExposureRatio(row)
  const tsunami = toNumber(row, 'tsunami_inundation_ratio')
  const capacity = capacityPressure(row)
  const reasons: Reason[] = []
  if (walk !== null) reasons.push({ key: 'walk', label: mode === 'elderly' ? '高齢者想定の避難時間' : '避難時間', valueLabel: `${formatNumber(walk, 1)}分`, severity: Math.min(1, walk / 30) })
  if (aging !== null) reasons.push({ key: 'aging', label: '65歳以上人口割合', valueLabel: formatPercent(aging), severity: Math.min(1, aging / 0.6) })
  if (route !== null) reasons.push({ key: 'route', label: '経路の津波曝露', valueLabel: formatPercent(route), severity: Math.min(1, route / 0.5) })
  if (capacity !== null) reasons.push({ key: 'capacity', label: '避難場所の収容負荷', valueLabel: formatPct100(capacity * 100), severity: Math.min(1, capacity / 1.5) })
  if (tsunami !== null) reasons.push({ key: 'tsunami', label: 'メッシュ内の津波浸水曝露', valueLabel: formatPercent(tsunami), severity: Math.min(1, tsunami / 0.7) })
  return reasons.sort((a, b) => b.severity - a.severity)
}

const scoreStatusLabel = (row: JsonRecord | undefined | null): string => {
  const status = toText(row, 'score_status')
  return status ? SCORE_STATUS_LABELS[status] ?? status : '—'
}

const routeStatusLabel = (row: JsonRecord | undefined | null): string => {
  const status = toText(row, 'route_status')
  return status ? ROUTE_STATUS_LABELS[status] ?? status : '—'
}

function AppCorrected() {
  const mapElement = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const [mapReady, setMapReady] = useState(false)
  const [mapError, setMapError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [populationFeatures, setPopulationFeatures] = useState<GeoFeature[]>([])
  const [riskRows, setRiskRows] = useState<RiskRow[]>([])
  const [shelters, setShelters] = useState<GeoCollection>({ type: 'FeatureCollection', features: [] })
  const [analysisMetadata, setAnalysisMetadata] = useState<AnalysisMetadata | null>(null)
  const [municipalities, setMunicipalities] = useState<AssetIndexItem[]>([])
  const [municipality, setMunicipality] = useState('all')
  const [mode, setMode] = useState<ModeId>('elderly')
  const [metric, setMetric] = useState<MetricId>('walking')
  const [selectedMeshId, setSelectedMeshId] = useState<string | null>(null)
  const [showTsunami, setShowTsunami] = useState(true)
  const [showShelters, setShowShelters] = useState(true)
  const [showRoute, setShowRoute] = useState(true)
  const [mobilePanelOpen, setMobilePanelOpen] = useState(false)

  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        setLoading(true)
        const [populationIndex, riskIndex, routeIndex, shelterData, metadata] = await Promise.all([
          loadJson<AssetIndexItem[]>(`${BASE}data/population/index.json`),
          loadJson<AssetIndexItem[]>(`${BASE}data/risk/index.json`),
          loadJson<AssetIndexItem[]>(`${BASE}data/routes/index.json`),
          loadJson<GeoCollection>(`${BASE}data/shelters/shelters.geojson`),
          loadJson<AnalysisMetadata>(`${BASE}data/metadata/analysis.json`),
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
        setAnalysisMetadata(metadata)
      } catch (error) {
        if (active) setLoadError(error instanceof Error ? error.message : 'データの読み込みに失敗しました')
      } finally {
        if (active) setLoading(false)
      }
    }
    load()
    return () => { active = false }
  }, [])

  const capacityTrusted = useMemo(() => riskRows.some((row) => row.capacity_pressure_area_weighted !== undefined), [riskRows])
  const riskByMesh = useMemo(() => new Map(riskRows.map((row) => [String(row.mesh_id), row])), [riskRows])
  const shelterById = useMemo(() => {
    const map = new Map<string, JsonRecord>()
    for (const feature of shelters.features) {
      const id = String(feature.properties.common_id ?? feature.properties.gsi_common_id ?? '')
      if (id) map.set(id, feature.properties)
    }
    return map
  }, [shelters])

  const filteredRows = useMemo(() => {
    const rows = municipality === 'all' ? riskRows : riskRows.filter((row) => String(row.municipality_code) === municipality)
    return rows.filter((row) => (toNumber(row, 'tsunami_inundation_ratio') ?? 0) > 0)
  }, [riskRows, municipality])

  const selectedRow = selectedMeshId ? riskByMesh.get(selectedMeshId) ?? null : null
  const selectedFeature = selectedMeshId ? populationFeatures.find((feature) => String(feature.properties.mesh_id) === selectedMeshId) ?? null : null
  const selectedShelterId = selectedRow ? toText(selectedRow, 'selected_shelter_common_id') : null
  const selectedShelter = selectedShelterId ? shelterById.get(selectedShelterId) ?? null : null
  const selectedShelterName = selectedRow ? toText(selectedRow, 'selected_shelter_name') ?? toText(selectedShelter, 'name') ?? selectedShelterId : null
  const activeWalkingSpeed: '1.0' | '0.62' = mode === 'elderly' ? '0.62' : '1.0'

  const mapData = useMemo<GeoCollection>(() => ({
    type: 'FeatureCollection',
    features: populationFeatures.map((feature) => {
      const meshId = String(feature.properties.mesh_id)
      const risk = riskByMesh.get(meshId)
      const properties: JsonRecord = { ...feature.properties, analysis_target: false }
      if (risk) {
        const standard = walkingMinutes(risk, '1.0')
        const elderly = walkingMinutes(risk, '0.62')
        const capacity = capacityPressure(risk)
        const aging = toNumber(risk, 'aging_rate_65plus')
        const route = routeExposureRatio(risk)
        const tsunami = toNumber(risk, 'tsunami_inundation_ratio')
        properties.walking_min_standard = standard
        properties.walking_min_elderly = elderly
        properties.tsunami_pct = tsunami === null ? null : tsunami * 100
        properties.aging65_pct = aging === null ? null : aging * 100
        properties.route_pct = route === null ? null : route * 100
        properties.capacity_pct = capacity === null ? null : capacity * 100
        properties.policy_score = canonicalPolicyScore(risk)
        properties.score_status = toText(risk, 'score_status')
        properties.route_status = toText(risk, 'route_status')
        properties.municipality_code = risk.municipality_code
        properties.analysis_target = (tsunami ?? 0) > 0
      }
      return { ...feature, properties }
    }),
  }), [populationFeatures, riskByMesh])

  const summary = useMemo(() => {
    const demandValues = filteredRows.map((row) => toNumber(row, 'mesh_evacuation_demand_area_weighted'))
    const completeDemandValues = demandValues.filter((value): value is number => value !== null)
    const tsunamiProxyPopulation = filteredRows.length > 0 && completeDemandValues.length === filteredRows.length
      ? completeDemandValues.reduce((sum, value) => sum + value, 0)
      : null
    const standardOver20 = filteredRows.filter((row) => (walkingMinutes(row, '1.0') ?? -1) > 20)
    const elderlyOver20 = filteredRows.filter((row) => (walkingMinutes(row, '0.62') ?? -1) > 20)
    const routeUnavailable = filteredRows.filter((row) => toText(row, 'route_status') !== 'complete')
    const populationFor = (rows: RiskRow[]) => rows.reduce((sum, row) => sum + (toNumber(row, 'total_population') ?? 0), 0)
    const overloaded = new Set<string>()
    if (capacityTrusted) {
      for (const row of filteredRows) {
        const pressure = capacityPressure(row)
        const key = toText(row, 'selected_shelter_key') ?? toText(row, 'selected_shelter_common_id')
        if (pressure !== null && pressure > 1 && key) overloaded.add(key)
      }
    }
    return {
      tsunamiProxyPopulation,
      standardOver20Population: populationFor(standardOver20),
      elderlyOver20Population: populationFor(elderlyOver20),
      routeUnavailablePopulation: populationFor(routeUnavailable),
      routeUnavailableMeshes: routeUnavailable.length,
      overloadedShelters: overloaded.size,
      meshCount: filteredRows.length,
    }
  }, [filteredRows, capacityTrusted])

  const metricProperty = useCallback((metricId: MetricId): string => {
    if (metricId === 'walking') return mode === 'elderly' ? 'walking_min_elderly' : 'walking_min_standard'
    if (metricId === 'tsunami') return 'tsunami_pct'
    if (metricId === 'aging65') return 'aging65_pct'
    if (metricId === 'route') return 'route_pct'
    if (metricId === 'capacity') return 'capacity_pct'
    return 'policy_score'
  }, [mode])

  const fillExpression = useCallback((metricId: MetricId) => {
    const property = metricProperty(metricId)
    if (metricId === 'walking') {
      return ['case', ['==', ['get', property], null], '#cbd5e1', ['interpolate', ['linear'], ['get', property], 0, '#3d9b68', 5, '#75b84a', 10, '#d4c83c', 15, '#f3a62f', 20, '#ef7726', 30, '#dd3d32', 40, '#991f2d']]
    }
    if (metricId === 'capacity') {
      return ['case', ['==', ['get', property], null], '#cbd5e1', ['interpolate', ['linear'], ['get', property], 0, '#3d9b68', 50, '#d4c83c', 100, '#ef7726', 150, '#c92f3c', 250, '#7f1d2d']]
    }
    return ['case', ['==', ['get', property], null], '#cbd5e1', ['interpolate', ['linear'], ['get', property], 0, '#3d9b68', 25, '#9ac64b', 50, '#f1c232', 75, '#ef7726', 100, '#b52e3d']]
  }, [metricProperty])

  const legendStops = useMemo(() => metric === 'walking' ? ['0', '5', '10', '15', '20', '30', '40+'] : metric === 'capacity' ? ['0', '50', '100', '150', '200+'] : ['0', '25', '50', '75', '100'], [metric])

  const rankingRows = useMemo(() => {
    const sorted = [...filteredRows]
    sorted.sort((a, b) => {
      if (mode === 'capacity') return (capacityPressure(b) ?? -1) - (capacityPressure(a) ?? -1)
      if (mode === 'current') return (walkingMinutes(b, '1.0') ?? -1) - (walkingMinutes(a, '1.0') ?? -1)
      return (walkingMinutes(b, '0.62') ?? -1) - (walkingMinutes(a, '0.62') ?? -1)
    })
    return sorted.slice(0, 8)
  }, [filteredRows, mode])

  const selectedReasons = selectedRow ? reasonRows(selectedRow, mode) : []
  const selectedWalkMinutes = selectedRow ? walkingMinutes(selectedRow, activeWalkingSpeed) : null
  const selectedDistance = selectedRow ? toNumber(selectedRow, 'total_walking_distance_m') : null
  const selectedNetworkDistance = selectedRow ? toNumber(selectedRow, 'network_path_distance_m') : null
  const selectedOriginConnector = selectedRow ? toNumber(selectedRow, 'origin_access_distance_m') : null
  const selectedShelterConnector = selectedRow ? toNumber(selectedRow, 'shelter_connector_distance_m') : null
  const selectedCapacityPressure = selectedRow ? capacityPressure(selectedRow) : null
  const selectedRouteExposure = selectedRow ? routeExposureRatio(selectedRow) : null
  const selectedCrossBorder = selectedRow?.cross_border === true || selectedRow?.cross_border === 'True' || selectedRow?.cross_border === 'true'
  const selectedUncertainty = selectedRow?.route_exposure_uncertainty_flag === true || selectedRow?.route_exposure_uncertainty_flag === 'True' || selectedRow?.route_exposure_uncertainty_flag === 'true'

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
      setMapError('このブラウザではWebGLが無効のため、インタラクティブ地図を表示できません。')
      return
    }
    let map: maplibregl.Map | null = null
    try {
      map = new maplibregl.Map({
        container: mapElement.current,
        style: {
          version: 8,
          sources: {
            gsiPale: { type: 'raster', tiles: [GSI_PALE_TILES], tileSize: 256, attribution: '地理院タイル' },
          },
          layers: [
            { id: 'gsi-pale', type: 'raster', source: 'gsiPale', paint: { 'raster-opacity': 0.94 } },
          ],
        },
        center: [132.8, 33.8],
        zoom: 8.25,
        minZoom: 6,
        maxZoom: 18,
        renderWorldCopies: false,
        attributionControl: false,
      })
      mapRef.current = map
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
      map.on('load', () => setMapReady(true))
      map.on('error', (event) => {
        if (event.error?.message?.includes('04_tsunami')) return
        console.warn('MapLibre error', event.error)
      })
    } catch {
      setMapError('地図エンジンを初期化できませんでした。')
    }
    return () => {
      map?.remove()
      mapRef.current = null
      setMapReady(false)
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady) return
    if (!map.getSource('mesh')) {
      map.addSource('mesh', { type: 'geojson', data: mapData as never })
      map.addSource('shelters', { type: 'geojson', data: shelters as never })
      map.addSource('route', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } as never })
      map.addSource('route-inundated', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } as never })
      map.addSource('tsunami', { type: 'raster', tiles: [GSI_TSUNAMI_TILES], tileSize: 256, attribution: '出典：ハザードマップポータルサイト' })
      map.addLayer({ id: 'tsunami-raster', type: 'raster', source: 'tsunami', layout: { visibility: showTsunami ? 'visible' : 'none' }, paint: { 'raster-opacity': 0.42 } })
      map.addLayer({ id: 'mesh-fill', type: 'fill', source: 'mesh', paint: { 'fill-color': fillExpression(metric) as never, 'fill-opacity': 0.67, 'fill-outline-color': 'rgba(255,255,255,.72)' }, filter: ['==', ['get', 'analysis_target'], true] })
      map.addLayer({ id: 'mesh-selected', type: 'line', source: 'mesh', paint: { 'line-color': '#0b2744', 'line-width': 4 }, filter: ['==', ['get', 'mesh_id'], ''] })
      map.addLayer({ id: 'shelters-circle', type: 'circle', source: 'shelters', layout: { visibility: showShelters ? 'visible' : 'none' }, paint: { 'circle-color': '#1265b0', 'circle-radius': ['interpolate', ['linear'], ['zoom'], 7, 3.5, 13, 6.5], 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.6, 'circle-opacity': 0.96 } })
      map.addLayer({ id: 'route-line-casing', type: 'line', source: 'route', layout: { visibility: showRoute ? 'visible' : 'none' }, paint: { 'line-color': '#ffffff', 'line-width': 7, 'line-opacity': 0.95 } })
      map.addLayer({ id: 'route-line', type: 'line', source: 'route', layout: { visibility: showRoute ? 'visible' : 'none' }, paint: { 'line-color': '#092f5c', 'line-width': 4.5, 'line-opacity': 0.98 } })
      map.addLayer({ id: 'route-inundated-line', type: 'line', source: 'route-inundated', layout: { visibility: showRoute ? 'visible' : 'none' }, paint: { 'line-color': '#d92d20', 'line-width': 5.5, 'line-opacity': 0.98 } })
      map.on('click', 'mesh-fill', (event) => {
        const id = event.features?.[0]?.properties?.mesh_id
        if (id !== undefined && id !== null) {
          setSelectedMeshId(String(id))
          setMobilePanelOpen(true)
        }
      })
      map.on('mouseenter', 'mesh-fill', () => { map.getCanvas().style.cursor = 'pointer' })
      map.on('mouseleave', 'mesh-fill', () => { map.getCanvas().style.cursor = '' })
    }
    const meshSource = map.getSource('mesh') as maplibregl.GeoJSONSource | undefined
    if (meshSource) meshSource.setData(mapData as never)
    map.setPaintProperty('mesh-fill', 'fill-color', fillExpression(metric) as never)
    const filters: unknown[] = [['==', ['get', 'analysis_target'], true]]
    if (municipality !== 'all') filters.push(['==', ['get', 'municipality_code'], municipality])
    map.setFilter('mesh-fill', filters.length === 1 ? filters[0] as never : ['all', ...filters] as never)
    map.setFilter('mesh-selected', selectedMeshId ? ['==', ['get', 'mesh_id'], selectedMeshId] : ['==', ['get', 'mesh_id'], ''])
    map.setLayoutProperty('tsunami-raster', 'visibility', showTsunami ? 'visible' : 'none')
    map.setLayoutProperty('shelters-circle', 'visibility', showShelters ? 'visible' : 'none')
    const routeVisible = showRoute && selectedRow !== null && toText(selectedRow, 'route_status') === 'complete'
    map.setLayoutProperty('route-line-casing', 'visibility', routeVisible ? 'visible' : 'none')
    map.setLayoutProperty('route-line', 'visibility', routeVisible ? 'visible' : 'none')
    map.setLayoutProperty('route-inundated-line', 'visibility', routeVisible ? 'visible' : 'none')
    const routeCoordinates = routeCoordinatesForRow(selectedRow)
    const routeSource = map.getSource('route') as maplibregl.GeoJSONSource | undefined
    if (routeSource) {
      routeSource.setData({ type: 'FeatureCollection', features: routeCoordinates.length >= 2 ? [{ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: routeCoordinates } }] : [] } as never)
    }
    const inundatedSource = map.getSource('route-inundated') as maplibregl.GeoJSONSource | undefined
    if (inundatedSource) inundatedSource.setData(inundatedFeatureCollection(selectedRow) as never)
  }, [fillExpression, mapData, mapReady, metric, municipality, selectedMeshId, selectedRow, showRoute, showShelters, showTsunami, shelters])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady || !selectedFeature) return
    const center = centerOfFeature(selectedFeature)
    if (center) map.flyTo({ center, zoom: Math.max(map.getZoom(), 11), duration: 500 })
  }, [mapReady, selectedFeature])

  useEffect(() => {
    if (municipality !== 'all' && selectedRow && String(selectedRow.municipality_code) !== municipality) setSelectedMeshId(null)
  }, [municipality, selectedRow])

  const setModeAndMetric = (next: ModeId) => {
    setMode(next)
    if (next === 'capacity') setMetric('capacity')
    else setMetric('walking')
  }

  if (loadError) {
    return <div className="fatal-state"><strong>データを読み込めませんでした</strong><span>{loadError}</span><button onClick={() => window.location.reload()}>再読み込み</button></div>
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand-block">
          <div className="eyebrow">EHIME / NANKAI TROUGH · POLICY EXPLORER</div>
          <h1>南海トラフ・本当に逃げられるかマップ</h1>
          <p>人口・避難距離・経路曝露・避難場所容量から見る愛媛の避難困難度</p>
        </div>
        <div className="header-actions">
          <div className="header-status"><span className="status-dot" /><b>{loading ? 'データ読込中' : '解析是正版 v4'}</b><span>/ {formatNumber(municipality === 'all' ? (analysisMetadata?.target_meshes ?? filteredRows.length) : filteredRows.length)}津波対象メッシュ</span></div>
          <button className="header-icon-button" title="使い方"><Icon name="info" />使い方</button>
          <button className="header-icon-button" onClick={() => window.print()} title="印刷">▣ 印刷</button>
        </div>
      </header>

      <nav className="mode-strip" aria-label="分析モード">
        {(Object.keys(MODE_LABELS) as ModeId[]).map((id) => { const disabled = id === 'capacity' && !capacityTrusted; return <button key={id} className={`${mode === id ? 'active' : ''} ${disabled ? 'coming-soon' : ''}`} onClick={() => setModeAndMetric(id)} disabled={disabled}>{MODE_LABELS[id]}{id === 'capacity' && !capacityTrusted && <small>容量データなし</small>}</button> })}
      </nav>

      <section className="kpi-strip" aria-label="主要指標">
        <KpiCard icon="people" tone="blue" label="津波曝露人口（代理）" value={summary.tsunamiProxyPopulation === null ? '—' : `${formatNumber(summary.tsunamiProxyPopulation)}人`} sub="人口×津波浸水面積割合（STEP 4面積按分需要）" />
        <KpiCard icon="clock" tone="orange" label="20分超避難人口" value={`${formatNumber(summary.standardOver20Population)}人`} sub="標準歩行 1.0m/s" />
        <KpiCard icon="elderly" tone="red" label="高齢者想定20分超人口" value={`${formatNumber(summary.elderlyOver20Population)}人`} sub="観測参考 0.62m/s" />
        <KpiCard icon="warning" tone="red" label="経路未成立人口" value={`${formatNumber(summary.routeUnavailablePopulation)}人`} sub={`${formatNumber(summary.routeUnavailableMeshes)}メッシュ / no path・coverage gap`} />
        <KpiCard icon="shelter" tone="purple" label="容量超過避難場所" value={capacityTrusted ? `${formatNumber(summary.overloadedShelters)}施設` : '—'} sub="面積按分需要の避難場所別集約 / 公表収容人数 > 100%" muted={!capacityTrusted} />
      </section>

      <main className="dashboard-grid">
        <section className="map-panel" aria-label="避難時間マップ">
          {mapError ? <div className="map-fallback"><div className="map-fallback-card"><Icon name="warning" /><h2>地図表示にはWebGLが必要です</h2><p>{mapError}</p></div></div> : <div ref={mapElement} className="map-canvas" />}

          <div className="map-control map-municipality">
            <select aria-label="対象市町" value={municipality} onChange={(event) => setMunicipality(event.target.value)}>
              <option value="all">沿岸14市町（全体）</option>
              {municipalities.map((item) => <option key={item.municipality_code} value={item.municipality_code}>{item.municipality}</option>)}
            </select>
          </div>

          <div className="map-control layer-card">
            <div className="control-title">レイヤー</div>
            <label><input type="checkbox" checked={showTsunami} onChange={(event) => setShowTsunami(event.target.checked)} /><span className="layer-symbol tsunami" />津波浸水想定</label>
            <label><input type="checkbox" checked={showShelters} onChange={(event) => setShowShelters(event.target.checked)} /><span className="layer-symbol shelter" />避難場所</label>
            <label style={{ opacity: selectedRow ? 1 : 0.56 }}><input type="checkbox" checked={showRoute} disabled={!selectedRow} onChange={(event) => setShowRoute(event.target.checked)} /><span className="layer-symbol route" />選択メッシュの避難経路</label>
            {!selectedRow && <div style={{ margin: '-2px 0 8px 24px', maxWidth: 160, color: '#64748b', fontSize: 10, lineHeight: 1.35 }}>色付きメッシュをタップするとSTEP 2/3経路を表示</div>}
            <div className="control-divider" />
            <label className="metric-select-label">地図の色</label>
            <select value={metric} onChange={(event) => setMetric(event.target.value as MetricId)}>
              <option value="walking">避難時間</option>
              <option value="tsunami">津波浸水曝露</option>
              <option value="aging65">65歳以上人口割合</option>
              <option value="route">経路津波曝露</option>
              <option value="score">政策優先度</option>
              <option value="capacity" disabled={!capacityTrusted}>収容負荷</option>
            </select>
          </div>

          <div className="map-legend">
            <div className="legend-heading"><b>{METRICS[metric].label}</b><span>{METRICS[metric].unit}</span></div>
            <div className={`legend-gradient legend-${metric}`} />
            <div className="legend-labels">{legendStops.map((stop) => <span key={stop}>{stop}</span>)}</div>
            <p>{METRICS[metric].note}</p>
          </div>

          <div className="route-key"><span><i className="selected-mesh-symbol" />選択メッシュ</span><span><i className="route-safe-symbol" />道路ネットワーク経路</span><span className="future-key" style={{ color: '#b42318' }}>赤線＝STEP 3で判定した津波浸水区間</span></div>
          <div className="map-attribution">地理院タイル / 出典：ハザードマップポータルサイト / © OpenStreetMap contributors</div>
        </section>

        <aside className={`diagnostic-panel ${mobilePanelOpen ? 'mobile-open' : ''}`}>
          <button className="mobile-panel-toggle" onClick={() => setMobilePanelOpen((open) => !open)}>{mobilePanelOpen ? '診断を閉じる' : '選択地点の診断を見る'}</button>
          <div className="diagnostic-scroll">
            <section className="diagnostic-head">
              <div>
                <span className="section-kicker">SELECTED MESH</span>
                <h2>選択メッシュの診断</h2>
                <p>{selectedRow ? `${selectedRow.municipality} / ${selectedRow.mesh_id}` : '地図またはランキングからメッシュを選択'}</p>
              </div>
            </section>

            {selectedRow ? <>
              <section className="hero-diagnosis">
                <div className="hero-time">
                  <span>{mode === 'elderly' ? '高齢者想定' : '標準歩行'}</span>
                  <strong>{formatNumber(selectedWalkMinutes, 1)}<small>分</small></strong>
                  {selectedCrossBorder && <em>市町境を越える避難先</em>}
                  {toText(selectedRow, 'route_status') !== 'complete' && <em>{routeStatusLabel(selectedRow)}</em>}
                </div>
                <div className="fact-grid">
                  <Fact icon="school" label="選択避難場所" value={selectedShelterName ?? '経路未成立のため未選択'} />
                  <Fact icon="distance" label="総距離" value={formatMeters(selectedDistance)} />
                  <Fact icon="walk" label="道路まで" value={formatMeters(selectedOriginConnector)} />
                  <Fact icon="route" label="道路上" value={formatMeters(selectedNetworkDistance)} />
                  <Fact icon="distance" label="避難場所入口まで" value={formatMeters(selectedShelterConnector)} />
                  <Fact icon="wave" label="経路の津波曝露" value={`${formatPercent(selectedRouteExposure)}${selectedUncertainty ? '（一部不明）' : ''}`} />
                  <Fact icon="people" label="避難場所収容負荷" value={!capacityTrusted ? '—' : selectedCapacityPressure === null ? '不明（収容人数未公表）' : formatPct100(selectedCapacityPressure * 100)} alert={selectedCapacityPressure !== null && selectedCapacityPressure > 1} />
                  <Fact icon="database" label="分析ステータス" value={scoreStatusLabel(selectedRow)} />
                  <Fact icon="database" label="データ完全度" value={formatPct100(toNumber(selectedRow, 'data_completeness_pct'), 0)} />
                </div>
              </section>

              <section className="diagnosis-split">
                <div className="why-card">
                  <div className="section-title"><h3>なぜ厳しい？</h3><Icon name="info" /></div>
                  {toText(selectedRow, 'route_status') !== 'complete'
                    ? <p style={{ margin: 0, color: '#64748b', fontSize: 12, lineHeight: 1.6 }}>経路が成立していないため、徒歩・経路曝露・容量を含む数値リスクは算出していません。失敗状態を母数に残して表示しています。</p>
                    : selectedReasons.slice(0, 4).map((reason, index) => <div className="reason-item" key={reason.key}><b>{index + 1}</b><div><span>{reason.label}</span><i><em style={{ width: `${Math.max(4, reason.severity * 100)}%` }} /></i></div><strong>{reason.valueLabel}</strong></div>)}
                </div>
                <div className="action-card">
                  <div className="section-title"><h3>対策の方向</h3><Icon name="info" /></div>
                  <button><span>＋</span>避難場所増強</button>
                  <button><span>⇄</span>広域避難計画</button>
                  <button><span>△</span>経路改善</button>
                  <p>収容負荷はSTEP 4の面積按分需要を避難場所単位に集約し、公表収容人数で除した診断値です。収容人数未公表は不明のままとし、超過分の自動再配分は行いません。</p>
                </div>
              </section>
            </> : <div className="empty-diagnosis"><div className="empty-icon">＋</div><h3>メッシュを選択してください</h3><p>地図上の色付きメッシュ、または下の要対策ランキングをクリックすると、避難時間・経路・津波曝露を診断します。</p></div>}

            <details className="advanced-card">
              <summary><span><span className="section-kicker">ADVANCED</span><b>高度な分析</b></span><span>⌄</span></summary>
              <div className="advanced-body">
                <div className="advanced-note">政策優先度はAnalysis Core v4で確定した公開済み <code>evacuation_difficulty_score</code> をそのまま表示します。探索的固定重みは「津波25 / 要配慮人口20 / 徒歩25 / 経路曝露15 / 収容負荷15」で、公式基準ではありません。ブラウザ側で重み変更・再正規化は行いません。容量・年齢等が欠損するメッシュは <code>score_status</code> に従い数値スコアを表示しません。</div>
              </div>
            </details>
          </div>
        </aside>

        <section className="ranking-panel">
          <div className="ranking-header">
            <div><span className="section-kicker">PRIORITY MESHES</span><h2>要対策メッシュランキング</h2></div>
            <div className="ranking-meta">{mode === 'elderly' ? '高齢者想定時間' : mode === 'current' ? '標準避難時間' : '収容負荷'}（降順）</div>
          </div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>順位</th><th>市町</th><th>メッシュ</th><th>対象人口</th><th>標準時間</th><th>高齢者想定</th><th>経路曝露</th><th>状態 / 主因</th></tr></thead>
              <tbody>{rankingRows.map((row, index) => {
                const reasons = reasonRows(row, mode)
                const routeStatus = toText(row, 'route_status')
                return <tr key={row.mesh_id} onClick={() => { setSelectedMeshId(String(row.mesh_id)); setMobilePanelOpen(true) }} className={selectedMeshId === String(row.mesh_id) ? 'selected' : ''}><td><span className={`rank-badge rank-${index + 1}`}>{index + 1}</span></td><td>{row.municipality}</td><td className="mesh-id">{row.mesh_id}</td><td>{formatNumber(toNumber(row, 'total_population'))}人</td><td>{formatNumber(walkingMinutes(row, '1.0'), 1)}分</td><td><b>{formatNumber(walkingMinutes(row, '0.62'), 1)}分</b></td><td>{formatPercent(routeExposureRatio(row))}</td><td>{routeStatus !== 'complete' ? routeStatusLabel(row) : reasons.slice(0, 2).map((reason) => reason.label).join('・')}</td></tr>
              })}</tbody>
            </table>
          </div>
        </section>
      </main>

      <footer className="disclaimer"><span>本サイトは公開データを用いた政策分析・可視化PoCです。実際の避難行動や避難経路の安全を保証するものではありません。メッシュ避難需要は人口×津波浸水面積割合の代理値です。収容負荷はその面積按分需要を避難場所単位に集約して公表収容人数で除した値で、収容人数未公表は不明のまま扱います。実避難者数の予測ではありません。</span><span>人口：2020年国勢調査 / 津波：ハザードマップポータル / 避難場所：愛媛県・国土地理院 / 道路：OpenStreetMap / 解析：Analysis Core v4 corrected</span></footer>
    </div>
  )
}

function Icon({ name }: { name: IconName }) {
  const common = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const }
  return <svg viewBox="0 0 24 24" aria-hidden="true" className="ui-icon">
    {name === 'people' && <><circle cx="8" cy="8" r="3" {...common}/><circle cx="16" cy="9" r="2.5" {...common}/><path d="M2.5 19c.5-4 2.6-6 5.5-6s5 2 5.5 6M13 14c3.6-.2 6 1.6 6.5 5" {...common}/></>}
    {name === 'clock' && <><circle cx="12" cy="12" r="8.5" {...common}/><path d="M12 7v5l3 2" {...common}/></>}
    {name === 'elderly' && <><circle cx="12" cy="5" r="2" {...common}/><path d="M11 8.5l-1.5 5L7 18m4-5 3 2.5 1.5 4M9.5 10l4 .5M16.5 8v12" {...common}/></>}
    {name === 'warning' && <><path d="M12 3 2.5 20h19L12 3Z" {...common}/><path d="M12 9v5m0 3h.01" {...common}/></>}
    {name === 'shelter' && <><path d="M4 20V9l8-6 8 6v11H4Z" {...common}/><path d="M9 20v-6h6v6M8 10h8" {...common}/></>}
    {name === 'route' && <><path d="M5 19c0-4 5-3 5-7s-5-3-5-7m14 14c0-4-5-3-5-7s5-3 5-7" {...common}/><circle cx="5" cy="19" r="1.5" {...common}/><circle cx="19" cy="5" r="1.5" {...common}/></>}
    {name === 'wave' && <><path d="M3 10c2.4 0 2.4-2 4.8-2s2.4 2 4.8 2 2.4-2 4.8-2 2.4 2 3.6 2M3 15c2.4 0 2.4-2 4.8-2s2.4 2 4.8 2 2.4-2 4.8-2 2.4 2 3.6 2" {...common}/></>}
    {name === 'database' && <><ellipse cx="12" cy="5" rx="7" ry="3" {...common}/><path d="M5 5v6c0 1.7 3.1 3 7 3s7-1.3 7-3V5M5 11v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" {...common}/></>}
    {name === 'distance' && <><path d="M4 12h16M4 12l3-3m-3 3 3 3m13-3-3-3m3 3-3 3" {...common}/></>}
    {name === 'walk' && <><circle cx="13" cy="5" r="2" {...common}/><path d="m11 8-2 5 3 2 1 5m-4-7-3 2m7-5 3 3 3-1" {...common}/></>}
    {name === 'school' && <><path d="M4 20V9h16v11M7 9V5h10v4M10 20v-5h4v5M8 12h2m4 0h2" {...common}/></>}
    {name === 'info' && <><circle cx="12" cy="12" r="9" {...common}/><path d="M12 11v6m0-9h.01" {...common}/></>}
  </svg>
}

function KpiCard({ icon, tone, label, value, sub, muted = false }: { icon: IconName; tone: string; label: string; value: string; sub: string; muted?: boolean }) {
  return <article className={`kpi-card tone-${tone} ${muted ? 'muted' : ''}`}><div className="kpi-icon"><Icon name={icon}/></div><div><span>{label}</span><strong>{value}</strong><small>{sub}</small></div></article>
}

function Fact({ icon, label, value, alert = false }: { icon: IconName; label: string; value: string; alert?: boolean }) {
  return <div className={`fact ${alert ? 'alert' : ''}`}><Icon name={icon}/><div><span>{label}</span><strong>{value}</strong></div></div>
}

export default AppCorrected
