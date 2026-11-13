/**
 * Изоляция пользователей и защита заявки от дублей.
 *
 * Проверяем требования кейса: два пользователя не видят диалоги друг друга,
 * правка карточки отменяет прежнее согласие, повторное подтверждение не создаёт
 * вторую заявку. Каждый пользователь работает в своём контексте браузера:
 * у контекстов отдельные cookie, как у разных людей.
 */

import { BASE, createRun, launchBrowser, registerFresh } from './lib/harness.mjs'

const run = createRun('Изоляция и защита от дублей')
const browser = await launchBrowser()

// --- Пользователь A: диалог и заявка ---
const contextA = await browser.newContext({ viewport: { width: 1280, height: 900 } })
const pageA = await contextA.newPage()
run.watch(pageA)
await registerFresh(pageA, 'iso.a')

await pageA.locator('#composer-input').fill('Секретный вопрос пользователя А про счётчики')
await pageA.getByRole('button', { name: 'Отправить' }).click()
// bubble-bot есть и у индикатора «печатает». Ждём текст завершённого ответа,
// чтобы начать заполнение карточки после появления её черновика резюме.
await pageA.waitForSelector('.row-bot .bubble-bot .bubble-text', { timeout: 30000 })
run.step('пользователь А получил ответ', true)

await pageA.getByRole('button', { name: 'Оставить заявку' }).click()
await pageA.waitForSelector('.lead')
await pageA.getByLabel('ФИО').fill('Иванов Пётр Сергеевич')
await pageA.getByLabel('Телефон').fill('+7 900 000-00-01')
await pageA.getByLabel('Компания').fill('ООО «Тестовая компания»')
await pageA.getByRole('button', { name: 'Проверить карточку' }).click()
await pageA.waitForSelector('.consent')

// Правка карточки обязана сбросить согласие: backend считает прежнюю версию устаревшей.
await pageA.locator('.consent input').check()
await pageA.getByRole('button', { name: 'Изменить' }).click()
await pageA.waitForSelector('.lead-summary')
await pageA.getByLabel('Телефон').fill('+7 900 000-00-02')
await pageA.getByRole('button', { name: 'Проверить карточку' }).click()
await pageA.waitForSelector('.consent')
run.step(
  'после правки карточки согласие требуется заново',
  !(await pageA.locator('.consent input').isChecked()),
)
run.step(
  'подтверждение заблокировано до нового согласия',
  await pageA.getByRole('button', { name: 'Подтвердить заявку' }).isDisabled(),
)

await pageA.locator('.consent input').check()
await pageA.getByRole('button', { name: 'Подтвердить заявку' }).click()
await pageA.waitForSelector('.lead-done', { timeout: 15000 })
const firstId = await pageA.locator('.lead-id').textContent()
run.step('заявка создана', firstId.length > 10)

// Повторное открытие карточки показывает ту же заявку, а не новую.
await pageA.getByRole('button', { name: 'Свернуть' }).click()
await pageA.getByRole('button', { name: 'Моя заявка' }).click()
await pageA.waitForSelector('.lead-done')
const repeatedId = await pageA.locator('.lead-id').textContent()
run.step('повторный заход показывает ту же заявку', repeatedId === firstId, repeatedId)

// --- Пользователь B: своя история ---
const contextB = await browser.newContext({ viewport: { width: 1280, height: 900 } })
const pageB = await contextB.newPage()
run.watch(pageB)
await registerFresh(pageB, 'iso.b')

const startVisible = await pageB.locator('.start').count()
run.step('у нового пользователя пустой чат', startVisible === 1)

await pageB.getByRole('button', { name: 'История' }).click()
await pageB.waitForSelector('.screen', { timeout: 15000 })
const foreignDialogs = await pageB.locator('.archive-item').count()
run.step('пользователь B не видит диалогов пользователя A', foreignDialogs === 0, `${foreignDialogs} записей`)

const staffVisible = await pageB.getByRole('button', { name: 'Проверка' }).count()
run.step('обычному пользователю раздел «Проверка» не показан', staffVisible === 0)

// --- Пользователь A по-прежнему видит свой диалог ---
await pageA.getByRole('button', { name: 'История' }).click()
await pageA.waitForSelector('.archive-list', { timeout: 15000 })
const ownDialogs = await pageA.locator('.archive-item').count()
run.step('пользователь A видит свой диалог', ownDialogs >= 1, `${ownDialogs} записей`)

await contextA.close()
await contextB.close()
await browser.close()
run.finish()
