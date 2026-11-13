/**
 * Регрессия интеграции: карточка открыта до получения ответа консультанта.
 *
 * Управляем доставкой HTTP-ответа через Playwright, без угадывания задержки sleep.
 * Backend и ядро отвечают штатно; браузер получает их ответ после действия в форме.
 * Исходники и собственные тесты Даши не меняем. Аккаунты только вымышленные.
 */

import assert from 'node:assert/strict'
import { launchBrowser, registerFresh } from '../frontend/tests/lib/harness.mjs'

const browser = await launchBrowser()
try {
  for (const mode of ['untouched', 'edited', 'submitted']) {
    const context = await browser.newContext()
    let release
    const gate = new Promise((resolve) => { release = resolve })
    let arrived
    const responseReady = new Promise((resolve) => { arrived = resolve })
    try {
      const page = await context.newPage()
      const errors = []
      page.on('pageerror', (error) => errors.push(error.message))
      await registerFresh(page, `late-summary-${mode}`)
      await page.route('**/api/dialogs/*/messages', async (route) => {
        const response = await route.fetch()
        assert.equal(response.status(), 200)
        arrived()
        await gate
        await route.fulfill({ response })
      })
      await page.locator('#composer-input').fill('Как экспортировать отчёт стенда Альфа в CSV?')
      await page.getByRole('button', { name: 'Отправить', exact: true }).click()
      await page.getByRole('button', { name: 'Оставить заявку', exact: true }).click()
      const summary = page.locator('#lead-summary')
      await summary.waitFor()
      assert.equal(await summary.inputValue(), '')
      if (mode === 'edited') {
        await summary.fill('Резюме, введённое пользователем вручную')
      } else if (mode === 'submitted') {
        // Даже отправка пустой формы закрепляет её значения за пользователем.
        await page.getByRole('button', { name: 'Проверить карточку', exact: true }).click()
        await page.locator('#lead-summary-error').waitFor()
      }
      // Не выпускаем ответ до совершения нужного действия. Внешних API здесь нет:
      // общий harness запускает backend в stub/demo на одноразовой БД.
      await responseReady
      release()
      await page.locator('.typing').waitFor({ state: 'detached' })
      await page.locator('#composer-input').waitFor({ state: 'visible' })
      if (mode === 'untouched') {
        await page.waitForFunction(() => document.querySelector('#lead-summary')?.value.trim().length > 0)
      } else if (mode === 'edited') {
        assert.equal(await summary.inputValue(), 'Резюме, введённое пользователем вручную')
      } else {
        assert.equal(await summary.inputValue(), '')
        assert.equal(await page.locator('#lead-summary-error').count(), 1)
      }
      assert.deepEqual(errors, [])
      console.log(`OK late summary: ${mode}`)
    } finally {
      release()
      await context.close()
    }
  }
} finally {
  await browser.close()
}
