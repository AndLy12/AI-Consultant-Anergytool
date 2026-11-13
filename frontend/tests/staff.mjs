/**
 * Экран проверки для сотрудника и повторный вход в существующий аккаунт.
 *
 * Перед прогоном роль выдаётся командой backend:
 *   docker compose exec backend python -m backend.manage grant-staff --email reviewer@example.test
 * Аккаунт reviewer@example.test с паролем TestPass12345 должен быть зарегистрирован.
 */

import { BASE, createRun, launchBrowser } from './lib/harness.mjs'

const run = createRun('Экран сотрудника')
const browser = await launchBrowser()
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
run.watch(page)

await page.goto(BASE)
await page.waitForSelector('.auth-card')
await page.getByLabel('Электронная почта').fill('reviewer@example.test')
await page.getByLabel('Пароль').fill('TestPass12345')
await page.getByRole('button', { name: 'Войти' }).click()
await page.waitForSelector('.chat', { timeout: 15000 })
run.step('вход в существующий аккаунт', true)

const staffTab = page.getByRole('button', { name: 'Проверка' })
run.step('раздел «Проверка» виден сотруднику', (await staffTab.count()) === 1)

await staffTab.click()
await page.waitForSelector('.archive-list', { timeout: 15000 })
const dialogs = await page.locator('.archive-item').count()
run.step('виден список всех диалогов', dialogs > 0, `${dialogs} шт.`)
await run.shot(page, '13-staff-dialogs')

await page.getByRole('button', { name: /Заявки/ }).click()
await page.waitForSelector('.staff-applications', { timeout: 15000 })
const applications = await page.locator('.staff-application').count()
run.step('виден список всех заявок', applications > 0, `${applications} шт.`)
await run.shot(page, '14-staff-applications')

await page.getByRole('button', { name: /Диалоги/ }).click()
await page.locator('.archive-item').first().click()
await page.waitForSelector('.feed-readonly', { timeout: 15000 })
run.step('диалог открывается на чтение', true)

await browser.close()
run.finish()
