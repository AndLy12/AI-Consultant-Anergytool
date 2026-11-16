/**
 * Гонка создания диалога: «Отправить» и «Оставить заявку» почти одновременно.
 *
 * Дефект (CI, коммит 1e65508, scripts/check_late_summary.mjs): отправка и кнопка
 * заявки в левой колонке, пока диалога ещё нет, создавали ДВА диалога. Второй
 * POST /api/dialogs закрывал первый, и сообщение в него получало 409
 * «диалог закрыт». На быстрой машине ответы приходят по порядку, и дефект не виден,
 * поэтому ответ POST /api/dialogs здесь задерживается в браузере:
 * - «придержать первый»: backend создаёт диалог сразу, а браузер получает ответ
 *   позже — порядок событий как в упавшем CI;
 * - «задержать все»: каждый POST /api/dialogs идёт на backend с задержкой.
 * Проверяем, что создаётся один диалог, сообщение и заявка попадают в него,
 * а ошибка создания не ломает следующую попытку.
 *
 * Аккаунт один на все сценарии: backend пропускает не больше 15 регистраций
 * в минуту с адреса, а весь набор test:ui уже близок к этому пределу.
 * Состояние «у пользователя ещё нет диалога» перед каждым сценарием создаём,
 * подменяя в браузере ответ GET /api/dialogs пустым списком. Создание диалога,
 * сообщения и заявка идут в настоящий backend.
 */

import { createRun, launchBrowser, registerFresh } from './lib/harness.mjs'

const run = createRun('Гонка создания диалога')
const browser = await launchBrowser()
const DELAY = 1000
const ID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/

const context = await browser.newContext({ viewport: { width: 1440, height: 1024 } })
const page = await context.newPage()
// 409 и 503 в части сценариев вызваны намеренно; ошибки страницы ловим всегда.
run.watch(page, { consoleErrors: false })
await registerFresh(page, 'dialog.race')

/** Журнал изменяющих запросов к диалогам текущего сценария. */
let calls = []
page.on('response', (response) => {
  const request = response.request()
  const path = new URL(response.url()).pathname
  if (request.method() !== 'GET' && path.startsWith('/api/dialogs')) {
    calls.push({ method: request.method(), path, status: response.status() })
  }
})

/**
 * Начало сценария: чат без диалога, как у только что зарегистрированного.
 * Прежние подмены снимаем; список диалогов отдаём пустым, затем перезагружаем.
 */
async function withoutDialog() {
  await page.unrouteAll({ behavior: 'wait' })
  await page.route('**/api/dialogs', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      : route.fallback(),
  )
  await page.reload()
  await page.waitForSelector('.start', { timeout: 15000 })
  calls = []
}

/**
 * Замедляет POST /api/dialogs. mode 'hold-first' — придержать в браузере только
 * ответ первого создания; 'delay-all' — задержать каждый запрос до backend;
 * 'fail-first' — первый запрос завершить 503 без обращения к backend.
 */
async function slowCreate(mode) {
  let count = 0
  await page.route('**/api/dialogs', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    const number = ++count
    if (mode === 'fail-first' && number === 1) {
      await new Promise((resolve) => setTimeout(resolve, 300))
      return route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"Service unavailable"}' })
    }
    if (mode === 'hold-first') {
      const response = await route.fetch()
      if (number === 1) await new Promise((resolve) => setTimeout(resolve, DELAY))
      return route.fulfill({ response })
    }
    await new Promise((resolve) => setTimeout(resolve, DELAY))
    return route.continue()
  })
}

const created = (calls) => calls.filter((call) => call.method === 'POST' && call.path === '/api/dialogs' && call.status === 201)
const messages = (calls) => calls.filter((call) => call.path.endsWith('/messages'))
const dialogOf = (call) => call.path.match(ID)?.[0]

