import { test, expect } from '@playwright/test'
test('demo live frames, stored comparison over distance and mobile layout', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto('/')
  await expect(page.getByTestId('backend-status')).toHaveText(/BACKEND VERBUNDEN/)
  await expect(page.getByTestId('demo-tag')).toBeVisible()
  await expect(page.getByTestId('telemetry-status')).toHaveText(/DEMO-TELEMETRIE/)
  const time = page.getByTestId('kpi-current').locator('strong')
  await expect(time).not.toHaveText('—:——.———')
  const prior = await time.textContent()
  await expect(time).not.toHaveText(prior || '')
  for (const corner of ['FL', 'FR', 'RL', 'RR']) await expect(page.getByTestId('tyre-' + corner)).toBeVisible()
  await page.getByRole('button', { name: 'Analyse', exact: true }).click()
  await expect(page.getByTestId('analysis-summary')).toBeVisible({ timeout: 20000 })
  await expect(page.getByTestId('sector-compare')).toBeVisible()
  await expect(page.getByTestId('driving-strip')).toHaveCount(0)
  await page.setViewportSize({ width: 390, height: 844 })
  for (const name of ['Live-Dashboard', 'Analyse', 'Onboard', 'Sessions', 'Einstellungen']) {
    await page.getByRole('button', { name, exact: true }).click()
    await page.waitForTimeout(600)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  }
  expect(errors).toEqual([])
})

test('live dashboard remains usable at 1080p, 1440p and ultrawide', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('demo-tag')).toBeVisible()
  for (const [width, height] of [
    [1920, 1080],
    [2560, 1440],
    [3440, 1440],
  ]) {
    await page.setViewportSize({ width, height })
    await expect(page.locator('.live-map')).toBeVisible()
    await expect(page.locator('.live-sectors')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  }
})
test('session archive selects real recorded rows and exports CSV', async ({ page }) => {
  await page.goto('/#/sessions')
  const card = page.locator('.session-item').filter({ hasText: 'Engineering Circuit' }).first()
  await expect(card).toBeVisible({ timeout: 15000 })
  await card.locator('input[type=checkbox]').check()
  await expect(page.locator('.lap-list tbody tr')).not.toHaveCount(0)
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.locator('.lap-list button[title="CSV exportieren"]').first().click(),
  ])
  expect(download.suggestedFilename()).toMatch(/lap-\d+\.csv/)
})
test('a stored session replays measured values in Onboard and advances to its next lap', async ({ page }) => {
  await page.goto('/#/sessions')
  const card = page.locator('.session-item').filter({ hasText: 'Engineering Circuit' }).first()
  await expect(card).toBeVisible()
  await card.locator('input[type=checkbox]').check()
  await page.getByRole('button', { name: 'Session im Onboard wiedergeben' }).click()
  await expect(page.getByTestId('replay-controls')).toContainText('WIEDERGABE')
  const slider = page.getByRole('slider', { name: 'Wiedergabezeit' })
  await expect.poll(async () => Number(await slider.inputValue())).toBeGreaterThan(0)
  const firstDuration = Number(await slider.getAttribute('max'))
  await slider.evaluate((el) => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setter.call(el, (el as HTMLInputElement).max)
    el.dispatchEvent(new Event('input', { bubbles: true }))
    el.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await expect.poll(async () => Number(await slider.getAttribute('max'))).toBeGreaterThan(firstDuration)
  await expect(page.locator('.onboard-empty')).toContainText('Telemetrie läuft unabhängig vom Video')
  await page.getByRole('button', { name: 'Wiedergabe beenden' }).click()
  await expect(slider).toHaveCount(0)
})
