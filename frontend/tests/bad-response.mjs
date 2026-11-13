/**
 * Ответ сервера не в формате JSON: например, HTML-страница ошибки прокси.
 *
 * Раньше разбор такого ответа падал, и пользователь видел английский текст
 * ошибки браузера вместо понятного сообщения. Теперь это техническая ошибка
 * с кнопкой «Повторить». Ответы подменяются в браузере: настоящий backend
 * всегда отвечает JSON, а прокси перед ним может и не ответить.
 */

import { createRun, launchBrowser, registerFresh } from './lib/harness.mjs'

const run = createRun('Ответ сервера не в формате JSON')
const browser = await launchBrowser()
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
// Ответ 502 подставлен намеренно, сообщение браузера о нём дефектом не считаем.
run.watch(page, { consoleErrors: false })

await registerFresh(page, 'bad.response')

let mode = 'html502'
const requestIds = []
await page.route('**/api/dialogs/*/messages', async (route) => {
  if (route.request().method() !== 'POST') return route.continue()
  requestIds.push(route.request().postDataJSON().request_id)
  if (mode === 'html502') {
    return route.fulfill({
      status: 502,
      contentType: 'text/html',
      body: '<html><head><title>502 Bad Gateway</title></head><body><h1>502 Bad Gateway</h1></body></html>',
    })
  }
  if (mode === 'html200') {
    // Страница-заглушка с кодом 200: формально «успех», но это не ответ backend.
    return route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: '<!doctype html><html><body>Проверка доступа…</body></html>',
    })
  }
  return route.continue()
})

const expected = 'Техническая ошибка, попробуйте ещё раз.'
/** Признаки сырой ошибки разбора или HTML, которые пользователю видеть нельзя. */
const raw = /JSON|Unexpected token|SyntaxError|<html|Bad Gateway/i

// --- HTML с кодом 502 ---
await page.locator('#composer-input').fill('Проверка ответа не в формате JSON')
await page.getByRole('button', { name: 'Отправить' }).click()
await page.waitForSelector('.bubble-error', { timeout: 20000 })
const text502 = await page.locator('.bubble-error').textContent()
run.step('HTML 502: понятный русский текст', text502.includes(expected), text502)
run.step('HTML 502: показан как техническая ошибка', text502.includes('Техническая ошибка'))
run.step('HTML 502: нет текста ошибки разбора и HTML', !raw.test(text502))
run.step('HTML 502: предложен повтор', (await page.getByRole('button', { name: 'Повторить' }).count()) === 1)
await run.shot(page, '21-bad-response-502')

// --- HTML с кодом 200 ---
mode = 'html200'
const before200 = requestIds.length
// Ждём именно ответ на повтор, иначе можно прочитать ещё прежний пузырь ошибки.
const answered = page.waitForResponse(
  (response) => response.url().includes('/messages') && response.request().method() === 'POST',
)
await page.getByRole('button', { name: 'Повторить' }).click()
await answered
await page.waitForSelector('.bubble-error', { timeout: 20000 })
const text200 = await page.locator('.bubble-error').textContent()
run.step('HTML 200: понятный русский текст', text200.includes(expected), text200)
run.step('HTML 200: нет текста ошибки разбора и HTML', !raw.test(text200))
run.step(
  'повтор после ответа не в JSON сохранил request_id',
  requestIds.length === before200 + 1 && requestIds[before200] === requestIds[before200 - 1],
)

// --- Сервер снова отвечает нормально ---
mode = 'pass'
await page.getByRole('button', { name: 'Повторить' }).click()
await page.waitForFunction(
  () => !document.querySelector('.typing') && !document.querySelector('.bubble-error')
    && document.querySelectorAll('.row-bot .bubble-bot').length >= 1,
  null,
  { timeout: 30000 },
)
run.step('после восстановления повтор получил ответ', true)

await browser.close()
run.finish()
