/**
 * Окно заявки по макету Figma (кадры 20:823, 22:1028): поведение окна.
 *
 * Проверяет правила владельца для этапа 4:
 * - окно всплывает поверх чата, фон затемнён и размыт, нажатие по фону окно не закрывает;
 * - поля ФИО, Номер телефона, Компания, Резюме диалога: подписи над полями, примеры внутри;
 * - ошибки — под полями красным; буквы в телефон не вводятся;
 * - Esc закрывает окно; крестик при введённых данных спрашивает подтверждение;
 * - проверка: данные для чтения, галочка, «Изменить» с рамкой, «Подтвердить» неактивна без галочки;
 * - «Заявка создана» с номером;
 * - на 390 px окно занимает весь экран.
 *
 * Отдельного аккаунта эти проверки не создают: их вызывает tests/chat-layout.mjs
 * в своей сессии после «Нового диалога». У backend лимит 15 попыток входа и
 * регистрации в минуту с одного адреса, и ещё одна регистрация в наборе test:ui
 * приводила к 429 при входе сотрудника в tests/staff.mjs.
 */

/** Проверки окна заявки на странице с вошедшим пользователем и пустым активным диалогом. */
export async function checkLeadModal(page, run) {
  await page.setViewportSize({ width: 1440, height: 1024 })
  const sideLead = page.locator('.sidebar').getByRole('button', { name: 'Оставить заявку', exact: true })
  const overlay = page.locator('.lead-overlay')
  const dialog = page.getByRole('dialog', { name: 'Форма заявки' })

  // --- Окно поверх чата ---
  await sideLead.click()
  await dialog.waitFor({ timeout: 15000 })
  const look = await overlay.evaluate((node) => {
    const style = getComputedStyle(node)
    return { background: style.backgroundColor, blur: style.backdropFilter, position: style.position }
  })
  run.step('фон затемнён rgba(0,0,0,0.21)', look.background === 'rgba(0, 0, 0, 0.21)', look.background)
  run.step('фон размыт на 8.5 px', look.blur === 'blur(8.5px)', look.blur)
  run.step('окно поверх всего экрана', look.position === 'fixed')
  const box = await page.locator('.lead-overlay .lead').boundingBox()
  run.step('окно 1050 × 731 на месте слоя 20:868 (x 197, y 142)',
    Math.abs(box.width - 1050) < 1 && Math.abs(box.x - 197) < 1 && Math.abs(box.y - 142) < 1, `${box.x}, ${box.y}, ${box.width}, ${box.height}`)
  run.step('заголовок «Форма заявки»', (await page.locator('.lead-heading').textContent()) === 'Форма заявки')

  for (const [label, placeholder] of [
    ['ФИО', 'Иван Иванович Иванов'],
    ['Номер телефона', '+7'],
    ['Компания', 'ООО “Пример”'],
    ['Резюме диалога', 'Кратко опишите задачу'],
  ]) {
    const field = page.getByLabel(label, { exact: true })
    run.step(`поле «${label}» с примером «${placeholder}»`, (await field.getAttribute('placeholder')) === placeholder)
  }

  // Нажатие по затемнённому фону окно не закрывает.
  await page.mouse.click(40, 40)
  run.step('нажатие по фону не закрывает окно', await dialog.isVisible())
  run.step('кнопки колонки за фоном недоступны', await page.evaluate(() => {
    const target = document.elementFromPoint(100, 140)
    return target?.closest('.lead-overlay') !== null
  }))

  // Esc закрывает пустую форму без вопроса.
  await page.keyboard.press('Escape')
  run.step('Esc закрывает пустую форму', (await overlay.count()) === 0)

  // --- Ошибки под полями, буквы в телефоне ---
  await sideLead.click()
  await dialog.waitFor()
  await page.getByRole('button', { name: 'Проверить карточку', exact: true }).click()
  await page.waitForSelector('#lead-full_name-error')
  const errorColor = await page.locator('#lead-full_name-error').evaluate((node) => getComputedStyle(node).color)
  run.step('ошибка под полем красная', errorColor === 'rgb(255, 0, 50)', errorColor)
  const fieldBox = await page.locator('#lead-full_name').boundingBox()
  const errorBox = await page.locator('#lead-full_name-error').boundingBox()
  run.step('ошибка стоит под полем', errorBox.y >= fieldBox.y + fieldBox.height)
  await run.shot(page, '30-lead-errors')

  const phone = page.getByLabel('Номер телефона', { exact: true })
  await phone.pressSequentially('+7 900 abcабв 000-00-01')
  run.step('буквы в телефон не вводятся', (await phone.inputValue()) === '+7 900  000-00-01', await phone.inputValue())

  // --- Крестик при введённых данных спрашивает подтверждение ---
  await page.getByLabel('ФИО', { exact: true }).fill('Иванов Пётр Сергеевич')
  await page.getByRole('button', { name: 'Закрыть', exact: true }).click()
  const ask = page.getByRole('dialog', { name: 'Закрыть форму?' })
  await ask.waitFor({ timeout: 5000 }).catch(() => undefined)
  run.step('крестик при введённых данных спрашивает подтверждение', await ask.isVisible())
  await page.keyboard.press('Escape')
  run.step('Esc в вопросе возвращает к форме', (await ask.count()) === 0 && (await dialog.isVisible()))
  run.step('введённое не потерялось', (await page.getByLabel('ФИО', { exact: true }).inputValue()) === 'Иванов Пётр Сергеевич')
  await page.getByRole('button', { name: 'Закрыть', exact: true }).click()
  await ask.getByRole('button', { name: 'Закрыть без сохранения' }).click()
  run.step('«Закрыть без сохранения» закрывает окно', (await overlay.count()) === 0)

  // --- Проверка и согласие ---
  await sideLead.click()
  await dialog.waitFor()
  await page.getByLabel('ФИО', { exact: true }).fill('Иванов Пётр Сергеевич')
  await page.getByLabel('Номер телефона', { exact: true }).fill('+7 900 000-00-01')
  await page.getByLabel('Компания', { exact: true }).fill('ООО «Тестовая компания»')
  await page.getByLabel('Резюме диалога', { exact: true }).fill('Клиент хочет снизить счета за электроэнергию на складе.')
  await run.shot(page, '31-lead-form')
  await page.getByRole('button', { name: 'Проверить карточку', exact: true }).click()
  const review = page.getByRole('dialog', { name: 'Проверка карточки заявки' })
  await review.waitFor({ timeout: 15000 })
  run.step('проверка: данные показаны для чтения', (await review.locator('input[type="text"], textarea').count()) === 0
    && (await review.locator('.lead-card').textContent()).includes('Иванов Пётр Сергеевич'))
  const confirmButton = review.getByRole('button', { name: 'Подтвердить заявку' })
  run.step('«Подтвердить» неактивна без галочки', await confirmButton.isDisabled())
  const greyBg = await confirmButton.evaluate((node) => getComputedStyle(node).backgroundColor)
  run.step('неактивная «Подтвердить» серая', greyBg === 'rgb(234, 237, 240)', greyBg)
  const edit = review.getByRole('button', { name: 'Изменить', exact: true })
  const editLook = await edit.evaluate((node) => {
    const style = getComputedStyle(node)
    return `${style.borderTopWidth} ${style.borderTopColor} ${style.backgroundColor}`
  })
  run.step('«Изменить» — контурная (рамка, белый фон)', editLook === '2px rgb(255, 0, 50) rgb(255, 255, 255)', editLook)
  await run.shot(page, '32-lead-review')

  await review.locator('.consent input').check()
  run.step('с галочкой «Подтвердить» активна и красная', (await confirmButton.isEnabled())
    && (await confirmButton.evaluate((node) => getComputedStyle(node).backgroundColor)) === 'rgb(255, 0, 50)')
  const checked = await review.locator('.consent input').evaluate((node) => getComputedStyle(node).backgroundColor)
  run.step('отмеченная галочка зелёная #26cd58', checked === 'rgb(38, 205, 88)', checked)
  await run.shot(page, '33-lead-consent')

  // После «Изменить» и повторной проверки согласие нужно дать заново.
  await edit.click()
  await dialog.waitFor()
  run.step('«Изменить» возвращает форму с данными', (await page.getByLabel('Компания', { exact: true }).inputValue()).includes('Тестовая'))
  await page.getByRole('button', { name: 'Проверить карточку', exact: true }).click()
  await review.waitFor({ timeout: 15000 })
  run.step('после правки галочка снята', !(await review.locator('.consent input').isChecked()))
  await review.locator('.consent input').check()
  await confirmButton.click()

  const done = page.getByRole('dialog', { name: 'Заявка создана' })
  await done.waitFor({ timeout: 15000 })
  const leadId = (await page.locator('.lead-id').textContent()).trim()
  run.step('«Заявка создана» с номером', leadId.length > 10, leadId)
  const idBox = await page.locator('.lead-id').boundingBox()
  const bodyBox = await page.locator('.lead-body').boundingBox()
  run.step('номер заявки виден без прокрутки (окно открыто сверху)', idBox !== null && idBox.y >= bodyBox.y && idBox.y + idBox.height <= bodyBox.y + bodyBox.height)
  run.step('на проверке и после создания крестик закрывает без вопроса', true)
  await run.shot(page, '34-lead-created')
  await page.getByRole('button', { name: 'Закрыть', exact: true }).click()
  run.step('крестик закрывает «Заявка создана»', (await overlay.count()) === 0)

  // --- Узкий экран: окно на весь экран ---
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: 'Открыть меню' }).click()
  await sideLead.click()
  await page.getByRole('dialog', { name: 'Заявка создана' }).waitFor({ timeout: 15000 })
  const mobile = await page.locator('.lead-overlay .lead').boundingBox()
  run.step('390 px: окно на весь экран', mobile.x === 0 && mobile.y === 0 && mobile.width === 390 && mobile.height === 844,
    `${mobile.x},${mobile.y},${mobile.width},${mobile.height}`)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)
  run.step('390 px: нет горизонтальной прокрутки', !overflow)
  await run.shot(page, '35-lead-mobile')
}
