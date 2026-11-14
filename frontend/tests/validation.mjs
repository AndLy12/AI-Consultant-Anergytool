/**
 * Проверка полей карточки заявки.
 *
 * Дефект UI-001: при неверном телефоне показывался текст валидатора
 * «Проверьте поля: Value error, Invalid phone». Проверяем, что теперь подсказка
 * понятная, стоит рядом с нужным полем, поле подсвечено, введённое не теряется,
 * а ответы backend 422 и 409 тоже читаются по-русски.
 */

import { createRun, launchBrowser, registerFresh } from './lib/harness.mjs'

const run = createRun('Проверка полей заявки')
const browser = await launchBrowser()
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
// Ошибки 422 и 409 вызываем намеренно, сообщения браузера о них не считаем дефектом.
run.watch(page, { consoleErrors: false })

await registerFresh(page, 'validation')

await page.locator('#composer-input').fill('Хотим снизить счета за электричество')
await page.getByRole('button', { name: 'Отправить' }).click()
await page.waitForSelector('.row-bot .bubble-bot', { timeout: 30000 })

await page.getByRole('button', { name: 'Оставить заявку' }).click()
await page.waitForSelector('.lead')

const error = (field) => page.locator(`#lead-${field}-error`)
const input = (field) => page.locator(`#lead-${field}`)

// --- Пустая форма: подсказки у каждого обязательного поля ---
// Сначала дожидаемся автозаполненного резюме. Ожидание «.bubble-bot» выше может
// сработать на индикаторе «печатает», и тогда форма открывается до ответа с пустым
// резюме. Очистка уже пустого поля не вызывает ни одного события, а значит,
// не проверяет то, что должна: что ручная очистка не отменяется черновиком.
await page.waitForFunction(() => document.querySelector('#lead-summary')?.value.trim().length > 0)
await input('summary').fill('')
await page.getByRole('button', { name: 'Проверить карточку' }).click()
await page.waitForSelector('#lead-full_name-error')
run.step('пустое ФИО: подсказка у поля', (await error('full_name').textContent()).includes('фамилию'))
run.step('пустой телефон: подсказка у поля', (await error('phone').textContent()).includes('Укажите телефон'))
run.step('пустая компания: подсказка у поля', (await error('company').textContent()).includes('название компании'))
run.step('пустое резюме: подсказка у поля', (await error('summary').textContent()).includes('не может быть пустым'))
run.step('поле подсвечено рамкой', (await input('full_name').getAttribute('class')).includes('input-invalid'))
run.step('поле помечено для скринридера', (await input('full_name').getAttribute('aria-invalid')) === 'true')
run.step('фокус переведён на первое поле с ошибкой', await input('full_name').evaluate((node) => node === document.activeElement))
run.step('карточка не ушла на проверку', (await page.locator('.consent').count()) === 0)
await run.shot(page, '16-validation-empty')

// --- ФИО: только буквы и знаки имени (дефект UI-002) ---
const submit = () => page.getByRole('button', { name: 'Проверить карточку' }).click()

await input('full_name').fill('Иванов 2')
await submit()
await page.waitForSelector('#lead-full_name-error')
run.step('цифры в ФИО отклонены', (await error('full_name').textContent()).includes('не должно быть цифр'))
await run.shot(page, '17-validation-name')

await input('full_name').fill('Иванов #Пётр')
await submit()
await page.waitForSelector('#lead-full_name-error')
run.step(
  'посторонние знаки в ФИО отклонены',
  (await error('full_name').textContent()).includes('Допустимы только буквы'),
)

await input('full_name').fill('И')
await submit()
await page.waitForSelector('#lead-full_name-error')
run.step('одна буква в ФИО отклонена', (await error('full_name').textContent()).includes('не меньше 2 букв'))

// Дефис и апостроф допустимы: проверяем по отсутствию подсказки именно у ФИО,
// пока остальные поля ещё пустые и подсвечены своими ошибками.
await input('full_name').fill('Жанна д’Арк-Петрова')
await submit()
await page.waitForSelector('#lead-company-error')
run.step('дефис и апостроф в ФИО допустимы', (await error('full_name').count()) === 0)

