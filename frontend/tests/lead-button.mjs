/**
 * Постоянная кнопка «Отправить заявку» в левой колонке.
 *
 * Настоящее ядро не присылает action=offer_application, поэтому предложение
 * заявки в ленте может не появиться ни разу. Проверяем, что путь к заявке есть
 * всегда: до первого сообщения, после ответа, после создания заявки, после F5
 * и на узком экране. Кнопка открывает ту же форму, что и кнопка над чатом.
 */

import { createRun, launchBrowser, registerFresh } from './lib/harness.mjs'

const run = createRun('Кнопка «Отправить заявку»')
const browser = await launchBrowser()
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
run.watch(page)

await registerFresh(page, 'lead.button')

const side = page.locator('.chat-side')
const sideButton = side.getByRole('button', { name: 'Отправить заявку', exact: true })

// --- До первого сообщения: диалога ещё нет ---
run.step('кнопка видна на пустом чате', await sideButton.isVisible())
const sideBox = await side.boundingBox()
const feedBox = await page.locator('.feed').boundingBox()
run.step('блок заявки стоит слева от чата', sideBox.x + sideBox.width <= feedBox.x)

await sideButton.click()
await page.waitForSelector('section.lead[aria-label="Форма заявки"]', { timeout: 15000 })
run.step('до первого сообщения открывается форма заявки', await page.locator('#lead-full_name').isVisible())
run.step(
  'для заявки создан диалог (над чатом появилась кнопка «Оставить заявку»)',
  await page.getByRole('button', { name: 'Оставить заявку', exact: true }).isVisible(),
)
await run.shot(page, '22-side-lead-empty')
await page.getByRole('button', { name: 'Не сейчас', exact: true }).click()
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
run.step('резюме предзаполнено, как в форме из панели над чатом', true)

await page.getByLabel('ФИО').fill('Иванов Пётр Сергеевич')
await page.getByLabel('Телефон').fill('+7 900 000-00-01')
await page.getByLabel('Компания').fill('ООО «Тестовая компания»')
await page.getByRole('button', { name: 'Проверить карточку' }).click()
await page.waitForSelector('.consent')
run.step(
  'в колонке: карточка сохранена, нужно подтвердить',
  (await side.textContent()).includes('осталось проверить и подтвердить'),
)
await page.locator('.consent input').check()
await page.getByRole('button', { name: 'Подтвердить заявку' }).click()
await page.waitForSelector('.lead-done', { timeout: 15000 })
const leadId = (await page.locator('.lead-id').textContent()).trim()
run.step('заявка создана через левую кнопку', leadId.length > 10, leadId)

// --- После создания кнопка остаётся и показывает ту же заявку ---
await page.getByRole('button', { name: 'Свернуть' }).click()
run.step('кнопка осталась после создания заявки', await sideButton.isEnabled())
run.step('в колонке сказано, что заявка создана', (await side.textContent()).includes('создана'))
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

// --- Узкий экран ---
await page.setViewportSize({ width: 390, height: 844 })
await page.waitForTimeout(400)
const button = await sideButton.boundingBox()
run.step('на узком экране кнопка видна без прокрутки', button !== null && button.y + button.height <= 844)
const mobileSide = await side.boundingBox()
const mobileFeed = await page.locator('.feed').boundingBox()
run.step('на узком экране блок заявки над лентой', mobileSide.y + mobileSide.height <= mobileFeed.y)
const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)
run.step('на узком экране нет горизонтальной прокрутки', !overflow)
await run.shot(page, '23-side-lead-mobile')

await browser.close()
run.finish()
