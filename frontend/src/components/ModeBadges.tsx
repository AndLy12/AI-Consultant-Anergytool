/**
 * Плашки «тестовый режим» и «Демо-режим» рядом с «Energytool».
 *
 * В макете их нет, но они обязательны: пользователь должен видеть, что ответы —
 * заглушка или строятся на вымышленных документах. Показываются в колонке и,
 * на узком экране, в верхней полосе: колонка там спрятана за меню, а
 * предупреждение должно оставаться на виду.
 */

type Props = {
  stub: boolean
  demo: boolean
}

export default function ModeBadges({ stub, demo }: Props) {
  if (!stub && !demo) return null
  return (
    <span className="mode-badges">
      {stub ? (
        <span className="badge badge-test" title="Ядро консультанта ещё не подключено">
          тестовый режим
        </span>
      ) : null}
      {demo ? (
        <span
          className="badge badge-test badge-demo"
          title="Ядро работает на вымышленных документах и заглушке модели, это не настоящая консультация"
        >
          Демо-режим
        </span>
      ) : null}
    </span>
  )
}
