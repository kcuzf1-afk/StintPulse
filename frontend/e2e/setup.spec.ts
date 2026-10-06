/**
 * Setup assistant in a real browser: base setup → laps → feedback →
 * rule-based analysis (no AI call) → export → save into the TEMPORARY AC
 * setups folder created by scripts/browser_tests.py (never the real one).
 */
import { test, expect } from '@playwright/test'
import { readFileSync, existsSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

const setupsDir = process.env.AC_AGENT_TEST_SETUPS || ''
const trackDir = join(setupsDir, 'Prototype R', 'Engineering Circuit')

test('a setup is analysed rule-based, exported as a new file and saved without touching the base', async ({ page }) => {
  test.skip(!setupsDir, 'needs the temporary AC folders of scripts/browser_tests.py')
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  const base = readFileSync(join(trackDir, 'base.ini'))

  await page.goto('/#/analysis/setup')
  await expect(page.getByRole('tab', { name: 'Setup' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByTestId('setup-spec')).toContainText('Einstellgrenzen aus den Fahrzeugdaten')
  await expect(page.getByText(/KI ist ausgeschaltet/)).toBeVisible()

  await page.getByLabel('Ausgangssetup').selectOption('Engineering Circuit/base.ini')
  await expect(page.getByTestId('base-info')).toContainText('9 von 13 Parametern exportierbar')
  await expect(page.getByTestId('base-info')).toContainText('1 interne Einträge')
  await expect(page.locator('.setup-lap input:checked').first()).toBeChecked({ timeout: 15000 })

  await page.getByRole('button', { name: 'Hinzufügen' }).click()
  await expect(page.locator('.setup-feedback li')).toContainText('Untersteuern · Kurveneingang')

  // AI is off: only the labelled rule-based analysis is possible.
  await expect(page.getByTestId('analyze-ai')).toBeDisabled()
  await page.getByRole('button', { name: 'Daten-Vorschau' }).click()
  const preview = page.getByTestId('setup-preview')
  await expect(preview).toContainText('"setup_parameters"')
  await expect(preview).not.toContainText('Demo Driver')
  await page.getByTestId('analyze-rules').click()
  const result = page.getByTestId('setup-result')
  await expect(result.getByText('REGELBASIERT · KEINE KI')).toBeVisible({ timeout: 20000 })
  const card = page.getByTestId('rec-ARB_FRONT')
  await expect(card).toContainText('40.000 → 37.500')
  await expect(card).toContainText('[ARB_FRONT] VALUE=8 → VALUE=7')
  await expect(page.getByTestId('setup-before-after')).toContainText('VALUE=8 → 7')

  // Export: a NEW file, only the chosen VALUE line differs from the base.
  const download = page.waitForEvent('download')
  await page.getByTestId('setup-export').click()
  const file = await download
  expect(file.suggestedFilename()).toBe('base stintpulse.ini')
  const exported = readFileSync(await file.path())
  expect(exported.toString('latin1')).toBe(base.toString('latin1').replace('[ARB_FRONT]\r\nVALUE=8', '[ARB_FRONT]\r\nVALUE=7'))
  await expect(page.getByTestId('setup-exported')).toContainText('gespeichert und heruntergeladen')

  // Optional explicit save into the (temporary) AC setups folder.
  await page.getByTestId('setup-exported').getByRole('button', { name: 'Speichern' }).click()
  await expect(page.getByTestId('setup-exported')).toContainText('Gespeichert:')
  expect(readFileSync(join(trackDir, 'base stintpulse.ini')).equals(exported)).toBe(true)
  expect(readFileSync(join(trackDir, 'base.ini')).equals(base)).toBe(true)
  expect(readdirSync(trackDir).sort()).toEqual(['base stintpulse.ini', 'base.ini'])
  expect(existsSync(join(setupsDir, 'Prototype R', 'generic'))).toBe(false)

  // The version list knows base and export.
  const versions = page.getByTestId('setup-versions')
  await expect(versions).toContainText('Ausgang (Engineering Circuit/base.ini)')
  await expect(versions).toContainText('ARB Front: 40.000 → 37.500')
  expect(errors).toEqual([])
})
