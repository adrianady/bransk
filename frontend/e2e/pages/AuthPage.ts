import { type Page, type Locator, expect } from '@playwright/test';

export class AuthPage {
  readonly page: Page;
  readonly tokenInput: Locator;
  readonly submitButton: Locator;
  readonly passwordInput: Locator;
  readonly passwordSubmitButton: Locator;
  readonly errorMessage: Locator;

  constructor(page: Page) {
    this.page = page;
    this.tokenInput = page.locator('input[placeholder*="token" i], input[type="password"]').first();
    this.submitButton = page.locator('button:has-text("Autentificare"), button:has-text("Login"), button[type="submit"]').first();
    this.passwordInput = page.locator('input[type="password"]').last();
    this.passwordSubmitButton = page.locator('button:has-text("Creează cont"), button:has-text("Create account")').first();
    this.errorMessage = page.locator('.text-red-500, .text-destructive, [role="alert"]').first();
  }

  async goto() {
    await this.page.goto('/');
    await this.page.waitForLoadState('networkidle');
  }

  async isAuthRequired(): Promise<boolean> {
    return await this.tokenInput.isVisible({ timeout: 5000 });
  }

  async loginWithToken(token: string) {
    if (await this.isAuthRequired()) {
      await this.tokenInput.fill(token);
      await this.submitButton.click();
      await this.page.waitForLoadState('networkidle');
      await expect(this.page.locator('nav, [role="navigation"]')).toBeVisible({ timeout: 10000 });
    }
  }

  async createPassword(password: string) {
    if (await this.passwordInput.isVisible({ timeout: 3000 })) {
      await this.passwordInput.fill(password);
      await this.passwordSubmitButton.click();
      await this.page.waitForLoadState('networkidle');
    }
  }
}