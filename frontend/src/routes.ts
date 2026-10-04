/** Hash routes. Exactly five main areas; old links keep working via aliases. */

export type Page = 'live' | 'analysis' | 'onboard' | 'sessions' | 'settings'
export type SettingsTab = 'general' | 'data' | 'onboard' | 'network' | 'advanced'
export interface Route {
  page: Page
  tab?: SettingsTab
  /** Sessions: lap whose onboard video is played (#/sessions/video/<lapId>). */
  video?: string
}

export const PAGES: Page[] = ['live', 'analysis', 'onboard', 'sessions', 'settings']
export const SETTINGS_TABS: SettingsTab[] = ['general', 'data', 'onboard', 'network', 'advanced']

const ALIASES: Record<string, Route> = {
  '': { page: 'live' },
  live: { page: 'live' },
  dashboard: { page: 'live' },
  overview: { page: 'live' },
  // Former separate views, merged into the live dashboard.
  engineering: { page: 'live' },
  'live-mode': { page: 'live' },
  livemode: { page: 'live' },
  tablet: { page: 'live' },
  telemetry: { page: 'live' },
  trackmap: { page: 'live' },
  analysis: { page: 'analysis' },
  analyse: { page: 'analysis' },
  comparison: { page: 'analysis' },
  onboard: { page: 'onboard' },
  sessions: { page: 'sessions' },
  settings: { page: 'settings', tab: 'general' },
  // Diagnostics now lives under Settings → Advanced / Diagnostics.
  diagnostics: { page: 'settings', tab: 'advanced' },
  diagnose: { page: 'settings', tab: 'advanced' },
}

export function parseRoute(hash: string): Route {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean)
  const head = (parts[0] || '').toLowerCase()
  const base = ALIASES[head] || { page: 'live' }
  if (base.page === 'settings') {
    const tab = (parts[1] || '').toLowerCase()
    if (head === 'settings' && (SETTINGS_TABS as string[]).includes(tab))
      return { page: 'settings', tab: tab as SettingsTab }
    if (head === 'settings' && (tab === 'diagnostics' || tab === 'diagnose'))
      return { page: 'settings', tab: 'advanced' }
    return { page: 'settings', tab: base.tab || 'general' }
  }
  if (head === 'sessions' && parts[1] === 'video' && parts[2] && /^[\w-]{1,64}$/.test(parts[2]))
    return { page: 'sessions', video: parts[2] }
  return { page: base.page }
}

export function routeHash(route: Route): string {
  if (route.page === 'settings') return `#/settings/${route.tab || 'general'}`
  if (route.page === 'sessions' && route.video) return `#/sessions/video/${route.video}`
  return `#/${route.page}`
}