// --- Неверный телефон: именно случай из дефекта ---
await input('full_name').fill('Иванов Пётр Сергеевич')
await input('company').fill('ООО «Тестовая компания»')
await input('summary').fill('Клиент хочет снизить счета за электричество.')
await input('phone').fill('12345')
await page.getByRole('button', { name: 'Проверить карточку' }).click()
await page.waitForSelector('#lead-phone-error')
const shortPhone = await error('phone').textContent()
run.step('короткий номер: сказано сколько цифр нужно', shortPhone.includes('от 7 до 15 цифр'))
run.step('нет текста валидатора', !shortPhone.includes('Value error') && !shortPhone.includes('Invalid phone'))
await run.shot(page, '17-validation-phone')

await input('phone').fill('не телефон')
await page.getByRole('button', { name: 'Проверить карточку' }).click()
await page.waitForSelector('#lead-phone-error')
run.step(
  'буквы в номере: перечислены допустимые знаки',
  (await error('phone').textContent()).includes('Допустимы только цифры'),
)

// --- Остальные поля не потерялись ---
run.step('ФИО сохранилось после ошибки', (await input('full_name').inputValue()) === 'Иванов Пётр Сергеевич')
run.step('компания сохранилась после ошибки', (await input('company').inputValue()).includes('Тестовая компания'))
run.step('резюме сохранилось после ошибки', (await input('summary').inputValue()).length > 0)

// --- Подсказка исчезает при правке ---
await input('phone').fill('+7 900 000-00-01')
run.step('подсказка исчезает при исправлении', (await error('phone').count()) === 0)
run.step('рамка снимается при исправлении', !((await input('phone').getAttribute('class')) ?? '').includes('input-invalid'))

// --- Ответ backend 422 раскладывается по полю ---
await page.route('**/api/dialogs/*/application', async (route) => {
  if (route.request().method() !== 'PUT') return route.continue()
  await route.fulfill({
    status: 422,
    contentType: 'application/json',
    body: JSON.stringify({
      detail: [
        {
          type: 'string_too_long',
          loc: ['body', 'company'],
          msg: 'String should have at most 300 characters',
          ctx: { max_length: 300 },
        },
      ],
    }),
  })
})
await page.getByRole('button', { name: 'Проверить карточку' }).click()
await page.waitForSelector('#lead-company-error')
const serverError = await error('company').textContent()
run.step('422 от backend показан у нужного поля', serverError.includes('не больше 300'))
run.step('введённое после ответа 422 сохранилось', (await input('phone').inputValue()) === '+7 900 000-00-01')
await run.shot(page, '18-validation-server-422')

// --- Ответ backend 409 переведён на русский ---
await page.unroute('**/api/dialogs/*/application')
await page.route('**/api/dialogs/*/application', async (route) => {
  if (route.request().method() !== 'PUT') return route.continue()
  await route.fulfill({
    status: 409,
    contentType: 'application/json',
    body: JSON.stringify({ detail: 'Application already confirmed' }),
  })
})
await page.getByRole('button', { name: 'Проверить карточку' }).click()
await page.waitForSelector('.form-error')
const conflict = await page.locator('.form-error').textContent()
run.step('409 объяснён по-русски', conflict.includes('Заявка уже создана'))
run.step('в тексте 409 нет английского', !/[A-Za-z]{4,}/.test(conflict), conflict)

// --- Сохранение проходит после снятия подмены; ФИО без отчества принимается ---
await page.unroute('**/api/dialogs/*/application')
await input('full_name').fill('Иванов Пётр')
await page.getByRole('button', { name: 'Проверить карточку' }).click()
await page.waitForSelector('.consent', { timeout: 15000 })
run.step('ФИО из двух слов принимается', true)
run.step(
  'в карточку попало введённое ФИО',
  (await page.locator('.lead-card').textContent()).includes('Иванов Пётр'),
)

await browser.close()
run.finish()
