/**
 * Стартовый экран пустого диалога.
 *
 * Подсказки — типовые задачи клиентов Energytool. Это только формулировки запроса:
 * они не обещают результат и не содержат продуктовых фактов, факты приходят из базы.
 */

const SUGGESTIONS = [
  'Хотим снизить счета за электричество на производстве',
  'Нужно автоматически снимать показания со счётчиков',
  'Подозреваем коммерческие потери — как их найти?',
  'Думаем о солнечной станции: с чего начать?',
  'Нужен резерв питания на случай отключений',
  'Хотим разобраться, подходит ли нам оптовый рынок',
]

type Props = {
  onPick: (text: string) => void
  disabled: boolean
}

export default function StartSuggestions({ onPick, disabled }: Props) {
  return (
    <div className="start">
      <h1 className="start-title">Чем помочь?</h1>
      <p className="start-lead">
        Опишите задачу своими словами. Консультант уточнит детали, найдёт ответ
        в базе знаний Energytool и подскажет следующий шаг.
      </p>
      <div className="start-grid">
        {SUGGESTIONS.map((text) => (
          <button
            key={text}
            type="button"
            className="suggestion"
            disabled={disabled}
            onClick={() => onPick(text)}
          >
            {text}
          </button>
        ))}
      </div>
    </div>
  )
}
