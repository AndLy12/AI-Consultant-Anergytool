/**
 * Окно заявки для менеджера по макету Figma (кадры 20:823 и 22:1028).
 *
 * Всплывающее окно поверх чата: фон затемнён и размыт, нажатие по фону ничего
 * не делает, Esc и крестик закрывают окно (если в форме что-то введено — сначала
 * спрашиваем). Четыре состояния: форма → проверка (данные для чтения и галочка
 * согласия) → согласие (галочка стоит, «Подтвердить» активна) → «Заявка создана».
 * Экрана проверки и «Заявка создана» в макете нет — они собраны из его элементов.
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
import { createPortal } from 'react-dom'
import { ApiError, api } from '../api/client'
import type { Application } from '../api/types'
import closeIcon from '../assets/icons/close-cross.svg'
import { describeError, fieldErrors as parseFieldErrors } from '../lib/errors'
import { formatDateTime } from '../lib/format'
import { FIELD_ORDER, sanitizePhoneInput, validateApplication } from '../lib/validation'
import type { FieldErrors, FieldName } from '../lib/validation'
import ConfirmModal from './ConfirmModal'

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

/** Подписи над полями и примеры внутри полей — тексты макета 20:823 (решения владельца §8). */
const FIELDS: { name: FieldName; label: string; placeholder: string }[] = [
  { name: 'full_name', label: 'ФИО', placeholder: 'Иван Иванович Иванов' },
  { name: 'phone', label: 'Номер телефона', placeholder: '+7' },
  { name: 'company', label: 'Компания', placeholder: 'ООО “Пример”' },
  { name: 'summary', label: 'Резюме диалога', placeholder: 'Кратко опишите задачу' },
]

/** Что подставить при открытии формы; null — подставлять пока нечего. */
function startFields(card: Application | null, summaryDraft: string): Fields | null {
  if (card) {
    return { full_name: card.full_name, phone: card.phone, company: card.company, summary: card.summary }
  }
  return summaryDraft ? { ...EMPTY, summary: summaryDraft } : null
}

const TITLE: Record<'form' | 'review' | 'created', string> = {
  form: 'Форма заявки',
  review: 'Проверьте данные',
  created: 'Заявка создана',
}

/** Подпись окна для диктора и тестов (по ней ищется открытая форма). */
const DIALOG_LABEL: Record<'form' | 'review' | 'created', string> = {
  form: 'Форма заявки',
  review: 'Проверка карточки заявки',
  created: 'Заявка создана',
}

