export type JsonRecord = Record<string, unknown>

export const DATA_BASE = import.meta.env.BASE_URL

export const POLICY_WEIGHTS = {
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

export const textValue = (value: unknown): string | null => {
  if (value === null || value === undefined || value === '') return null
  return String(value)
}

export const recordNumber = (row: JsonRecord | undefined | null, key: string): number | null => {
  if (!row) return null
  return finiteNumber(row[key])
}

export const recordText = (row: JsonRecord | undefined | null, key: string): string | null => {
  if (!row) return null
  return textValue(row[key])
}

export const formatNumber = (value: number | null, digits = 0): string => {
  if (value === null || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('ja-JP', {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(value)
}

export async function loadJson<T>(path: string): Promise<T> {
  const response = await fetch(path)
  if (!response.ok) throw new Error(`${response.status} ${path}`)
  return response.json() as Promise<T>
}
