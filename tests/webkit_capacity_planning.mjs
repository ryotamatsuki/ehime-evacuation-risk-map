import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { devices, webkit } from 'playwright'

const baseUrl = process.env.BASE_URL ?? 'http://127.0.0.1:4173/ehime-evacuation-risk-map/'
const reportPath = path.resolve('data/qa/step8-10/webkit_capacity_planning.json')
const cases = [
  { name: 'desktop-webkit', context: { viewport: { width: 1440, height: 1000 } } },
  { name: 'iphone-webkit', context: { ...devices['iPhone 13'] } },
]

const report = {
  test: 'STEP 8-10 capacity planning and root-cause Playwright WebKit smoke',
  native_safari_verified: false,
  note: 'Playwright WebKit is a Safari-compatibility automation proxy, not native Safari or physical iPhone verification.',
  cases: [],
  pass: false,
}

function ensure(condition, message) {
  if (!condition) throw new Error(message)
}

function number(text) {
  const match = String(text).replaceAll(',', '').match(/-?[\d.]+/)
  return match ? Number(match[0]) : Number.NaN
}

function closeTo(actual, expected, tolerance = 0.0001) {
  return Number.isFinite(actual) && Math.abs(actual - expected) <= tolerance
}

async function assertCanonicalReady(page, result) {
  await page.locator('[aria-label="主要指標"]').waitFor({ state: 'visible', timeout: 30000 })
  await page.waitForFunction(() => document.body.innerText.includes('35施設'), null, { timeout: 30000 })
  ensure(await page.locator('.fatal-state').count() === 0, 'canonical fatal-state rendered before STEP 8-10 interaction')
  result.assertions.push('canonical v4 data fully loaded before STEP 8-10 interaction')
}

async function runCase(browser, spec) {
  const context = await browser.newContext(spec.context)
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', (error) => errors.push(String(error)))
  const result = { name: spec.name, assertions: [], pass: false, errors: [] }

  try {
    await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 })
    await page.locator('h1').filter({ hasText: '南海トラフ・本当に逃げられるかマップ' }).waitFor({ timeout: 30000 })
    result.assertions.push('canonical application shell rendered')
    await assertCanonicalReady(page, result)

    const launcher = page.getByRole('button', { name: /容量配分・未収容原因/ })
    await launcher.waitFor({ state: 'visible', timeout: 30000 })
    result.assertions.push('STEP 8-10 launcher rendered')
    await launcher.click()

    const dialog = page.locator('[aria-label="容量制約付き避難配分・投資最適化・未収容原因分析"]')
    await dialog.waitFor({ state: 'visible', timeout: 30000 })

    const baseline = await page.getByTestId('step8-baseline-overload').innerText({ timeout: 30000 })
    ensure(number(baseline) === 35, `STEP 8 baseline not 35: ${baseline}`)
    result.assertions.push('canonical v4 baseline fixed: 35 over-capacity shelters')

    const unserved = number(await page.getByTestId('step8-unserved').innerText())
    ensure(closeTo(unserved, 4923.9), `STEP 8 area-weighted unserved regression: ${unserved}`)
    result.assertions.push('STEP 8 production KPI fixed: area-weighted unserved=4923.9')

    const causes = ['route_unavailable', 'unknown_capacity_only', 'candidate_limit_recoverable', 'known_capacity_saturation']
    const causeValues = []
    for (const cause of causes) {
      const text = await page.locator(`[data-cause="${cause}"] strong`).innerText({ timeout: 30000 })
      causeValues.push(number(text))
    }
    const causeSum = causeValues.reduce((sum, value) => sum + value, 0)
    ensure(closeTo(causeSum, 4923.9, 0.2), `STEP 10 decomposition sum regression: ${causeSum}`)
    ensure(closeTo(causeValues[0], 295.7, 0.2), `STEP 10 route-unavailable demand regression: ${causeValues[0]}`)
    const k30Unserved = number(await page.getByTestId('step10-k30-unserved').innerText())
    ensure(Number.isFinite(k30Unserved) && k30Unserved <= 4923.9 + 0.0001, `STEP 10 K30 unserved must not exceed K10: ${k30Unserved}`)
    result.assertions.push('STEP 10 root causes close exactly to K10 shortage and K30 does not worsen shortage')

    const plus1000 = page.getByRole('button', { name: '+1,000人', exact: true })
    await plus1000.click()
    await page.waitForTimeout(100)
    const used = number(await page.getByTestId('step9-capacity-used').innerText())
    const reduction = number(await page.getByTestId('step9-unserved-reduction').innerText())
    ensure(closeTo(used, 1000), `STEP 9 +1000 capacity-used regression: ${used}`)
    ensure(closeTo(reduction, 0), `STEP 9 +1000 area-weighted shortage-reduction regression: ${reduction}`)
    result.assertions.push('STEP 9 +1000 production KPI fixed: used=1000 and area-weighted shortage reduction=0')

    await page.getByRole('button', { name: '容量配分を閉じる' }).click()
    await dialog.waitFor({ state: 'hidden' })
    ensure(await page.locator('.fatal-state').count() === 0, 'canonical fatal-state rendered after STEP 8-10 interaction')
    result.assertions.push('canonical v4 remains healthy after STEP 8-10 interaction')

    if (spec.name === 'iphone-webkit') {
      const toggle = page.locator('.mobile-panel-toggle')
      await toggle.waitFor({ state: 'visible', timeout: 30000 })
      await toggle.click()
      ensure(await page.locator('.diagnostic-panel.mobile-open').count() === 1, 'capacity launcher intercepted mobile diagnostic toggle')
      result.assertions.push('mobile diagnostic panel remains reachable')
    }

    ensure(errors.length === 0, `uncaught page errors: ${errors.join(' | ')}`)
    result.assertions.push('no uncaught page errors')
    result.pass = true
  } catch (error) {
    result.errors.push(String(error))
    fs.mkdirSync(path.dirname(reportPath), { recursive: true })
    await page.screenshot({ path: path.join(path.dirname(reportPath), `${spec.name}-failure.png`), fullPage: true }).catch(() => {})
  } finally {
    await context.close()
  }
  return result
}

fs.mkdirSync(path.dirname(reportPath), { recursive: true })
const browser = await webkit.launch()
try {
  for (const spec of cases) report.cases.push(await runCase(browser, spec))
} finally {
  await browser.close()
}

report.pass = report.cases.every((item) => item.pass)
fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8')
console.log(JSON.stringify(report, null, 2))
if (!report.pass) process.exit(1)
