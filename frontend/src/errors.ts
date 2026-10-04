/**
 * Understandable messages with an action for the UI; the technical detail is
 * kept in a small in-browser log shown under Settings → Advanced / Diagnostics.
 */

export interface LoggedError {
  at: number
  context: string
  detail: string
}
const LOG: LoggedError[] = []
const listeners = new Set<() => void>()

export function errorLog(): LoggedError[] {
  return [...LOG]
}
export function onErrorLog(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function logError(error: unknown, context = '') {
  LOG.unshift({ at: Date.now(), context, detail: String(error).slice(0, 500) })
  LOG.splice(30)
  listeners.forEach((l) => l())
}

/** Short message + suggested action; never a raw "TypeError: Failed to fetch". */
export function friendlyError(error: unknown, de: boolean): string {
  const text = String(error)
  const t = (a: string, b: string) => (de ? a : b)
  if (/Failed to fetch|NetworkError|Load failed|ERR_CONNECTION|network/i.test(text))
    return t(
      'Keine Verbindung zum PC-Programm. Läuft die App am PC? Die Seite verbindet sich automatisch neu.',
      'No connection to the PC app. Is it running? The page reconnects automatically.',
    )
  if (/Too many wrong/i.test(text))
    return t('Zu viele falsche Zugriffscodes. Bitte 60 Sekunden warten.', 'Too many wrong access codes. Please wait 60 seconds.')
  if (/Access code required/i.test(text))
    return t('Zugriffscode erforderlich – bitte den Code vom PC eingeben.', 'Access code required – enter the code shown on the PC.')
  if (/same source, car, track and layout|Demo and measured laps/i.test(text))
    return t(
      'Diese Runden sind nicht vergleichbar (anderes Fahrzeug, andere Strecke, anderes Layout oder Demo/echt gemischt).',
      'These laps are not comparable (different car, track, layout or demo/real mixed).',
    )
  if (/Track spline length is not available/i.test(text))
    return t(
      'Für diese Strecke fehlt die Streckenlänge – ein Vergleich über die Distanz ist nicht möglich.',
      'Track length is missing – a distance-based comparison is not possible.',
    )
  if (/HTTP 404|not found/i.test(text))
    return t('Die Daten wurden nicht gefunden (evtl. gelöscht). Liste aktualisieren.', 'Data not found (maybe deleted). Refresh the list.')
  if (/HTTP 413|limited to/i.test(text))
    return t('Die Datei ist zu groß für den Import.', 'The file is too large to import.')
  if (/HTTP 422|Invalid/i.test(text))
    return t('Die Eingabe wurde nicht akzeptiert. Bitte Werte prüfen.', 'The input was rejected. Please check the values.')
  return t(
    'Aktion fehlgeschlagen. Details unter Einstellungen → Erweitert / Diagnose.',
    'Action failed. Details under Settings → Advanced / Diagnostics.',
  )
}
