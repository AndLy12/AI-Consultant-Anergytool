/**
 * Аварийные сценарии: сбой ядра (503), обрыв сети, повторы и двойной клик.
 *
 * Ответы backend подменяются в браузере, поэтому проверка не зависит от того,
 * удастся ли вызвать настоящий сбой ядра. Проверяем именно правило из
 * docs/BACKEND.md: после 503 повтор идёт с НОВЫМ request_id, после обрыва сети —
 * с тем же, иначе backend вызовет модель второй раз.
 */

import { createRun, launchBrowser, registerFresh } from './lib/harness.mjs'

const run = createRun('Аварийные сценарии')
const browser = await launchBrowser()
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
// Сбои здесь вызваны намеренно, поэтому сообщения браузера о них не считаем дефектом.
run.watch(page, { consoleErrors: false })

await registerFresh(page, 'failures')

let mode = '503'
const requestIds = []
await page.route('**/api/dialogs/*/messages', async (route) => {
  requestIds.push(route.request().postDataJSON().request_id)
  if (mode === '503') {
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Consultant temporarily unavailable; your message was saved' }),
    })
  } else if (mode === 'offline') {
    await route.abort('failed')
  } else {
    await route.continue()
  }
})

await page.locator('#composer-input').fill('Проверка поведения при сбое ядра')
await page.getByRole('button', { name: 'Отправить' }).click()
await page.waitForSelector('.bubble-error', { timeout: 20000 })
const errorText = await page.locator('.bubble-error').textContent()
run.step('503 показан как техническая ошибка', errorText.includes('Техническая ошибка'))
run.step('ошибка отличается от «нет данных в базе»', errorText.includes('сбой сервиса'))
run.step('предложен повтор', (await page.getByRole('button', { name: 'Повторить' }).count()) === 1)
await run.shot(page, '11-error-503')

mode = 'pass'
await page.getByRole('button', { name: 'Повторить' }).click()
await page.waitForSelector('.row-bot .bubble-bot', { timeout: 30000 })
run.step('повтор после 503 получил ответ', true)
run.step(
  'повтор после 503 ушёл с новым request_id',
  requestIds.length >= 2 && requestIds[0] !== requestIds[1],
)

mode = 'offline'
const before = requestIds.length
await page.locator('#composer-input').fill('Проверка поведения при обрыве сети')
await page.getByRole('button', { name: 'Отправить' }).click()
await page.waitForSelector('.bubble-error', { timeout: 20000 })
const offlineText = await page.locator('.bubble-error').textContent()
run.step('обрыв сети объяснён понятным текстом', offlineText.includes('Нет связи с сервером'))
await run.shot(page, '12-error-network')

mode = 'pass'
await page.getByRole('button', { name: 'Повторить' }).click()
await page.waitForFunction(
  () => document.querySelectorAll('.row-bot .bubble-bot').length >= 2,
  null,
  { timeout: 30000 },
)
run.step(
  'повтор после обрыва сети сохранил request_id',
  requestIds[before] === requestIds[before + 1],
)

await page.unroute('**/api/dialogs/*/messages')
const countBefore = await page.locator('.row-user').count()
await page.locator('#composer-input').fill('Проверка двойного клика')
const sendButton = page.getByRole('button', { name: 'Отправить' })
await sendButton.click()
run.step('кнопка отправки блокируется во время запроса', await sendButton.isDisabled())
await page.waitForFunction(
  (n) => document.querySelectorAll('.row-user').length === n + 1,
  countBefore,
  { timeout: 30000 },
)
await page.waitForTimeout(1000)
run.step(
  'одно сообщение на одну отправку',
  (await page.locator('.row-user').count()) === countBefore + 1,
)

await browser.close()
run.finish()
