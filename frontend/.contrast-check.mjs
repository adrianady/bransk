import puppeteer from 'puppeteer'

const browser = await puppeteer.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const page = await browser.newPage()
await page.setViewport({ width: 1440, height: 900 })
await page.goto('http://127.0.0.1:8000', { waitUntil: 'domcontentloaded' })
await page.waitForFunction(() => document.body.innerText.includes('Structură'), { timeout: 25000 })
await page.evaluate(() => { window.location.hash = 'studies' })
await page.waitForFunction(() => document.querySelector('main h2')?.textContent?.includes('Studii'), { timeout: 20000 })
await new Promise((r) => setTimeout(r, 1500))
const data = await page.evaluate(() => {
  const parse = (c) => {
    const m = c.match(/rgba?\(([^)]+)\)/)
    return m ? m[1].split(',').map(Number) : [0, 0, 0]
  }
  const lum = ([r, g, b]) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
  }
  const ratio = (a, b) => {
    const la = lum(parse(a)), lb = lum(parse(b))
    return +((Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)).toFixed(2)
  }
  const out = {}
  for (const cls of ['badge-brass', 'badge-verdigris', 'badge-madder', 'badge-accent', 'badge-muted']) {
    const el = document.querySelector('.' + cls)
    if (!el) { out[cls] = 'not present'; continue }
    const cs = getComputedStyle(el)
    out[cls] = { text: cs.color, bg: cs.backgroundColor, ratio: ratio(cs.color, cs.backgroundColor) }
  }
  const muted = [...document.querySelectorAll('.text-muted')].find((n) => n.clientWidth > 0)
  if (muted) {
    const cs = getComputedStyle(muted)
    let p = muted.parentElement
    let bg = 'rgb(255, 255, 255)'
    while (p) { const s = getComputedStyle(p); if (s.backgroundColor !== 'rgba(0, 0, 0, 0)') { bg = s.backgroundColor; break } p = p.parentElement }
    out.textMuted = { text: cs.color, bg, ratio: ratio(cs.color, bg) }
  }
  return out
})
console.log(JSON.stringify(data, null, 2))
await browser.close()
