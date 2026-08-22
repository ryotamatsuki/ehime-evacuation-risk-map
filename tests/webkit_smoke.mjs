import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { devices, webkit } from 'playwright'

const baseUrl = process.env.BASE_URL ?? 'http://127.0.0.1:4173/ehime-evacuation-risk-map/'
const reportPath = path.resolve('data/qa/step6/webkit_smoke.json')
const cases = [
  { name: 'desktop-webkit', context: { viewport: { width: 1440, height: 1000 } } },
  { name: 'iphone-webkit', context: { ...devices['iPhone 13'] } },
]

const report = {
  test: 'Production Playwright WebKit smoke including STEP 7 policy simulation',
  base_url: baseUrl,
  native_safari_verified: false,
  note: 'Playwright WebKit is a Safari-compatibility automation proxy, not native Safari or physical iPhone verification.',
  cases: [],
  pass: false,
}

function ensure(condition, message) {
  if (!condition) throw new Error(message)
}

function facilityCount(text) {
  const match = String(text).match(/([\d,]+)施設/)
  return match ? Number(match[1].replaceAll(',', '')) : Number.NaN
}

async function runCase(browser, spec) {
  const context = await browser.newContext(spec.context)
  const page = await context.newPage()
  const pageErrors = []
  page.on('pageerror', (error) => pageErrors.push(String(error)))
  const result = { name: spec.name, assertions: [], pass: false, errors: [] }

  try {
    await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 })
    await page.locator('h1').filter({ hasText: '南海トラフ・本当に逃げられるかマップ' }).waitFor({ timeout: 30000 })
    result.assertions.push('title rendered')

    await page.locator('[aria-label="分析モード"]').waitFor()
    await page.locator('[aria-label="主要指標"]').waitFor()
    await page.locator('[aria-label="避難時間マップ"]').waitFor()
    await page.locator('[aria-label="対象市町"]').waitFor()
    result.assertions.push('core policy explorer regions rendered')

    await page.waitForFunction(() => document.body.innerText.includes('35施設'), null, { timeout: 30000 })
    result.assertions.push('production capacity KPI=35 facilities')

    ensure(await page.locator('.fatal-state').count() === 0, 'fatal-state rendered')
    result.assertions.push('no fatal data state')

    const mapCanvas = await page.locator('.map-canvas').count()
    const mapFallback = await page.locator('.map-fallback').count()
    ensure(mapCanvas + mapFallback >= 1, 'neither map canvas nor WebGL fallback rendered')
    result.assertions.push(mapFallback ? 'WebGL fallback rendered safely' : 'map canvas rendered')

    const municipality = page.locator('[aria-label="対象市町"]')
    await municipality.selectOption('38201')
    await page.waitForTimeout(250)
    ensure(await municipality.inputValue() === '38201', 'municipality selector failed')
    ensure(await page.locator('.fatal-state').count() === 0, 'fatal-state after municipality change')
    result.assertions.push('municipality interaction works')

    const simulationButton = page.getByRole('button', { name: /対策シミュレーション/ })
    await simulationButton.waitFor({ state: 'visible' })
    ensure(!(await simulationButton.isDisabled()), 'STEP 7 simulation launcher is disabled')
    await simulationButton.click()
    const dialog = page.locator('[aria-label="対策シミュレーション"]')
    await dialog.waitFor({ state: 'visible' })
    const baselineText = await page.getByTestId('simulation-baseline-overload').innerText({ timeout: 30000 })
    const afterText = await page.getByTestId('simulation-after-overload').innerText({ timeout: 30000 })
    ensure(facilityCount(baselineText) === 35, `simulation baseline is not 35: ${baselineText}`)
    ensure(facilityCount(afterText) <= 35, `simulation increased overload count: ${afterText}`)
    result.assertions.push('STEP 7 simulation opens with production baseline=35 and non-increasing overload')

    const plus1000 = page.getByRole('button', { name: '+1,000人', exact: true })
    await plus1000.click()
    await page.waitForTimeout(100)
    const after1000Text = await page.getByTestId('simulation-after-overload').innerText()
    ensure(facilityCount(after1000Text) <= facilityCount(afterText), 'larger capacity augmentation increased overload count')
    result.assertions.push('larger capacity augmentation is monotonic')

    await page.getByRole('button', { name: 'シミュレーションを閉じる' }).click()
    await dialog.waitFor({ state: 'hidden' })

    if (spec.name === 'iphone-webkit') {
      const toggle = page.locator('.mobile-panel-toggle')
      await toggle.waitFor({ state: 'visible' })
      await toggle.click()
      ensure(await page.locator('.diagnostic-panel.mobile-open').count() === 1, 'mobile diagnostic panel did not open')
      result.assertions.push('iPhone-equivalent mobile diagnostic panel opens')
    }

    ensure(pageErrors.length === 0, `uncaught page errors: ${pageErrors.join(' | ')}`)
    result.assertions.push('no uncaught page errors')
    result.pass = true
  } catch (error) {
    result.errors.push(String(error))
    const screenshotDir = path.resolve('data/qa/step6')
    fs.mkdirSync(screenshotDir, { recursive: true })
    await page.screenshot({ path: path.join(screenshotDir, `${spec.name}-failure.png`), fullPage: true }).catch(() => {})
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
