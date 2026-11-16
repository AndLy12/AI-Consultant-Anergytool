/**
 * Стартовый экран пустого диалога по макету Figma (кадр 9:2).
 *
 * Приветствие зависит от времени суток (lib/greeting), имени нет: backend его не хранит.
 * Подсказок в макете нет; по решению владельца (2026-11-16) они остаются под
 * приветствием в стиле макета — это типовые задачи клиентов Energytool из плана кейса.
 * Подсказки — только формулировки запроса: они не обещают результат и не содержат
 * продуктовых фактов, факты приходят из базы.
 */

import { greetingFor } from '../lib/greeting'

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
      <h1 className="start-title">{greetingFor(new Date())} Как я могу Вам помочь?</h1>
      <div className="start-grid" aria-label="Типовые задачи">
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
