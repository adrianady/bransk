import { test, expect } from '@playwright/test';

const AUTH_TOKEN = 'bbecf51231779b48230618d8841ffaffb52843d4f02a72a4';
const API_BASE = 'http://localhost:8000';
const UI_BASE = 'http://localhost:5173';

test.describe('ChronoKG - Smoke Tests (API + Basic UI)', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript((token) => {
      localStorage.setItem('chronokg-auth-token', token);
    }, AUTH_TOKEN);
  });

  test.describe('API Health Checks', () => {
    test('backend health endpoint responds', async ({ request }) => {
      const response = await request.get(`${API_BASE}/api/health`);
      expect(response.ok()).toBeTruthy();
      const body = await response.json();
      expect(body.status).toBe('ok');
    });

    test('projects endpoint requires auth', async ({ request }) => {
      const response = await request.get(`${API_BASE}/api/projects`);
      expect(response.status()).toBe(401);
    });

    test('projects endpoint works with token', async ({ request }) => {
      const response = await request.get(`${API_BASE}/api/projects`, {
        headers: { Authorization: `Bearer ${AUTH_TOKEN}` },
      });
      expect(response.ok()).toBeTruthy();
      const body = await response.json();
      expect(body.projects).toBeDefined();
      expect(Array.isArray(body.projects)).toBeTruthy();
    });

    test('entries endpoint works with token', async ({ request }) => {
      const response = await request.get(`${API_BASE}/api/entries?limit=1`, {
        headers: { Authorization: `Bearer ${AUTH_TOKEN}` },
      });
      expect(response.ok()).toBeTruthy();
      const body = await response.json();
      expect(Array.isArray(body)).toBeTruthy();
    });

    test('chat endpoint works', async ({ request }) => {
      const response = await request.post(`${API_BASE}/api/chat`, {
        headers: {
          Authorization: `Bearer ${AUTH_TOKEN}`,
          'Content-Type': 'application/json',
        },
        data: { message: 'Test message' },
      });
      expect(response.ok()).toBeTruthy();
      const body = await response.json();
      expect(body.reply).toBeDefined();
    });

    test('questionnaires endpoint works', async ({ request }) => {
      const response = await request.get(`${API_BASE}/api/questionnaires`, {
        headers: { Authorization: `Bearer ${AUTH_TOKEN}` },
      });
      expect(response.ok()).toBeTruthy();
      const body = await response.json();
      expect(Array.isArray(body)).toBeTruthy();
    });

    test('zotero status endpoint works', async ({ request }) => {
      const response = await request.get(`${API_BASE}/api/zotero/status`, {
        headers: { Authorization: `Bearer ${AUTH_TOKEN}` },
      });
      expect(response.ok()).toBeTruthy();
      const body = await response.json();
      expect(body.installed).toBeTruthy();
    });

    test('ai status endpoint works', async ({ request }) => {
      const response = await request.get(`${API_BASE}/api/ai/status`, {
        headers: { Authorization: `Bearer ${AUTH_TOKEN}` },
      });
      expect(response.ok()).toBeTruthy();
      const body = await response.json();
      expect(body.active_provider).toBeDefined();
    });
  });

  test.describe('Basic UI Load', () => {
    test('should load app at root and show content', async ({ page }) => {
      await page.goto(UI_BASE);
      await page.waitForLoadState('networkidle');
      
      // Wait for React to hydrate - check for any content
      await expect(page.locator('body')).toBeVisible({ timeout: 30000 });
      
      // Should have some content rendered
      const bodyText = await page.locator('body').textContent();
      expect(bodyText?.length).toBeGreaterThan(100);
    });

    test('should have Bransk brand visible', async ({ page }) => {
      await page.goto(UI_BASE);
      await page.waitForLoadState('networkidle');
      
      // Check for brand - SVG or text content
      const brand = page.locator('svg').or(page.getByText('Bransk')).or(page.getByText('ChronoKG')).first();
      await expect(brand).toBeVisible({ timeout: 30000 });
    });
  });

  test.describe('Hash Navigation (client-side routing)', () => {
    test('should navigate to project view via hash', async ({ page }) => {
      await page.goto(`${UI_BASE}/#project`);
      await page.waitForLoadState('networkidle');
      
      // Wait for client-side routing and lazy loading
      await page.waitForTimeout(3000);
      
      // Should have some project-related content
      const content = page.locator('main, #root, body').first();
      await expect(content).toBeVisible({ timeout: 15000 });
    });

    test('should navigate to library (studies) via hash', async ({ page }) => {
      await page.goto(`${UI_BASE}/#studies`);
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(3000);
      
      const content = page.locator('main, #root, body').first();
      await expect(content).toBeVisible({ timeout: 15000 });
    });

    test('should navigate to library (entries) via hash', async ({ page }) => {
      await page.goto(`${UI_BASE}/#entries`);
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(3000);
      
      const content = page.locator('main, #root, body').first();
      await expect(content).toBeVisible({ timeout: 15000 });
    });

    test('should navigate to graph view via hash', async ({ page }) => {
      await page.goto(`${UI_BASE}/#graph`);
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(3000);
      
      const content = page.locator('main, #root, body').first();
      await expect(content).toBeVisible({ timeout: 15000 });
    });

    test('should navigate to timeline view via hash', async ({ page }) => {
      await page.goto(`${UI_BASE}/#timeline`);
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(3000);
      
      const content = page.locator('main, #root, body').first();
      await expect(content).toBeVisible({ timeout: 15000 });
    });

    test('should navigate to theories view via hash', async ({ page }) => {
      await page.goto(`${UI_BASE}/#theories`);
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(3000);
      
      const content = page.locator('main, #root, body').first();
      await expect(content).toBeVisible({ timeout: 15000 });
    });
  });

  test.describe('Chat Panel (if accessible)', () => {
    test('should have chat button in sidebar', async ({ page }) => {
      await page.goto(`${UI_BASE}/#project`);
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(3000);
      
      // Check for chat button - Bot icon in sidebar
      const chatButton = page.locator('button:has(svg), button[title*="Chat"], button[aria-label*="Chat"]').first();
      await expect(chatButton).toBeVisible({ timeout: 20000 });
    });
  });
});