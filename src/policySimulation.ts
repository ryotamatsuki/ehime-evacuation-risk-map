export type SimulationRiskRow = Record<string, unknown> & {
  mesh_id?: string
  score_status?: string
  selected_shelter_key?: string
}

export type SimulationShelterRow = Record<string, unknown> & {
  selected_shelter_key?: string
  selected_shelter_common_id?: string
  selected_shelter_name?: string
  shelter_municipality_code?: string
}

export interface ShelterSimulationResult {
  shelterKey: string
  commonId: string | null
  name: string
  municipalityCode: string | null
  baselineCapacity: number
  simulatedCapacity: number
  assignedDemand: number
  baselinePressure: number
  simulatedPressure: number
  pressureReduction: number
  baselineOverCapacity: boolean
  simulatedOverCapacity: boolean
  resolvedOverCapacity: boolean
  assignedMeshCount: number
  affectedCompleteMeshes: number
  totalScoreReduction: number
  meanScoreReduction: number
  maxScoreReduction: number
}

export interface CapacitySimulationSummary {
  capacityDelta: number
  knownCapacityShelters: number
  baselineOverCapacityShelters: number
  simulatedOverCapacityShelters: number
  resolvedOverCapacityShelters: number
  affectedCompleteMeshes: number
  totalScoreReduction: number
  results: ShelterSimulationResult[]
}

const WEIGHTS = {
  tsunami: 25,
  vulnerable: 20,
  walking: 25,
  route: 15,
  capacity: 15,
} as const

export const finiteNumber = (value: unknown): number | null => {
  if (value === null || value === undefined || value === '') return null
  const number = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(number) ? number : null
}

const textValue = (value: unknown): string | null => {
  if (value === null || value === undefined || value === '') return null
  return String(value)
}

export const capacityComponentFromPressure = (pressure: number): number => Math.min(Math.max(pressure * 100, 0), 100)

export const simulatedFiveComponentScore = (row: SimulationRiskRow, simulatedCapacityComponent: number): number | null => {
  if (textValue(row.score_status) !== 'complete') return null
  const tsunami = finiteNumber(row.tsunami_exposure_component)
  const vulnerable = finiteNumber(row.vulnerable_population_component)
  const walking = finiteNumber(row.walking_accessibility_component)
  const route = finiteNumber(row.route_inundation_exposure_component)
  if (tsunami === null || vulnerable === null || walking === null || route === null) return null
  return (
    tsunami * WEIGHTS.tsunami
    + vulnerable * WEIGHTS.vulnerable
    + walking * WEIGHTS.walking
    + route * WEIGHTS.route
    + simulatedCapacityComponent * WEIGHTS.capacity
  ) / 100
}

export const simulateCapacityAugmentation = (
  riskRows: SimulationRiskRow[],
  shelters: SimulationShelterRow[],
  capacityDelta: number,
): CapacitySimulationSummary => {
  const delta = Number.isFinite(capacityDelta) ? Math.max(0, capacityDelta) : 0
  const completeByShelter = new Map<string, SimulationRiskRow[]>()
  for (const row of riskRows) {
    if (textValue(row.score_status) !== 'complete') continue
    const key = textValue(row.selected_shelter_key)
    if (!key) continue
    const rows = completeByShelter.get(key) ?? []
    rows.push(row)
    completeByShelter.set(key, rows)
  }

  const results: ShelterSimulationResult[] = []
  for (const shelter of shelters) {
    const shelterKey = textValue(shelter.selected_shelter_key)
    const baselineCapacity = finiteNumber(shelter.shelter_capacity)
    const assignedDemand = finiteNumber(shelter.assigned_demand_area_weighted)
    if (!shelterKey || baselineCapacity === null || baselineCapacity <= 0 || assignedDemand === null || assignedDemand < 0) continue

    const simulatedCapacity = baselineCapacity + delta
    const fallbackPressure = assignedDemand / baselineCapacity
    const baselinePressure = finiteNumber(shelter.capacity_pressure_area_weighted) ?? fallbackPressure
    const simulatedPressure = assignedDemand / simulatedCapacity
    const simulatedComponent = capacityComponentFromPressure(simulatedPressure)
    const affectedRows = completeByShelter.get(shelterKey) ?? []
    const reductions: number[] = []
    for (const row of affectedRows) {
      const canonical = finiteNumber(row.evacuation_difficulty_score)
      const simulated = simulatedFiveComponentScore(row, simulatedComponent)
      if (canonical === null || simulated === null) continue
      reductions.push(Math.max(0, canonical - simulated))
    }

    const totalScoreReduction = reductions.reduce((sum, value) => sum + value, 0)
    const baselineOverCapacity = baselinePressure > 1
    const simulatedOverCapacity = simulatedPressure > 1
    results.push({
      shelterKey,
      commonId: textValue(shelter.selected_shelter_common_id),
      name: textValue(shelter.selected_shelter_name) ?? shelterKey,
      municipalityCode: textValue(shelter.shelter_municipality_code),
      baselineCapacity,
      simulatedCapacity,
      assignedDemand,
      baselinePressure,
      simulatedPressure,
      pressureReduction: Math.max(0, baselinePressure - simulatedPressure),
      baselineOverCapacity,
      simulatedOverCapacity,
      resolvedOverCapacity: baselineOverCapacity && !simulatedOverCapacity,
      assignedMeshCount: finiteNumber(shelter.assigned_mesh_count) ?? 0,
      affectedCompleteMeshes: reductions.length,
      totalScoreReduction,
      meanScoreReduction: reductions.length ? totalScoreReduction / reductions.length : 0,
      maxScoreReduction: reductions.length ? Math.max(...reductions) : 0,
    })
  }

  results.sort((a, b) =>
    b.totalScoreReduction - a.totalScoreReduction
    || Number(b.resolvedOverCapacity) - Number(a.resolvedOverCapacity)
    || b.pressureReduction - a.pressureReduction
    || b.assignedDemand - a.assignedDemand
    || a.name.localeCompare(b.name, 'ja'),
  )

  return {
    capacityDelta: delta,
    knownCapacityShelters: results.length,
    baselineOverCapacityShelters: results.filter((row) => row.baselineOverCapacity).length,
    simulatedOverCapacityShelters: results.filter((row) => row.simulatedOverCapacity).length,
    resolvedOverCapacityShelters: results.filter((row) => row.resolvedOverCapacity).length,
    affectedCompleteMeshes: results.reduce((sum, row) => sum + row.affectedCompleteMeshes, 0),
    totalScoreReduction: results.reduce((sum, row) => sum + row.totalScoreReduction, 0),
    results,
  }
}
