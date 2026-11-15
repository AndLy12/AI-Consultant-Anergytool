/**
 * Главное окно чата по макету Figma (кадры 9:2, 18:429, 19:488, 20:712).
 *
 * Проверяет правила владельца для этапа 3:
 * - колонка: Energytool, «Новый диалог», «Оставить заявку», история, «Выйти из аккаунта»,
 *   «Режим отладки»; пункта «База знаний» нет;
 * - приветствие по времени суток без имени пользователя;
 * - под ответом строка «Источники» (и «Отладка» только в режиме отладки), раскрывается
 *   по нажатию; в режиме отладки раскрыта сразу;
 * - Enter отправляет сообщение, Shift+Enter переносит строку;
 * - предложение заявки показывается один раз на ответ с offer_application;
 * - пункт истории открывает прошлый диалог только для чтения;
 * - на узком экране колонка за кнопкой меню, Esc её закрывает.
 * Stub-ядро не присылает offer_application, поэтому для проверки предложения ответ
 * GET диалога подменяется в браузере: меняется только поле action, остальное — настоящее.
 */

import { createRun, launchBrowser, registerFresh } from './lib/harness.mjs'
import { checkLeadModal } from './lead-modal.mjs'

const run = createRun('Главное окно чата')
const browser = await launchBrowser()
const context = await browser.newContext({ viewport: { width: 1440, height: 1024 } })
const page = await context.newPage()
run.watch(page)

// Часы браузера: 19:30 — «Добрый вечер!», как в макете 9:2.
await page.clock.setFixedTime(new Date(2026, 10, 16, 19, 30))
await registerFresh(page, 'chat.layout')
await page.waitForSelector('.start', { timeout: 15000 })

const side = page.locator('.sidebar')
run.step('колонка: «Energytool»', (await side.locator('.brand-name').textContent()) === 'Energytool')
for (const name of ['Новый диалог', 'Оставить заявку', 'История чатов', 'Выйти из аккаунта', 'Режим отладки']) {
  run.step(`колонка: кнопка «${name}»`, (await side.getByRole('button', { name, exact: true }).count()) === 1)
}
run.step('колонка: пункта «База знаний» нет', (await page.getByText('База знаний', { exact: true }).count()) === 0)
const title = (await page.locator('.start-title').textContent()).trim()
run.step('приветствие вечером: «Добрый вечер! Как я могу Вам помочь?»', title === 'Добрый вечер! Как я могу Вам помочь?', title)
run.step('в приветствии нет почты пользователя', !title.includes('@'))
const panel = await page.locator('.app-main').boundingBox()
run.step('белая панель начинается на x=266 (ширина колонки по макету)', Math.abs(panel.x - 266) < 1, String(panel.x))

// --- Shift+Enter переносит строку, Enter отправляет ---
const input = page.locator('#composer-input')
await input.fill('У нас выросли счета за свет')
await input.press('Shift+Enter')
await input.pressSequentially('что можно сделать?')
run.step('Shift+Enter переносит строку, а не отправляет', (await input.inputValue()).includes('\n') && (await page.locator('.row-user').count()) === 0)
await input.press('Enter')
await page.waitForSelector('.row-bot .bubble-bot .bubble-text', { timeout: 30000 })
run.step('Enter отправляет сообщение', (await page.locator('.row-user').count()) === 1)

// --- Строка «Источники» под ответом ---
const toggle = page.locator('.answer-toggle').first()
run.step('отладка выключена: строка «Источники» без «Отладки»', (await toggle.textContent()).trim() === 'Источники')
run.step('отладка выключена: блок свёрнут', (await toggle.getAttribute('aria-expanded')) === 'false')
await toggle.click()
run.step('нажатие раскрывает источники', (await page.locator('.answer-details .sources').count()) === 1)
run.step('без режима отладки блока отладки нет', (await page.locator('.answer-details .debug').count()) === 0)
const bubbleBox = await page.locator('.row-bot .bubble-bot').first().boundingBox()
const detailsBox = await page.locator('.answer-details').first().boundingBox()
const toggleBox = await toggle.boundingBox()
run.step('блок раскрывается под ответом, строка остаётся под блоком (как 19:488)',
  detailsBox.y >= bubbleBox.y + bubbleBox.height && toggleBox.y >= detailsBox.y + detailsBox.height)
await toggle.click()
run.step('повторное нажатие сворачивает', (await page.locator('.answer-details').count()) === 0)

