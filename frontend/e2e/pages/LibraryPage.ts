import { type Page, type Locator, expect } from '@playwright/test';

export class LibraryPage {
  readonly page: Page;
  readonly filterPills: Locator;
  readonly searchInput: Locator;
  readonly entryList: Locator;
  readonly entryCards: Locator;
  readonly pagination: Locator;
  readonly detailModal: Locator;
  readonly closeDetailButton: Locator;

  constructor(page: Page) {
    this.page = page;
    // Filter pills are buttons with role="tab" inside div[role="tablist"]
    this.filterPills = page.locator('[role="tablist"] button[role="tab"]');
    this.searchInput = page.locator('input[placeholder*="Caută" i], input[placeholder*="Search" i], input[type="search"]').first();
    this.entryList = page.locator('main, .entries-grid, [data-testid="entry-list"]').first();
    this.entryCards = page.locator('article, .entry-card, [data-entry-id]');
    this.pagination = page.locator('nav[aria-label="Pagination"], .pagination, button:has-text("Anterior"), button:has-text("Următor")');
    this.detailModal = page.locator('[role="dialog"], .modal, .entry-detail').first();
    this.closeDetailButton = page.locator('button[aria-label*="Închide" i], button[aria-label*="Close" i], .modal button:has-text("×")').first();
  }

  async gotoStudies() {
    await this.page.goto('/#studies');
    await this.page.waitForLoadState('networkidle');
    await expect(this.filterPills.first()).toBeVisible({ timeout: 20000 });
  }

  async gotoEntries() {
    await this.page.goto('/#entries');
    await this.page.waitForLoadState('networkidle');
    await expect(this.filterPills.first()).toBeVisible({ timeout: 20000 });
  }

  // Backward compatibility
  async goto() {
    await this.gotoStudies();
  }

  async selectFilter(filterName: 'Toate intrările' | 'Studii academice' | 'Notițe & idei' | 'Cu PDF' | 'Fără PDF') {
    const pill = this.filterPills.filter({ hasText: filterName }).first();
    await expect(pill).toBeVisible({ timeout: 10000 });
    await pill.click();
    await this.page.waitForLoadState('networkidle');
  }

  async search(query: string) {
    await this.searchInput.fill(query);
    await this.searchInput.press('Enter');
    await this.page.waitForLoadState('networkidle');
  }

  async openEntry(index: number = 0) {
    const entries = this.entryList.locator('article, .entry-card, [data-entry-id]');
    const count = await entries.count();
    if (count === 0) throw new Error('No entries found');
    await entries.nth(index).click();
    await this.page.waitForLoadState('networkidle');
    await expect(this.detailModal).toBeVisible({ timeout: 5000 });
  }

  async closeDetail() {
    if (await this.closeDetailButton.isVisible({ timeout: 2000 })) {
      await this.closeDetailButton.click();
      await expect(this.detailModal).toBeHidden({ timeout: 3000 });
    }
  }

  async getEntryCount(): Promise<number> {
    return await this.entryList.locator('article, .entry-card, [data-entry-id]').count();
  }

  async verifyFilterActive(filterName: string) {
    const pill = this.filterPills.filter({ hasText: filterName }).first();
    await expect(pill).toHaveClass(/active|bg-surface|border-chrono/, { timeout: 5000 });
  }
}