import { expect, test } from '@playwright/test'

// The real browser SDK, given a DSN the way the server gives it, sending to a stand-in.
test('browser error reporting sends a scrubbed event', async ({ page }) => {
  const sent: string[] = []
  await page.route('https://o1.ingest.example/**', async (route) => {
    sent.push(route.request().postData() ?? '')
    await route.fulfill({ status: 200, body: '{}' })
  })
  const meta = `<meta name="novel-engine-client" content='{"sentryDsn":"https://key@o1.ingest.example/2","environment":"production","release":"novel-engine@abc1234"}' />`
  await page.route('**/*', async (route) => {
    const request = route.request()
    if (request.resourceType() !== 'document') return route.fallback()
    const response = await route.fetch()
    await route.fulfill({ response, body: (await response.text()).replace('</head>', `${meta}</head>`) })
  })

  await page.goto('/?secret=PRIVATE-QUERY#PRIVATE-FRAGMENT')
  await page.getByRole('link', { name: 'Sign in' }).first().click()
  await page.getByRole('link', { name: /Your project/ }).click()
  // Works whatever the project already holds: other tests share this backend.
  await page.getByRole('button', { name: 'New entry' }).first().click()
  await page.getByRole('button', { name: 'Fill it in by hand' }).click()
  await page.getByLabel('Name', { exact: true }).fill('PRIVATE-TYPED-NAME')
  await page.evaluate(() => console.log('PRIVATE-CONSOLE-LINE'))
  await page.evaluate(() => {
    setTimeout(() => {
      throw new Error('probe failure')
    }, 0)
  })
  await expect.poll(() => sent.length, { timeout: 15_000 }).toBeGreaterThan(0)

  const text = sent.join('\n')
  expect(text).toContain('probe failure')
  expect(text).toContain('novel-engine@abc1234')
  for (const leaked of ['PRIVATE-QUERY', 'PRIVATE-FRAGMENT', 'PRIVATE-TYPED-NAME', 'PRIVATE-CONSOLE-LINE', 'breadcrumbs', 'novel_session', 'novel_csrf']) {
    expect(text, leaked).not.toContain(leaked)
  }
})
