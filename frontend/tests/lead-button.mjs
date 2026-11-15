/**
 * Кнопка «Оставить заявку» в левой колонке (макет Figma 9:2) работает всегда.
 *
 * Настоящее ядро не присылает action=offer_application, поэтому предложение
 * заявки в ленте может не появиться ни разу. Проверяем, что путь к заявке есть
 * всегда: до первого сообщения, после ответа, после создания заявки, после F5
 * и на узком экране (там колонка открывается кнопкой меню).
 *
 * Раньше это была кнопка «Отправить заявку» в отдельном блоке над чатом; по макету
 * она стала пунктом колонки «Оставить заявку», панель над чатом убрана.
 */

import { createRun, launchBrowser, registerFresh } from './lib/harness.mjs'

const run = createRun('Кнопка «Оставить заявку» в колонке')
const browser = await launchBrowser()
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
run.watch(page)

await registerFresh(page, 'lead.button')

const side = page.locator('.sidebar')
const sideButton = side.getByRole('button', { name: 'Оставить заявку', exact: true })

// --- До первого сообщения: диалога ещё нет ---
run.step('кнопка видна на пустом чате', await sideButton.isVisible())
const sideBox = await side.boundingBox()
const feedBox = await page.locator('.feed').boundingBox()
run.step('колонка стоит слева от чата', sideBox.x + sideBox.width <= feedBox.x)

await sideButton.click()
await page.waitForSelector('section.lead[aria-label="Форма заявки"]', { timeout: 15000 })
run.step('до первого сообщения открывается форма заявки', await page.locator('#lead-full_name').isVisible())
// История перечитывается после создания диалога, поэтому ждём подсветку.
await side.locator('.sidebar-chat-active').waitFor({ timeout: 5000 }).catch(() => undefined)
run.step('для заявки создан диалог (он подсвечен в истории)', (await side.locator('.sidebar-chat-active').count()) === 1)
await run.shot(page, '22-side-lead-empty')
// Кнопки «Не сейчас» в окне заявки нет (макет 20:823): закрывает крестик. Форма пустая,
// поэтому окно закрывается без вопроса.
await page.getByRole('button', { name: 'Закрыть', exact: true }).click()
run.step('форма закрывается, чат доступен', await page.locator('#composer-input').isEnabled())

// --- После ответа консультанта: та же форма с черновиком резюме ---
await page.locator('#composer-input').fill('Хотим снизить счета за электричество в магазине')
await page.getByRole('button', { name: 'Отправить', exact: true }).click()
await page.waitForSelector('.row-bot .bubble-bot .bubble-text', { timeout: 30000 })
run.step('получен ответ консультанта', true)

await sideButton.click()
await page.waitForSelector('#lead-summary')
await page.waitForFunction(() => document.querySelector('#lead-summary')?.value.trim().length > 0, null, {
  timeout: 15000,
})
run.step('резюме предзаполнено черновиком', true)

await page.getByLabel('ФИО').fill('Иванов Пётр Сергеевич')
await page.getByLabel('Телефон').fill('+7 900 000-00-01')
await page.getByLabel('Компания').fill('ООО «Тестовая компания»')
await page.getByRole('button', { name: 'Проверить карточку' }).click()
await page.waitForSelector('.consent')
run.step('карточка сохранена, открыт шаг проверки', await page.locator('.consent').isVisible())
await page.locator('.consent input').check()
await page.getByRole('button', { name: 'Подтвердить заявку' }).click()
await page.waitForSelector('.lead-done', { timeout: 15000 })
const leadId = (await page.locator('.lead-id').textContent()).trim()
run.step('заявка создана через кнопку колонки', leadId.length > 10, leadId)

// --- После создания кнопка остаётся и показывает ту же заявку ---
await page.getByRole('button', { name: 'Свернуть' }).click()
run.step('кнопка осталась после создания заявки', await sideButton.isEnabled())
await sideButton.click()
await page.waitForSelector('.lead-done')
run.step(
  'повторное нажатие показывает ту же заявку, а не новую форму',
  (await page.locator('.lead-id').textContent()).trim() === leadId,
)
await page.getByRole('button', { name: 'Свернуть' }).click()

// --- После F5 ---
await page.reload()
await page.waitForSelector('.chat', { timeout: 15000 })
run.step('кнопка на месте после перезагрузки', await sideButton.isVisible())
await sideButton.click()
await page.waitForSelector('.lead-done')
run.step(
  'после перезагрузки открывается та же заявка',
  (await page.locator('.lead-id').textContent()).trim() === leadId,
)
await page.getByRole('button', { name: 'Свернуть' }).click()

// --- Узкий экран: колонка спрятана за кнопкой меню ---
await page.setViewportSize({ width: 390, height: 844 })
await page.waitForTimeout(400)
run.step('на узком экране колонка спрятана', !(await sideButton.isVisible()))
await page.getByRole('button', { name: 'Открыть меню' }).click()
await sideButton.waitFor({ state: 'visible', timeout: 5000 }).catch(() => undefined)
const button = await sideButton.boundingBox()
run.step('после нажатия меню кнопка видна без прокрутки', button !== null && button.y + button.height <= 844)
await sideButton.click()
await page.waitForSelector('.lead-done')
run.step('на узком экране кнопка открывает ту же заявку', (await page.locator('.lead-id').textContent()).trim() === leadId)
// Колонка уезжает с анимацией 0.2 с.
await sideButton.waitFor({ state: 'hidden', timeout: 5000 }).catch(() => undefined)
run.step('после нажатия меню закрылось', !(await sideButton.isVisible()))
const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)
run.step('на узком экране нет горизонтальной прокрутки', !overflow)
await run.shot(page, '23-side-lead-mobile')

await browser.close()
run.finish()
