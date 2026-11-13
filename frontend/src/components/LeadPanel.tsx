/**
 * Сбор заявки для менеджера: предложение → форма → проверка карточки → создание.
 *
 * Состояния заявки живут во frontend: backend хранит только карточку и её статус
 * ('draft' или 'created'). Любое редактирование меняет version карточки, из-за чего
 * прежнее согласие становится недействительным — поэтому галочку согласия
 * сбрасываем после каждого сохранения и подтверждаем именно показанную версию.
 *
 * Поля проверяются до отправки по тем же ограничениям, что и в backend, а ответ 422
 * раскладывается обратно по полям: пользователь видит подсказку рядом с нужным
 * полем, а не текст валидатора. Введённое при ошибке не теряется.
 *
 * Отказ от заявки и незаполненная форма не мешают продолжать консультацию.
 * Реальной отправки в CRM или менеджеру нет: говорим «заявка создана», не обещая
 * звонка, срока и имени специалиста.
 */

import { useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { Application } from '../api/types'
import { describeError, fieldErrors as parseFieldErrors } from '../lib/errors'
import { formatDateTime } from '../lib/format'
import { FIELD_ORDER, validateApplication } from '../lib/validation'
import type { FieldErrors, FieldName } from '../lib/validation'

export type LeadStage = 'hidden' | 'offer' | 'form' | 'review' | 'created'

type Props = {
  dialogId: string
  stage: LeadStage
  card: Application | null
  /** Черновик резюме из ответа ядра либо запасной текст, собранный из реплик клиента. */
  summaryDraft: string
  onStage: (stage: LeadStage) => void
  onCard: (card: Application | null) => void
  onSessionExpired: () => void
}

type Fields = Record<FieldName, string>

const EMPTY: Fields = { full_name: '', phone: '', company: '', summary: '' }

export default function LeadPanel({
  dialogId,
  stage,
  card,
  summaryDraft,
  onStage,
  onCard,
  onSessionExpired,
}: Props) {
  // Начальные значения задаём до первого показа формы, а не поздним effect:
  // иначе пользователь успевает очистить резюме, после чего автозаполнение его возвращает.
  const [fields, setFields] = useState<Fields>(() => card
    ? { full_name: card.full_name, phone: card.phone, company: card.company, summary: card.summary }
    : { ...EMPTY, summary: summaryDraft })
  const edited = useRef(false)
  const [invalid, setInvalid] = useState<FieldErrors>({})
  const [consent, setConsent] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  // При открытии формы подставляем сохранённую карточку, иначе — черновик резюме.
  // Уже введённый текст не затираем: иначе правка после ошибки теряла бы данные.
  useEffect(() => {
    if (stage !== 'form') return
    setFields((current) => {
      // Явное редактирование, включая очистку единственного резюме, имеет приоритет.
      if (edited.current) return current
      if (current.full_name || current.phone || current.company) return current
      return card
        ? { full_name: card.full_name, phone: card.phone, company: card.company, summary: card.summary }
        : { ...EMPTY, summary: summaryDraft }
    })
  }, [stage, card, summaryDraft])

  function update(key: FieldName, value: string) {
    edited.current = true
    setFields((current) => ({ ...current, [key]: value }))
    // Подсказку убираем сразу, как только поле начали исправлять.
    setInvalid((current) => (current[key] ? { ...current, [key]: undefined } : current))
  }

  /** Фокус на первое поле с ошибкой: иначе её можно не заметить в длинной форме. */
  function focusFirstInvalid(errors: FieldErrors) {
    const name = FIELD_ORDER.find((field) => errors[field])
    if (name) document.getElementById(`lead-${name}`)?.focus()
  }

  function handleFailure(caught: unknown) {
    if (caught instanceof ApiError && caught.status === 401) {
      onSessionExpired()
      return
    }
    setError(describeError(caught))
  }

  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (busy) return

    const found = validateApplication(fields)
    if (Object.keys(found).length > 0) {
      setInvalid(found)
      setError('')
      focusFirstInvalid(found)
      return
    }

    setBusy(true)
    setError('')
    setInvalid({})
    try {
      const saved = await api.saveApplication(dialogId, {
        full_name: fields.full_name.trim(),
        phone: fields.phone.trim(),
        company: fields.company.trim(),
        summary: fields.summary.trim(),
      })
      onCard(saved)
      // Новая версия карточки: согласие нужно дать заново именно для неё.
      setConsent(false)
      onStage('review')
    } catch (caught) {
      // Если backend указал конкретные поля, показываем подсказки рядом с ними,
      // а форму оставляем заполненной.
      const fromServer = parseFieldErrors(caught)
      if (Object.keys(fromServer).length > 0) {
        setInvalid(fromServer)
        focusFirstInvalid(fromServer)
      } else {
        handleFailure(caught)
      }
    } finally {
      setBusy(false)
    }
  }

  async function confirm() {
    if (!card || busy || !consent) return
    setBusy(true)
    setError('')
    try {
      const created = await api.confirmApplication(dialogId, card.version)
      onCard(created)
      onStage('created')
    } catch (caught) {
      // 409 означает, что карточку изменили: показываем актуальную версию заново.
      if (caught instanceof ApiError && caught.status === 409) {
        try {
          const actual = await api.getApplication(dialogId)
          onCard(actual)
          setConsent(false)
          if (actual.status === 'created') {
            onStage('created')
            return
          }
        } catch {
          // Если карточку прочитать не удалось, остаётся общий текст ошибки ниже.
        }
      }
      handleFailure(caught)
    } finally {
      setBusy(false)
    }
  }

  if (stage === 'hidden') return null

  if (stage === 'offer') {
    return (
      <section className="lead lead-offer" aria-label="Предложение оставить заявку">
        <p className="lead-offer-text">
          Передать задачу менеджеру Energytool? Понадобятся ФИО, телефон и компания.
          Консультация останется доступной в любом случае.
        </p>
        <div className="lead-actions">
          <button type="button" className="button button-primary" onClick={() => onStage('form')}>
            Оставить заявку
          </button>
          <button type="button" className="button button-ghost" onClick={() => onStage('hidden')}>
            Продолжить консультацию
          </button>
        </div>
      </section>
    )
  }

  if (stage === 'form') {
    /** Общие атрибуты поля: красная рамка и связь подсказки с полем для скринридера. */
    const fieldProps = (name: FieldName) => ({
      id: `lead-${name}`,
      className: invalid[name] ? 'input-invalid' : undefined,
      'aria-invalid': invalid[name] ? true : undefined,
      'aria-describedby': invalid[name] ? `lead-${name}-error` : undefined,
      value: fields[name],
      onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
        update(name, event.target.value),
    })

    const hint = (name: FieldName) =>
      invalid[name] ? (
        <span className="field-error" id={`lead-${name}-error`} role="alert">
          {invalid[name]}
        </span>
      ) : null

    return (
      <section className="lead" aria-label="Форма заявки">
        <div className="lead-head">
          <h2 className="lead-title">Заявка менеджеру</h2>
          <button type="button" className="button button-ghost" onClick={() => onStage('hidden')}>
            Закрыть
          </button>
        </div>
        <p className="lead-note">
          Прототип хакатона: вводите только вымышленные данные, например
          Иванов Пётр Сергеевич, +7 900 000-00-01, ООО «Тестовая компания».
        </p>

        {/* noValidate: подсказки показываем своими словами, а не сообщениями браузера. */}
        <form className="form" onSubmit={save} noValidate>
          <label className="field">
            <span className="field-label">ФИО</span>
            <input {...fieldProps('full_name')} placeholder="Иванов Пётр Сергеевич" />
            {hint('full_name') ?? (
              <span className="field-hint">Отчество указывать не обязательно.</span>
            )}
          </label>
          <label className="field">
            <span className="field-label">Телефон</span>
            <input {...fieldProps('phone')} type="tel" placeholder="+7 900 000-00-01" />
            {hint('phone') ?? (
              <span className="field-hint">
                От 7 до 15 цифр, допустимы +, пробелы, скобки и дефисы.
              </span>
            )}
          </label>
          <label className="field">
            <span className="field-label">Компания</span>
            <input {...fieldProps('company')} placeholder="ООО «Тестовая компания»" />
            {hint('company')}
          </label>
          <label className="field">
            <span className="field-label">Резюме для менеджера</span>
            <textarea {...fieldProps('summary')} className={
              invalid.summary ? 'lead-summary input-invalid' : 'lead-summary'
            } maxLength={10000} rows={6} />
            {hint('summary') ?? (
              <span className="field-hint">
                Черновик можно отредактировать: менеджер увидит именно этот текст.
              </span>
            )}
          </label>

          {error ? <p className="form-error" role="alert">{error}</p> : null}

          <div className="lead-actions">
            <button type="submit" className="button button-primary" disabled={busy}>
              {busy ? 'Сохраняем…' : 'Проверить карточку'}
            </button>
            <button type="button" className="button button-ghost" onClick={() => onStage('hidden')}>
              Не сейчас
            </button>
          </div>
        </form>
      </section>
    )
  }

  if (stage === 'review' && card) {
    return (
      <section className="lead" aria-label="Проверка карточки заявки">
        <div className="lead-head">
          <h2 className="lead-title">Проверьте данные</h2>
          <span className="lead-version">версия карточки {card.version}</span>
        </div>

        <dl className="lead-card">
          <div><dt>ФИО</dt><dd>{card.full_name}</dd></div>
          <div><dt>Телефон</dt><dd>{card.phone}</dd></div>
          <div><dt>Компания</dt><dd>{card.company}</dd></div>
          <div><dt>Резюме</dt><dd className="lead-card-summary">{card.summary}</dd></div>
        </dl>

        <label className="consent">
          <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
          <span>
            Подтверждаю показанные данные и создание заявки. Это прототип, данные
            вымышленные и используются только для проверки.
          </span>
        </label>

        {error ? <p className="form-error" role="alert">{error}</p> : null}

        <div className="lead-actions">
          <button type="button" className="button button-primary" onClick={confirm} disabled={busy || !consent}>
            {busy ? 'Создаём…' : 'Подтвердить заявку'}
          </button>
          <button type="button" className="button button-ghost" onClick={() => onStage('form')}>
            Изменить
          </button>
          <button type="button" className="button button-ghost" onClick={() => onStage('hidden')}>
            Не сейчас
          </button>
        </div>
      </section>
    )
  }

  if (stage === 'created' && card) {
    return (
      <section className="lead lead-done" aria-label="Заявка создана">
        <h2 className="lead-title">Заявка создана</h2>
        <p className="lead-done-text">
          Карточка сохранена в системе. Номер заявки:{' '}
          <span className="lead-id">{card.id}</span>
        </p>
        <p className="lead-note">
          Создана {card.consent_at ? formatDateTime(card.consent_at) : formatDateTime(card.created_at)}.
          Консультация продолжается — можно задавать вопросы дальше.
        </p>
        <dl className="lead-card">
          <div><dt>ФИО</dt><dd>{card.full_name}</dd></div>
          <div><dt>Телефон</dt><dd>{card.phone}</dd></div>
          <div><dt>Компания</dt><dd>{card.company}</dd></div>
          <div><dt>Резюме</dt><dd className="lead-card-summary">{card.summary}</dd></div>
        </dl>
        <div className="lead-actions">
          <button type="button" className="button button-ghost" onClick={() => onStage('hidden')}>
            Свернуть
          </button>
        </div>
      </section>
    )
  }

  return null
}
