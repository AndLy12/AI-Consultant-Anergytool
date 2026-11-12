/**
 * Проверка полей карточки заявки на стороне интерфейса.
 *
 * Повторяет ограничения backend (backend/app.py, модель ApplicationInput), чтобы
 * пользователь видел понятную подсказку рядом с полем до отправки, а не ответ
 * валидатора вида «Value error, Invalid phone». Это не замена серверной проверки:
 * решение всё равно принимает backend, ответ 422 разбирается отдельно и тоже
 * раскладывается по полям.
 */

export type FieldName = 'full_name' | 'phone' | 'company' | 'summary'

export type FieldErrors = Partial<Record<FieldName, string>>

export const FIELD_LABEL: Record<FieldName, string> = {
  full_name: 'ФИО',
  phone: 'Телефон',
  company: 'Компания',
  summary: 'Резюме',
}

/** Порядок полей в форме: по нему выбираем, на какое поле поставить фокус. */
export const FIELD_ORDER: FieldName[] = ['full_name', 'phone', 'company', 'summary']

const LIMITS = {
  full_name: { min: 2, max: 200 },
  company: { min: 1, max: 300 },
  summary: { min: 1, max: 10000 },
  phone: { maxLength: 30, minDigits: 7, maxDigits: 15 },
} as const

/** Backend допускает цифры, пробелы, скобки, дефис и плюс в начале номера. */
const PHONE_ALLOWED = /^\+?[\d\s()-]+$/

/**
 * Состав ФИО: буквы русского и латинского алфавита, пробел, дефис, точка
 * и апостроф (оба написания, прямое и типографское). Это ограничение интерфейса:
 * backend проверяет только длину, но цифры и знаки в имени — почти всегда опечатка.
 * Число слов не ограничиваем: встречаются имена без отчества и двойные фамилии.
 */
const NAME_ALLOWED = /^[A-Za-zА-Яа-яЁё\s.'’-]+$/
const NAME_LETTER = /[A-Za-zА-Яа-яЁё]/g

function checkFullName(value: string): string | undefined {
  if (!value) return 'Укажите имя и фамилию'
  if (value.length > LIMITS.full_name.max) {
    return `Слишком длинный текст: не больше ${LIMITS.full_name.max} символов`
  }
  if (/\d/.test(value)) return 'В ФИО не должно быть цифр'
  if (!NAME_ALLOWED.test(value)) {
    return 'Допустимы только буквы, пробел, дефис, точка и апостроф'
  }
  if ((value.match(NAME_LETTER) ?? []).length < LIMITS.full_name.min) {
    return `Слишком короткое значение: нужно не меньше ${LIMITS.full_name.min} букв`
  }
  return undefined
}

function checkPhone(value: string): string | undefined {
  if (!value) return 'Укажите телефон, например +7 900 000-00-01'
  if (value.length > LIMITS.phone.maxLength) {
    return `Слишком длинный номер: не больше ${LIMITS.phone.maxLength} символов`
  }
  if (!PHONE_ALLOWED.test(value)) {
    return 'Допустимы только цифры, пробелы, скобки, дефис и знак + в начале'
  }
  const digits = value.replace(/\D/g, '').length
  if (digits < LIMITS.phone.minDigits || digits > LIMITS.phone.maxDigits) {
    return `В номере должно быть от ${LIMITS.phone.minDigits} до ${LIMITS.phone.maxDigits} цифр, сейчас ${digits}`
  }
  return undefined
}

function checkText(
  name: 'company' | 'summary',
  value: string,
  empty: string,
): string | undefined {
  const limits = LIMITS[name]
  if (!value) return empty
  if (value.length < limits.min) return `Слишком короткое значение: не меньше ${limits.min} символов`
  if (value.length > limits.max) {
    return `Слишком длинный текст: не больше ${limits.max.toLocaleString('ru-RU')} символов`
  }
  return undefined
}

/** Значения проверяем уже обрезанными: backend обрезает пробелы сам. */
export function validateApplication(values: Record<FieldName, string>): FieldErrors {
  const errors: FieldErrors = {}
  const fullName = checkFullName(values.full_name.trim())
  if (fullName) errors.full_name = fullName

  const phone = checkPhone(values.phone.trim())
  if (phone) errors.phone = phone

  const company = checkText('company', values.company.trim(), 'Укажите название компании')
  if (company) errors.company = company

  const summary = checkText('summary', values.summary.trim(), 'Резюме не может быть пустым')
  if (summary) errors.summary = summary

  return errors
}
