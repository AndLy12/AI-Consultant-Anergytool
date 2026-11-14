/**
 * Основной путь пользователя: вход, консультация, отладка, заявка, новый диалог,
 * история, перезагрузка, узкий экран, выход.
 *
 * Требует запущенный backend и dev-сервер (npm run dev).
 */

import { createRun, launchBrowser, registerFresh } from './lib/harness.mjs'

const run = createRun('Основной путь')
const browser = await launchBrowser()
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
run.watch(page)

await registerFresh(page, 'main.path')
run.step('регистрация и переход в чат', true)
await run.shot(page, '01-start')

const suggestions = await page.locator('.suggestion').count()
run.step('стартовые подсказки', suggestions === 6, `${suggestions} шт.`)

await page.locator('.suggestion').first().click()
await page.waitForSelector('.row-bot .bubble-bot .bubble-text', { timeout: 30000 })
const answer = await page.locator('.row-bot .bubble-text').first().textContent()
run.step('получен ответ консультанта', answer.length > 0, answer.slice(0, 50))
await run.shot(page, '02-answer')

await page.locator('#composer-input').fill('У нас три цеха, хотим видеть расход по каждому.')
await page.getByRole('button', { name: 'Отправить' }).click()
await page.waitForFunction(() => document.querySelectorAll('.row-bot').length >= 2, null, { timeout: 30000 })
run.step('второе сообщение учтено в том же диалоге', true)

await page.getByText('Отладка', { exact: true }).click()
await page.waitForSelector('.debug', { timeout: 5000 })
const debugText = await page.locator('.debug').first().textContent()
run.step('отладка показывает время ответа', debugText.includes('Время ответа'))
run.step('отладка показывает число источников', debugText.includes('Процитировано источников'))
await run.shot(page, '03-debug')

await page.getByRole('button', { name: 'Оставить заявку' }).click()
await page.waitForSelector('.lead')
await page.getByLabel('ФИО').fill('Иванов Пётр Сергеевич')
await page.getByLabel('Телефон').fill('+7 900 000-00-01')
await page.getByLabel('Компания').fill('ООО «Тестовая компания»')
const summary = await page.locator('.lead-summary').inputValue()
run.step('резюме предзаполнено', summary.length > 0)
await run.shot(page, '04-lead-form')

await page.getByRole('button', { name: 'Проверить карточку' }).click()
await page.waitForSelector('.consent')
const confirmButton = page.getByRole('button', { name: 'Подтвердить заявку' })
run.step('без согласия подтверждение заблокировано', await confirmButton.isDisabled())
await run.shot(page, '05-lead-review')

await page.locator('.consent input').check()
await confirmButton.click()
await page.waitForSelector('.lead-done', { timeout: 15000 })
const leadId = await page.locator('.lead-id').textContent()
run.step('заявка создана, показан номер', leadId.length > 10, leadId)
await run.shot(page, '06-lead-done')

await page.getByRole('button', { name: 'Свернуть' }).click()
await page.locator('#composer-input').fill('А что с резервным питанием?')
await page.getByRole('button', { name: 'Отправить' }).click()
await page.waitForFunction(() => document.querySelectorAll('.row-bot').length >= 3, null, { timeout: 30000 })
run.step('консультация продолжается после заявки', true)

await page.getByRole('button', { name: 'Новый диалог' }).click()
await page.waitForSelector('.modal')
await page.getByRole('button', { name: 'Начать новый' }).click()
await page.waitForSelector('.start', { timeout: 15000 })
run.step('новый диалог очищает экран', (await page.locator('.row').count()) === 0)
await run.shot(page, '07-new-dialog')

await page.getByRole('button', { name: 'История' }).click()
await page.waitForSelector('.archive-list', { timeout: 15000 })
const archiveCount = await page.locator('.archive-item').count()
run.step('прежний диалог в истории', archiveCount >= 1, `${archiveCount} записей`)
await run.shot(page, '08-archive')

await page.locator('.archive-item').first().click()
await page.waitForSelector('.feed-readonly', { timeout: 15000 })
run.step('архивный диалог открыт только для чтения', (await page.locator('#composer-input').count()) === 0)
await run.shot(page, '09-archive-dialog')

await page.getByRole('button', { name: '← К списку' }).click()
await page.getByRole('button', { name: '← К чату' }).click()
await page.waitForSelector('.chat', { timeout: 15000 })
run.step('после просмотра архива активный диалог пуст', (await page.locator('.start').count()) === 1)

await page.reload()
await page.waitForSelector('.chat', { timeout: 15000 })
run.step('сессия и режим отладки пережили перезагрузку', await page.locator('.switch input').isChecked())

await page.setViewportSize({ width: 390, height: 844 })
await page.waitForTimeout(400)
const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)
run.step('на узком экране нет горизонтальной прокрутки', !overflow)
await run.shot(page, '10-mobile')

await page.setViewportSize({ width: 1280, height: 900 })
await page.getByRole('button', { name: 'Выйти' }).click()
await page.waitForSelector('.auth-card', { timeout: 15000 })
run.step('выход возвращает на экран входа', true)

await browser.close()
run.finish()
