/**
 * Плашка «Демо-режим» по полю core_demo из GET /health.
 *
 * В демо-режиме core_mode равен 'real', поэтому прежняя плашка «тестовый режим»
 * не появлялась, хотя ответы строятся на вымышленных документах. Сначала сверяем
 * плашку с настоящим ответом backend (прогон идёт и в stub, и в demo), затем
 * подменяем /health в браузере, чтобы проверить оба значения в одном прогоне
 * и старый формат ответа без поля core_demo.
 */

import { BASE, createRun, launchBrowser, registerFresh } from './lib/harness.mjs'

const run = createRun('Плашка «Демо-режим»')
const browser = await launchBrowser()
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
run.watch(page)

const authBadge = page.locator('.auth-card .badge-demo')
const headerBadge = page.locator('.app-header .badge-demo')

/** Подменяет ответ /health; null — снять подмену и отдать настоящий ответ. */
async function fakeHealth(body) {
  await page.unroute('**/health')
  if (body) {
    await page.route('**/health', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }),
    )
  }
}

// --- Настоящий backend ---
const real = await (await page.request.get(`${BASE}/health`)).json()
const realDemo = real.core_demo === true
await page.goto(BASE)
await page.waitForSelector('.auth-card')
await page.waitForTimeout(500)
run.step(
  `экран входа: плашка соответствует настоящему /health (core_demo=${real.core_demo})`,
  (await authBadge.count()) === (realDemo ? 1 : 0),
)
await registerFresh(page, 'demo.badge')
await page.waitForTimeout(500)
run.step(
  'шапка: плашка соответствует настоящему /health',
  (await headerBadge.count()) === (realDemo ? 1 : 0),
)

// --- core_demo: true ---
await fakeHealth({ status: 'ok', core_mode: 'real', core_demo: true, core_ready: true, frontend: 'react' })
await page.reload()
await page.waitForSelector('.chat', { timeout: 15000 })
await headerBadge.waitFor({ timeout: 5000 }).catch(() => undefined)
run.step('core_demo=true: в шапке плашка «Демо-режим»', (await headerBadge.textContent())?.trim() === 'Демо-режим')
run.step(
  'core_demo=true: плашка «тестовый режим» не показана (core_mode=real)',
  (await page.locator('.app-header').textContent()).includes('тестовый режим') === false,
)
const badgeBox = await headerBadge.boundingBox()
run.step('плашка маленькая, не шире 160px', badgeBox !== null && badgeBox.width <= 160)
await run.shot(page, '24-demo-badge')

await page.setViewportSize({ width: 390, height: 844 })
await page.waitForTimeout(300)
run.step('на узком экране плашка видна', await headerBadge.isVisible())
const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)
run.step('на узком экране нет горизонтальной прокрутки', !overflow)
await page.setViewportSize({ width: 1280, height: 900 })

await page.getByRole('button', { name: 'Выйти' }).click()
await page.waitForSelector('.auth-card')
await page.reload()
await page.waitForSelector('.auth-card')
await authBadge.waitFor({ timeout: 5000 }).catch(() => undefined)
run.step('core_demo=true: предупреждение есть и на экране входа', await authBadge.isVisible())

// --- core_demo: false ---
await fakeHealth({ status: 'ok', core_mode: 'real', core_demo: false, core_ready: true, frontend: 'react' })
await page.reload()
await page.waitForSelector('.auth-card')
await page.waitForTimeout(500)
run.step('core_demo=false: на экране входа плашки нет', (await authBadge.count()) === 0)

// --- Старый формат: поля core_demo нет ---
await fakeHealth({ status: 'ok', core_mode: 'stub' })
await page.reload()
await page.waitForSelector('.auth-card')
await page.waitForTimeout(500)
run.step('без поля core_demo плашки «Демо-режим» нет', (await authBadge.count()) === 0)
run.step(
  'без поля core_demo прежняя плашка заглушки на месте',
  (await page.locator('.auth-card').textContent()).includes('Тестовый режим'),
)

await fakeHealth(null)
await browser.close()
run.finish()
