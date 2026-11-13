/**
 * Обновление страницы (F5) во время ожидания ответа и после сбоя.
 *
 * Раньше индикатор «печатает» и кнопка «Повторить» жили только в памяти
 * страницы и пропадали после перезагрузки, хотя backend хранит сообщение
 * со статусом processing или failed.
 *
 * Настоящее ожидание или сбой ядра в режиме stub не получить, поэтому ответ
 * GET /api/dialogs/{id} подменяется в браузере: последнее сообщение клиента
 * помечается как processing или failed, а ответ консультанта на него скрывается.
 * Остальные запросы, включая повторную отправку, идут в настоящий backend.
 */

import { createRun, launchBrowser, registerFresh } from './lib/harness.mjs'

const run = createRun('Перезагрузка во время ожидания и после сбоя')
const browser = await launchBrowser()
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
run.watch(page)

await registerFresh(page, 'reload')

await page.locator('#composer-input').fill('Хотим снизить счета за электричество')
await page.getByRole('button', { name: 'Отправить' }).click()
// Ждём настоящий ответ, а не индикатор «печатает» с тем же оформлением пузыря.
await page.waitForFunction(
  () => !document.querySelector('.typing') && document.querySelectorAll('.row-bot .bubble-bot').length >= 1,
  null,
  { timeout: 30000 },
)

// --- Подмена истории: 'processing', 'failed' или null (настоящий ответ backend) ---
let fake = null
let failedRequestId = ''
await page.route('**/api/dialogs/*', async (route) => {
  if (route.request().method() !== 'GET' || !fake) return route.continue()
  const response = await route.fetch()
  const dialog = await response.json()
  const lastUser = dialog.messages.map((m) => m.role).lastIndexOf('user')
  dialog.messages = dialog.messages.slice(0, lastUser + 1)
  dialog.messages[lastUser].status = fake
  failedRequestId = dialog.messages[lastUser].request_id
  await route.fulfill({ response, json: dialog })
})

const sentIds = []
await page.route('**/api/dialogs/*/messages', async (route) => {
  if (route.request().method() === 'POST') sentIds.push(route.request().postDataJSON().request_id)
  await route.continue()
})

// --- F5, пока backend ещё готовит ответ ---
fake = 'processing'
await page.reload()
await page.waitForSelector('.chat', { timeout: 15000 })
await page.waitForSelector('.typing')
run.step('после F5 виден индикатор ожидания', (await page.locator('.typing').count()) === 1)
run.step('во время ожидания отправка заблокирована', await page.locator('#composer-input').isDisabled())
run.step('ошибка не показана, пока ответ готовится', (await page.locator('.bubble-error').count()) === 0)
await run.shot(page, '19-reload-processing')

// Ответ «готов»: интерфейс сам перечитывает диалог и показывает его.
fake = null
await page.waitForFunction(() => !document.querySelector('.typing'))
run.step('ответ появился без перезагрузки', (await page.locator('.row-bot .bubble-bot').count()) >= 1)
run.step('после ответа отправка снова доступна', !(await page.locator('#composer-input').isDisabled()))

// --- F5 после сбоя ядра ---
fake = 'failed'
await page.reload()
await page.waitForSelector('.chat', { timeout: 15000 })
await page.waitForSelector('.bubble-error')
const errorText = await page.locator('.bubble-error').textContent()
run.step('после F5 сбой показан как техническая ошибка', errorText.includes('Техническая ошибка'))
run.step('ошибка отличается от «нет данных в базе»', errorText.includes('сбой сервиса'))
run.step('после F5 осталась кнопка «Повторить»', (await page.getByRole('button', { name: 'Повторить' }).count()) === 1)
run.step('у сообщения есть пометка о неполученном ответе', (await page.locator('.row-note').count()) === 1)
await run.shot(page, '20-reload-failed')

fake = null
const before = sentIds.length
await page.getByRole('button', { name: 'Повторить' }).click()
await page.waitForFunction(
  () => !document.querySelector('.typing') && !document.querySelector('.bubble-error'),
  null,
  { timeout: 30000 },
)
run.step('повтор отправил сообщение один раз', sentIds.length === before + 1, `${sentIds.length - before} шт.`)
run.step('повтор после сбоя ушёл с новым request_id', Boolean(sentIds[before]) && sentIds[before] !== failedRequestId)
run.step('после повтора получен ответ', (await page.locator('.row-bot .bubble-bot').count()) >= 2)

await browser.close()
run.finish()