const debugButton = side.getByRole('button', { name: 'Режим отладки', exact: true })
const red = await debugButton.evaluate((node) => getComputedStyle(node).backgroundColor)
run.step('«Режим отладки» выключен — красная кнопка (18:429)', red === 'rgb(255, 0, 50)', red)
await debugButton.click()
const grey = await debugButton.evaluate((node) => getComputedStyle(node).backgroundColor)
run.step('«Режим отладки» включён — серая кнопка (19:488)', grey === 'rgb(218, 224, 234)', grey)
run.step('режим отладки: строка «Отладка · Источники»', (await toggle.textContent()).replace(/\s+/g, ' ').trim() === 'Отладка · Источники')
// Блок раскрывается при следующей отрисовке после переключения режима.
await page.locator('.answer-details .debug').waitFor({ timeout: 5000 }).catch(() => undefined)
run.step('режим отладки: блок раскрыт сразу', (await page.locator('.answer-details .debug').count()) === 1)
const debugText = await page.locator('.answer-details .debug').textContent()
run.step('в блоке отладки строки макета', ['Время ответа:', 'Документы базы знаний:', 'Статус ответа:'].every((line) => debugText.includes(line)))
await run.shot(page, '28-chat-debug')
await debugButton.click()

// --- Предложение заявки: один раз на ответ с offer_application ---
await page.route('**/api/dialogs/*', async (route) => {
  if (route.request().method() !== 'GET') return route.continue()
  const response = await route.fetch()
  const body = await response.json()
  const last = [...(body.messages ?? [])].reverse().find((message) => message.role === 'assistant')
  if (last) last.result_meta = { ...last.result_meta, action: 'offer_application' }
  await route.fulfill({ response, json: body })
})
await input.fill('Хотим передать задачу менеджеру')
await input.press('Enter')
const offer = page.locator('section.offer')
await offer.waitFor({ timeout: 30000 }).catch(() => undefined)
run.step('после offer_application показано предложение заявки', await offer.isVisible())
run.step('текст предложения из макета', (await offer.textContent()).includes('Хотите, передадим вашу задачу менеджеру?'))
await run.shot(page, '29-chat-offer')
await offer.getByRole('button', { name: 'Не сейчас', exact: true }).click()
run.step('«Не сейчас» скрывает предложение', (await offer.count()) === 0)
// Тот же ответ при повторном чтении диалога (смена экрана) не должен вернуть предложение.
await side.getByRole('button', { name: 'История чатов' }).click()
await page.getByRole('button', { name: '← К чату' }).click()
await page.waitForSelector('.chat', { timeout: 15000 })
await page.waitForTimeout(500)
run.step('предложение не повторяется для того же ответа после возврата в чат', (await page.locator('section.offer').count()) === 0)
await page.unroute('**/api/dialogs/*')

// --- История: прошлый диалог открывается только для чтения ---
await side.getByRole('button', { name: 'Новый диалог', exact: true }).click()
await page.getByRole('button', { name: 'Начать новый' }).click()
await page.waitForSelector('.start', { timeout: 15000 })
const archived = side.locator('.sidebar-chat', { hasText: 'У нас выросли счета за свет' })
await archived.waitFor({ timeout: 5000 }).catch(() => undefined)
await archived.click()
await page.waitForSelector('.feed-readonly', { timeout: 15000 })
run.step('пункт истории открывает прошлый диалог для чтения', (await page.locator('#composer-input').count()) === 0)
run.step('открытый диалог подсвечен в истории', (await archived.getAttribute('aria-current')) === 'true')

// --- Узкий экран ---
await page.setViewportSize({ width: 390, height: 844 })
await page.waitForTimeout(400)
run.step('390 px: колонка спрятана', !(await side.getByRole('button', { name: 'Новый диалог', exact: true }).isVisible()))
await page.getByRole('button', { name: 'Открыть меню' }).click()
await page.waitForTimeout(300)
run.step('390 px: меню открывает колонку', await side.getByRole('button', { name: 'Новый диалог', exact: true }).isVisible())
await page.keyboard.press('Escape')
await page.waitForTimeout(300)
run.step('390 px: Esc закрывает колонку', !(await side.getByRole('button', { name: 'Новый диалог', exact: true }).isVisible()))
const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)
run.step('390 px: нет горизонтальной прокрутки', !overflow)

// --- Окно заявки: в той же сессии, чтобы не создавать ещё один аккаунт ---
await page.setViewportSize({ width: 1440, height: 1024 })
await page.getByRole('button', { name: '← К списку' }).click()
await page.getByRole('button', { name: '← К чату' }).click()
await page.waitForSelector('.start', { timeout: 15000 })
await checkLeadModal(page, run)

await browser.close()
run.finish()