/** Ждём ответ консультанта ИЛИ ошибку: при дефекте ответа нет, а шаги должны показать FAIL. */
async function waitAnswer() {
  await page.waitForSelector('.row-bot .bubble-bot .bubble-text, .bubble-error', { timeout: 30000 })
  await page.waitForTimeout(DELAY + 500)
}

async function sendText(text) {
  await page.locator('#composer-input').fill(text)
  await page.getByRole('button', { name: 'Отправить', exact: true }).click()
}

// --- 1. Как в CI: «Отправить», сразу «Оставить заявку»; ответ первого создания приходит поздно ---
{
  await withoutDialog()
  await slowCreate('hold-first')
  await sendText('Как экспортировать отчёт стенда Альфа в CSV?')
  await page.getByRole('button', { name: 'Оставить заявку', exact: true }).click()
  await waitAnswer()
  run.step('CI: создан один диалог, а не два', created(calls).length === 1, `${created(calls).length} шт.`)
  const sent = messages(calls)
  run.step('CI: сообщение принято (нет 409)', sent.length === 1 && sent[0].status === 200, sent.map((c) => c.status).join(','))
  run.step('CI: нет сообщения об ошибке', (await page.locator('.bubble-error').count()) === 0)
  run.step('CI: форма заявки открыта', await page.locator('#lead-summary').isVisible())
  await run.shot(page, '30-race-ci')

  // Заявка привязывается к тому же диалогу, что и сообщение.
  await page.getByLabel('ФИО').fill('Иванов Пётр Сергеевич')
  await page.getByLabel('Телефон').fill('+7 900 000-00-01')
  await page.getByLabel('Компания').fill('ООО «Тестовая компания»')
  await page.locator('#lead-summary').fill('Вымышленный запрос для проверки гонки')
  await page.getByRole('button', { name: 'Проверить карточку', exact: true }).click()
  await page.waitForSelector('.consent', { timeout: 15000 })
  const card = calls.find((call) => call.method === 'PUT' && call.path.endsWith('/application'))
  run.step('CI: заявка сохранена в тот же диалог, что и сообщение', card && dialogOf(card) === dialogOf(sent[0]))
}

// --- 2. Обратный порядок и двойное нажатие: «Оставить заявку» ×2, затем сразу «Отправить» ---
{
  await withoutDialog()
  await slowCreate('delay-all')
  const lead = page.getByRole('button', { name: 'Оставить заявку', exact: true })
  await lead.click()
  await lead.click()
  await sendText('Хотим снизить счета за электричество')
  await waitAnswer()
  run.step('заявка ×2 + отправка: создан один диалог', created(calls).length === 1, `${created(calls).length} шт.`)
  const sent = messages(calls)
  run.step('заявка ×2 + отправка: сообщение принято', sent.length === 1 && sent[0].status === 200, sent.map((c) => c.status).join(','))
  run.step('заявка ×2 + отправка: нет сообщения об ошибке', (await page.locator('.bubble-error').count()) === 0)
  run.step('заявка ×2 + отправка: форма заявки открыта', await page.locator('#lead-summary').isVisible())
}

// --- 3. Ошибка создания не «залипает»: после 503 следующая отправка создаёт диалог ---
{
  await withoutDialog()
  await slowCreate('fail-first')
  await sendText('Нужен резерв питания на случай отключений')
  await page.waitForSelector('.banner-error, .bubble-error', { timeout: 15000 })
  run.step('503 при создании: показана ошибка', true)
  run.step('503 при создании: сообщение не отправлялось', messages(calls).length === 0)
  await sendText('Нужен резерв питания на случай отключений')
  await waitAnswer()
  run.step('после 503: новая попытка создала диалог', created(calls).length === 1, `${created(calls).length} шт.`)
  const sent = messages(calls)
  run.step('после 503: сообщение принято', sent.length === 1 && sent[0].status === 200, sent.map((c) => c.status).join(','))
}

await context.close()
await browser.close()
run.finish()
