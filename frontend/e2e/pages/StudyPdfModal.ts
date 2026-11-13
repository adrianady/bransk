import { type Page, type Locator, expect } from '@playwright/test';

export class StudyPdfModal {
  readonly page: Page;
  readonly modal: Locator;
  readonly pdfCanvas: Locator;
  readonly pageInput: Locator;
  readonly prevPageButton: Locator;
  readonly nextPageButton: Locator;
  readonly zoomInButton: Locator;
  readonly zoomOutButton: Locator;
  readonly loadingIndicator: Locator;
  readonly errorMessage: Locator;
  readonly retryButton: Locator;
  readonly closeButton: Locator;

  constructor(page: Page) {
    this.page = page;
    this.modal = page.locator('[role="dialog"], .modal').filter({ hasText: /PDF|pdf/i }).first();
    this.pdfCanvas = page.locator('canvas, .pdf-canvas, .pdf-page').first();
    this.pageInput = page.locator('input[type="number"][aria-label*="pagină" i], input[placeholder*="pagină" i]').first();
    this.prevPageButton = page.locator('button[aria-label*="precedent" i], button[aria-label*="previous" i], button:has-text("‹")').first();
    this.nextPageButton = page.locator('button[aria-label*="următor" i], button[aria-label*="next" i], button:has-text("›")').first();
    this.zoomInButton = page.locator('button[aria-label*="zoom in" i], button:has-text("+")').first();
    this.zoomOutButton = page.locator('button[aria-label*="zoom out" i], button:has-text("-")').first();
    this.loadingIndicator = page.locator('.loading, .spinner, [aria-busy="true"]').first();
    this.errorMessage = page.locator('.text-red-500, .error, [role="alert"]').first();
    this.retryButton = page.locator('button:has-text("Reîncearcă"), button:has-text("Retry")').first();
    this.closeButton = page.locator('button[aria-label*="Închide" i], button[aria-label*="Close" i]').first();
  }

  async openFromLibrary(entryIndex: number = 0) {
    // Navigate to library and click PDF icon on entry
    await this.page.goto('/#studies');
    await this.page.waitForLoadState('networkidle');
    const pdfButtons = this.page.locator('button[aria-label*="PDF" i], button:has-text("PDF"), a:has-text("PDF")');
    const count = await pdfButtons.count();
    if (count > entryIndex) {
      await pdfButtons.nth(entryIndex).click();
      await expect(this.modal).toBeVisible({ timeout: 15000 });
    }
  }

  async waitForPDFLoad(timeout: number = 30000) {
    await expect(this.loadingIndicator).toBeHidden({ timeout });
    await expect(this.pdfCanvas).toBeVisible({ timeout: 10000 });
  }

  async navigateToPage(pageNum: number) {
    if (await this.pageInput.isVisible({ timeout: 2000 })) {
      await this.pageInput.fill(pageNum.toString());
      await this.pageInput.press('Enter');
      await this.page.waitForLoadState('networkidle');
    }
  }

  async nextPage() {
    if (await this.nextPageButton.isEnabled({ timeout: 2000 })) {
      await this.nextPageButton.click();
      await this.page.waitForTimeout(500);
    }
  }

  async prevPage() {
    if (await this.prevPageButton.isEnabled({ timeout: 2000 })) {
      await this.prevPageButton.click();
      await this.page.waitForTimeout(500);
    }
  }

  async zoomIn() {
    if (await this.zoomInButton.isVisible({ timeout: 2000 })) {
      await this.zoomInButton.click();
    }
  }

  async zoomOut() {
    if (await this.zoomOutButton.isVisible({ timeout: 2000 })) {
      await this.zoomOutButton.click();
    }
  }

  async close() {
    await this.closeButton.click();
    await expect(this.modal).toBeHidden({ timeout: 3000 });
  }

  async isErrorVisible(): Promise<boolean> {
    return await this.errorMessage.isVisible({ timeout: 3000 });
  }

  async retryLoad() {
    if (await this.retryButton.isVisible({ timeout: 2000 })) {
      await this.retryButton.click();
      await this.waitForPDFLoad();
    }
  }
}