/**
 * Общая обвязка прогонов интерфейса в настоящем браузере.
 *
 * Браузер выбираем по тому, что установлено на машине участника: сначала
 * загруженный Playwright chromium, затем системные Edge и Chrome. Это позволяет
 * прогонять проверки и на Windows, и на Mac без дополнительной загрузки.
 */

import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

export const BASE = process.env.UI_BASE_URL || 'http://localhost:5173'
const SHOTS = 'tests/shots'

/** Каналы в порядке предпочтения; пустой канал — скачанный Playwright chromium. */
const CHANNELS = [undefined, 'msedge', 'chrome']

export async function launchBrowser() {
  const failures = []
  for (const channel of CHANNELS) {
    try {
      return await chromium.launch(channel ? { channel } : {})
    } catch (error) {
      failures.push(`${channel ?? 'chromium'}: ${error.message.split('\n')[0]}`)
    }
  }
  throw new Error(
    'Не удалось запустить браузер. Выполните «npx playwright install chromium».\n' +
      failures.join('\n'),
  )
}

export function createRun(name) {
  const problems = []
  mkdirSync(SHOTS, { recursive: true })

  return {
    problems,
    step(label, ok, extra = '') {
      console.log(`${ok ? 'OK  ' : 'FAIL'} ${label}${extra ? ' — ' + extra : ''}`)
      if (!ok) problems.push(label)
    },
    async shot(page, file) {
      await page.screenshot({ path: `${SHOTS}/${file}.png` })
    },
    /**
     * 401 на /api/auth/me до входа и 404 на карточку заявки — ожидаемые ответы API.
     * Браузер пишет их в консоль, дефектом интерфейса они не являются.
     */
    watch(page, { consoleErrors = true } = {}) {
      page.on('pageerror', (error) => problems.push(`ошибка страницы: ${error.message}`))
      if (!consoleErrors) return
      page.on('console', (message) => {
        const text = message.text()
        if (message.type() !== 'error') return
        if (text.includes('401') || text.includes('404')) return
        problems.push(`консоль: ${text}`)
      })
    },
    finish() {
      console.log(`\n--- ${name} ---`)
      if (problems.length === 0) console.log('Все шаги прошли.')
      else {
        console.log('Проблемы:')
        for (const item of problems) console.log(' - ' + item)
      }
      process.exit(problems.length === 0 ? 0 : 1)
    },
  }
}

/** Регистрация нового аккаунта: у каждого прогона своя изолированная история. */
export async function registerFresh(page, prefix) {
  const email = `${prefix}.${Date.now()}@example.test`
  await page.goto(BASE)
  await page.waitForSelector('.auth-card')
  await page.getByRole('tab', { name: 'Регистрация' }).click()
  await page.getByLabel('Электронная почта').fill(email)
  await page.getByLabel('Пароль').fill('TestPass12345')
  await page.getByRole('button', { name: 'Создать аккаунт' }).click()
  await page.waitForSelector('.chat', { timeout: 15000 })
  return email
}
