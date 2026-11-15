/**
 * Экран входа и регистрации по макету Figma: поведение кнопок и полей.
 *
 * Проверяет правила владельца для этапа 2:
 * - вход: «Далее» серая и неактивная, пока не заполнены оба поля, затем красная;
 * - «Зарегистрируйтесь» нажимается всегда (и в сером виде) и переносит почту;
 * - регистрация: «Далее» красная только при похожей на адрес почте и пароле от 10 символов;
 * - почта видна обычным текстом, пароль скрыт, глазок показывает символы;
 * - Enter в поле отправляет форму.
 * Цвета сверяются с токенами макета (#ff0032 — красная, #eaedf0 — серая).
 */

import { BASE, createRun, launchBrowser } from './lib/harness.mjs'

const RED = 'rgb(255, 0, 50)'
const GREY = 'rgb(234, 237, 240)'

const run = createRun('Экран входа и регистрации')
const browser = await launchBrowser()
const page = await browser.newPage({ viewport: { width: 1440, height: 1024 } })
run.watch(page)

const email = page.getByLabel('Электронная почта')
const password = page.getByLabel('Пароль')
const next = page.locator('.auth-form button[type="submit"]')
const toRegister = page.getByRole('button', { name: 'Зарегистрируйтесь' })
const background = (locator) => locator.evaluate((node) => getComputedStyle(node).backgroundColor)

await page.goto(BASE)
await page.waitForSelector('.auth-card')

// --- Вход ---
run.step('вход: заголовок как в макете', (await page.locator('.auth-title').textContent()) === 'Войдите по электронной почте')
run.step('вход: «Далее» неактивна при пустых полях', await next.isDisabled())
run.step('вход: «Далее» серая при пустых полях', (await background(next)) === GREY)
run.step('вход: «Зарегистрируйтесь» красная при пустых полях', (await background(toRegister)) === RED)

await email.fill('ivanov@example.test')
run.step('вход: только почта — «Далее» всё ещё неактивна', await next.isDisabled())
await password.fill('TestPass12345')
run.step('вход: оба поля — «Далее» активна', await next.isEnabled())
run.step('вход: оба поля — «Далее» красная', (await background(next)) === RED)
run.step('вход: оба поля — «Зарегистрируйтесь» серая', (await background(toRegister)) === GREY)
run.step('вход: серая «Зарегистрируйтесь» остаётся нажимаемой', await toRegister.isEnabled())

run.step('почта видна обычным текстом', (await email.getAttribute('type')) === 'email')
run.step('пароль скрыт', (await password.getAttribute('type')) === 'password')
const eye = page.getByRole('button', { name: 'Показать символы' })
await eye.click()
run.step('глазок показывает пароль', (await password.getAttribute('type')) === 'text')
run.step('глазок сообщает состояние для диктора', (await eye.getAttribute('aria-pressed')) === 'true')
await eye.click()
run.step('повторное нажатие снова скрывает пароль', (await password.getAttribute('type')) === 'password')

/*
 * Enter отправляет форму. Запросы входа/регистрации перехватываются в браузере и
 * до backend не доходят: у backend лимит 15 попыток входа в минуту с одного адреса,
 * а весь набор тестов и так регистрирует много аккаунтов подряд — лишние попытки
 * отсюда приводили к 429 в следующем тесте (staff.mjs). Настоящий вход и регистрацию
 * через этот экран проверяют registerFresh во всех тестах и staff.mjs.
 */
const sent = []
async function interceptAuth(path, status, body) {
  await page.route(`**/api/auth/${path}`, async (route) => {
    sent.push({ path, body: route.request().postDataJSON() })
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
  })
}
await interceptAuth('login', 401, { detail: 'Invalid email or password' })
await password.press('Enter')
await page.locator('.auth-error').waitFor({ timeout: 10000 }).catch(() => undefined)
const loginSent = sent.find((item) => item.path === 'login')
run.step(
  'Enter в поле пароля отправляет форму входа с введёнными данными',
  loginSent?.body?.email === 'ivanov@example.test' && loginSent?.body?.password === 'TestPass12345',
)
run.step('ответ об ошибке показан под полями', await page.locator('.auth-error').isVisible())
await run.shot(page, '26-auth-login-error')

// --- Переход на регистрацию переносит почту ---
await toRegister.click()
run.step(
  'регистрация: двухстрочный заголовок',
  (await page.locator('.auth-title').innerText()).replace(/\s+/g, ' ').trim() === 'Зарегистрируйтесь по электронной почте',
)
run.step('регистрация: почта перенесена', (await email.inputValue()) === 'ivanov@example.test')
run.step('регистрация: пароль очищен', (await password.inputValue()) === '')
run.step('регистрация: подсказка «Не менее 10 символов»', await page.getByText('Не менее 10 символов').isVisible())
run.step('регистрация: ошибка входа не переносится', (await page.locator('.auth-error').count()) === 0)

await password.fill('123456789')
run.step('регистрация: 9 символов — «Далее» неактивна', await next.isDisabled())
await password.fill('1234567890')
run.step('регистрация: 10 символов — «Далее» активна и красная', (await next.isEnabled()) && (await background(next)) === RED)
await email.fill('не-почта')
run.step('регистрация: неверная почта — «Далее» неактивна', await next.isDisabled())

const toLogin = page.getByRole('button', { name: 'Войдите' })
run.step('регистрация: «Войдите» серая, но нажимаемая', (await background(toLogin)) === GREY && (await toLogin.isEnabled()))
await toLogin.click()
run.step('«Войдите» возвращает на вход с той же почтой', (await page.locator('.auth-title').textContent()) === 'Войдите по электронной почте' && (await email.inputValue()) === 'не-почта')

// Регистрация через Enter: запрос перехвачен (см. выше), проверяем, что он ушёл с данными формы.
// Код 401 выбран намеренно: обвязка считает его ожидаемым в консоли; проверяем только сам запрос.
await interceptAuth('register', 401, { detail: 'Registration rejected in test' })
await toRegister.click()
await email.fill('auth.screen@example.test')
await password.fill('TestPass12345')
await password.press('Enter')
await page.locator('.auth-error').waitFor({ timeout: 10000 }).catch(() => undefined)
const registerSent = sent.find((item) => item.path === 'register')
run.step(
  'Enter на регистрации отправляет форму с введёнными данными',
  registerSent?.body?.email === 'auth.screen@example.test' && registerSent?.body?.password === 'TestPass12345',
)
run.step('оба запроса перехвачены в браузере и не расходуют лимит backend', sent.length === 2)

// --- Узкий экран ---
// Новая страница браузера — отдельный контекст без cookie, поэтому снова экран входа.
const narrow = await browser.newPage({ viewport: { width: 390, height: 844 } })
await narrow.goto(BASE)
await narrow.waitForSelector('.auth-card')
const overflow = await narrow.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
run.step('390 px: нет горизонтальной прокрутки на экране входа', !overflow)
await run.shot(narrow, '27-auth-narrow')

await browser.close()
run.finish()
