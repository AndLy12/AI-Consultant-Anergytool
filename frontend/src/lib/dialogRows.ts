/**
 * Список диалогов пользователя с первой репликой — для истории в колонке и экрана истории.
 *
 * Backend в GET /api/dialogs не отдаёт ни названия, ни превью, поэтому первую реплику
 * и число сообщений дочитываем по GET /api/dialogs/{id}. Это лишние запросы: их число
 * ограничено параметром limit, а поле preview запрошено у роли №1. Ошибка чтения одного
 * диалога не ломает список — такая строка показывается без превью.
 */

import { api } from '../api/client'
import type { Dialog } from '../api/types'

export type DialogRow = {
  dialog: Dialog
  /** Первая реплика пользователя; пустая строка — сообщений нет или прочитать не удалось. */
  preview: string
  count: number
}

/** Новые диалоги сверху; превью дочитываем только для первых `limit`. */
export async function loadDialogRows(limit: number): Promise<DialogRow[]> {
  const dialogs = await api.listDialogs()
  // Backend отдаёт список от старых к новым.
  const ordered = [...dialogs].reverse()
  const detailed = await Promise.all(
    ordered.slice(0, limit).map(async (dialog) => {
      try {
        const full = await api.getDialog(dialog.id)
        const first = full.messages.find((message) => message.role === 'user')
        return { dialog, preview: first?.content ?? '', count: full.messages.length }
      } catch {
        return { dialog, preview: '', count: 0 }
      }
    }),
  )
  const rest = ordered.slice(limit).map((dialog) => ({ dialog, preview: '', count: 0 }))
  return [...detailed, ...rest]
}
