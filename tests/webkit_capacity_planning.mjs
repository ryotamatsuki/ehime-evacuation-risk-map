import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { devices, webkit } from 'playwright'
const baseUrl=process.env.BASE_URL ?? 'http://127.0.0.1:4173/ehime-evacuation-risk-map/'
const reportPath=path.resolve('data/qa/step8-9/webkit_capacity_planning.json')
const cases=[{name:'desktop-webkit',context:{viewport:{width:1440,height:1000}}},{name:'iphone-webkit',context:{...devices['iPhone 13']}}]
const report={test:'STEP 8-9 capacity planning Playwright WebKit smoke',native_safari_verified:false,note:'Playwright WebKit is a Safari-compatibility automation proxy, not native Safari or physical iPhone verification.',cases:[],pass:false}
function ensure(c,m){if(!c) throw new Error(m)}
function number(text){const m=String(text).replaceAll(',','').match(/-?[\d.]+/); return m?Number(m[0]):Number.NaN}
async function runCase(browser,spec){
 const context=await browser.newContext(spec.context); const page=await context.newPage(); const errors=[]; page.on('pageerror',e=>errors.push(String(e))); const result={name:spec.name,assertions:[],pass:false,errors:[]}
 try{
  await page.goto(baseUrl,{waitUntil:'domcontentloaded',timeout:30000}); await page.locator('h1').filter({hasText:'南海トラフ・本当に逃げられるかマップ'}).waitFor({timeout:30000})
  await page.waitForFunction(()=>document.body.innerText.includes('35施設'),null,{timeout:30000}); result.assertions.push('canonical v4 baseline=35 remains rendered')
  const launcher=page.getByRole('button',{name:/容量配分・投資最適化/}); await launcher.waitFor({state:'visible'}); await launcher.click()
  const dialog=page.locator('[aria-label="容量制約付き避難配分・投資最適化"]'); await dialog.waitFor({state:'visible'})
  const baseline=await page.getByTestId('step8-baseline-overload').innerText({timeout:30000}); ensure(number(baseline)===35,`STEP 8 baseline not 35: ${baseline}`)
  const unserved=number(await page.getByTestId('step8-unserved').innerText()); ensure(Number.isFinite(unserved)&&unserved>=0,`invalid STEP 8 unserved: ${unserved}`); result.assertions.push('STEP 8 production allocation loaded')
  const plus1000=page.getByRole('button',{name:'+1,000人',exact:true}); await plus1000.click(); await page.waitForTimeout(100)
  const used=number(await page.getByTestId('step9-capacity-used').innerText()); const reduction=number(await page.getByTestId('step9-unserved-reduction').innerText()); ensure(Number.isFinite(used)&&used>=0&&used<=1000.0001,`STEP 9 budget violation: ${used}`); ensure(Number.isFinite(reduction)&&reduction>=-0.0001,`STEP 9 worsened shortage: ${reduction}`); result.assertions.push('STEP 9 +1000 budget respects bound and nonnegative shortage reduction')
  await page.getByRole('button',{name:'容量配分を閉じる'}).click(); await dialog.waitFor({state:'hidden'})
  if(spec.name==='iphone-webkit'){const toggle=page.locator('.mobile-panel-toggle'); await toggle.waitFor({state:'visible'}); await toggle.click(); ensure(await page.locator('.diagnostic-panel.mobile-open').count()===1,'capacity launcher intercepted mobile diagnostic toggle'); result.assertions.push('mobile diagnostic panel remains reachable')}
  ensure(errors.length===0,`uncaught page errors: ${errors.join(' | ')}`); result.assertions.push('no uncaught page errors'); result.pass=true
 }catch(e){result.errors.push(String(e)); fs.mkdirSync(path.dirname(reportPath),{recursive:true}); await page.screenshot({path:path.join(path.dirname(reportPath),`${spec.name}-failure.png`),fullPage:true}).catch(()=>{})}finally{await context.close()} return result
}
fs.mkdirSync(path.dirname(reportPath),{recursive:true}); const browser=await webkit.launch(); try{for(const spec of cases) report.cases.push(await runCase(browser,spec))}finally{await browser.close()} report.pass=report.cases.every(x=>x.pass); fs.writeFileSync(reportPath,`${JSON.stringify(report,null,2)}\n`,'utf8'); console.log(JSON.stringify(report,null,2)); if(!report.pass) process.exit(1)
