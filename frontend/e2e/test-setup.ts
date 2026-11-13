import { test as base, type Page } from '@playwright/test';

// Auth token from the running backend
const AUTH_TOKEN = 'bbecf51231779b48230618d8841ffaffb52843d4f02a72a4';

type AuthFixtures = {
  authenticatedPage: Page;
};

export const test = base.extend<AuthFixtures>({
  authenticatedPage: async ({ page }, use) => {
    // Set auth token in localStorage before navigation
    await page.addInitScript((token) => {
      localStorage.setItem('chronokg-auth-token', token);
    }, AUTH_TOKEN);

    // Also set it as a cookie for API calls
    await page.context().addCookies([{
      name: 'chronokg-auth-token',
      value: AUTH_TOKEN,
      domain: 'localhost',
      path: '/',
      httpOnly: false,
      secure: false,
    }]);

    await use(page);
  },
});

export { expect } from '@playwright/test';