export default function LeadPanel({
  dialogId,
  stage,
  card,
  summaryDraft,
  onStage,
  onCard,
  onSessionExpired,
}: Props) {
  const [fields, setFields] = useState<Fields>(EMPTY)
  /** Значения на момент открытия формы: по ним понимаем, вводил ли человек что-то. */
  const [initial, setInitial] = useState<Fields>(EMPTY)
  // Автозаполнение формы срабатывает один раз за открытие: 'waiting' — данных
  // для подстановки ещё нет, 'done' — поле больше не трогаем.
  const [prefill, setPrefill] = useState<'waiting' | 'done'>('waiting')
  const [formShown, setFormShown] = useState(false)
  const [invalid, setInvalid] = useState<FieldErrors>({})
  const [consent, setConsent] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [askClose, setAskClose] = useState(false)
  const dialogRef = useRef<HTMLElement>(null)

  // Подстановку делаем прямо при отрисовке, а не в effect: так форма никогда
  // не показывается со старыми значениями и не перезаписывается «задним числом».
  //
  // Почему не просто «при каждом изменении summaryDraft, пока поле не правили»:
  // форму можно открыть до ответа консультанта, когда черновик ещё пуст.
  // Очистка уже пустого поля не вызывает ни одного события, и такую «правку»
  // нельзя отличить от бездействия. Поэтому после отправки формы или любой
  // правки автозаполнение прекращается, и поздний черновик поле не трогает.
  const isForm = stage === 'form'
  if (isForm !== formShown) {
    setFormShown(isForm)
    if (isForm) {
      // Новое открытие формы: сохранённая карточка, иначе — черновик резюме, если он уже есть.
      const start = startFields(card, summaryDraft)
      setFields(start ?? EMPTY)
      setInitial(start ?? EMPTY)
      setPrefill(start ? 'done' : 'waiting')
    }
  } else if (isForm && prefill === 'waiting') {
    // Форму открыли раньше, чем появился черновик, и её ещё не трогали: подставляем один раз.
    const start = startFields(card, summaryDraft)
    if (start) {
      setFields(start)
      setInitial(start)
      setPrefill('done')
    }
  }

  const visible = stage === 'form' || stage === 'review' || stage === 'created'
  // Подтверждение при закрытии нужно только для формы с введёнными данными:
  // на проверке и после создания карточка уже сохранена в backend.
  const dirty = isForm && FIELD_ORDER.some((name) => fields[name] !== initial[name])

  function requestClose() {
    if (busy) return
    if (dirty) setAskClose(true)
    else onStage('hidden')
  }

  // Esc закрывает окно; пока открыт вопрос о закрытии, Esc обрабатывает он сам.
  // Tab не выходит за пределы окна: фон за окном недоступен ни мышью, ни клавиатурой.
  const requestCloseRef = useRef(requestClose)
  requestCloseRef.current = requestClose
  useEffect(() => {
    if (!visible || askClose) return
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        requestCloseRef.current()
        return
      }
      if (event.key !== 'Tab' || !dialogRef.current) return
      const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
      )
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      } else if (!dialogRef.current.contains(document.activeElement)) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [visible, askClose])

  // При смене состояния фокус переходит в окно: на первое поле формы или на заголовок.
  // Прокрутка тоже начинается сверху: иначе после согласия (внизу окна) номер
  // созданной заявки оказался бы за верхним краем.
  useEffect(() => {
    if (!visible) return
    dialogRef.current?.querySelector('.lead-body')?.scrollTo(0, 0)
    const target = stage === 'form'
      ? document.getElementById('lead-full_name')
      : dialogRef.current?.querySelector<HTMLElement>('.lead-heading')
    target?.focus()
  }, [visible, stage])

  function update(key: FieldName, value: string) {
    setPrefill('done')
    const next = key === 'phone' ? sanitizePhoneInput(value) : value
    setFields((current) => ({ ...current, [key]: next }))
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
    // После отправки поле принадлежит пользователю: поздний черновик его не заменит.
    setPrefill('done')

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

  if (!visible) return null
  if (stage !== 'form' && !card) return null
  const shown = stage as 'form' | 'review' | 'created'

  /** Данные карточки для чтения: на проверке и после создания. */
  const readonly = card ? (
    <dl className="lead-card">
      {FIELDS.map(({ name, label }) => (
        <div key={name} className="lead-card-row">
          <dt className="lead-label">{label}</dt>
          <dd className={name === 'summary' ? 'lead-value lead-card-summary' : 'lead-value'}>{card[name]}</dd>
        </div>
      ))}
    </dl>
  ) : null

  let body: React.ReactNode = null

  if (stage === 'form') {
    body = (
      // noValidate: подсказки показываем своими словами, а не сообщениями браузера.
      <form className="lead-form" onSubmit={save} noValidate>
        {FIELDS.map(({ name, label, placeholder }) => {
          const common = {
            id: `lead-${name}`,
            className: `lead-input${name === 'summary' ? ' lead-summary' : ''}${invalid[name] ? ' input-invalid' : ''}`,
            placeholder,
            value: fields[name],
            'aria-invalid': invalid[name] ? true : undefined,
            'aria-describedby': invalid[name] ? `lead-${name}-error` : undefined,
            onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
              update(name, event.target.value),
          }
          return (
            <div key={name} className="lead-field">
              <label className="lead-label" htmlFor={`lead-${name}`}>{label}</label>
              {name === 'summary' ? (
                <textarea {...common} maxLength={10000} rows={1} />
              ) : (
                <input {...common} type={name === 'phone' ? 'tel' : 'text'} inputMode={name === 'phone' ? 'tel' : undefined} />
              )}
              {invalid[name] ? (
                <span className="lead-error" id={`lead-${name}-error`} role="alert">
                  {invalid[name]}
                </span>
              ) : null}
            </div>
          )
        })}

        {error ? <p className="form-error" role="alert">{error}</p> : null}

        <button type="submit" className="lead-button lead-button-primary" disabled={busy}>
          {busy ? 'Сохраняем…' : 'Проверить карточку'}
        </button>
      </form>
    )
  } else if (stage === 'review' && card) {
    body = (
      <>
        {readonly}

        {/* Настоящий флажок, оформленный квадратом 57 × 57 по макету 22:1099. */}
        <label className="consent">
          <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
          <span className="consent-text">
            Согласен на обработку указанных данных для оформления заявки
            <span className="consent-note">Прототип: используются только вымышленные тестовые данные.</span>
          </span>
        </label>

        {error ? <p className="form-error" role="alert">{error}</p> : null}

        <div className="lead-actions">
          <button type="button" className="lead-button lead-button-outline" onClick={() => onStage('form')} disabled={busy}>
            Изменить
          </button>
          {/* Видимая надпись «Подтвердить», для диктора — «Подтвердить заявку». */}
          <button
            type="button"
            className="lead-button lead-button-primary"
            aria-label="Подтвердить заявку"
            onClick={confirm}
            disabled={busy || !consent}
          >
            {busy ? 'Создаём…' : 'Подтвердить'}
          </button>
        </div>
      </>
    )
  } else if (stage === 'created' && card) {
    body = (
      <>
        <p className="lead-done-text">
          Номер заявки: <span className="lead-id">{card.id}</span>
        </p>
        <p className="lead-note">
          Создана {card.consent_at ? formatDateTime(card.consent_at) : formatDateTime(card.created_at)}.
          Консультация продолжается — можно задавать вопросы дальше.
        </p>
        {readonly}
        <div className="lead-actions">
          <button type="button" className="lead-button lead-button-primary" onClick={() => onStage('hidden')}>
            Свернуть
          </button>
        </div>
      </>
    )
  }

  return createPortal(
    <div className="lead-overlay">
      <section
        ref={dialogRef}
        className={stage === 'created' ? 'lead lead-done' : 'lead'}
        role="dialog"
        aria-modal="true"
        aria-label={DIALOG_LABEL[shown]}
      >
        <header className="lead-head">
          <h2 className="lead-heading" tabIndex={-1}>{TITLE[shown]}</h2>
          <button type="button" className="lead-close" aria-label="Закрыть" onClick={requestClose}>
            <img src={closeIcon} alt="" width={35} height={35} />
          </button>
        </header>
        <div className="lead-body">{body}</div>
      </section>

      {askClose ? (
        <ConfirmModal
          title="Закрыть форму?"
          text="Введённые данные не сохранятся. Консультацию можно продолжить, заявку — оставить позже."
          confirmLabel="Закрыть без сохранения"
          onConfirm={() => {
            setAskClose(false)
            onStage('hidden')
          }}
          onCancel={() => setAskClose(false)}
        />
      ) : null}
    </div>,
    document.body,
  )
}
