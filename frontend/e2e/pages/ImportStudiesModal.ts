import { type Page, type Locator, expect } from '@playwright/test';

export class ImportStudiesModal {
  readonly page: Page;
  readonly openButton: Locator;
  readonly modal: Locator;
  readonly tabs: Locator;
  readonly searchTab: Locator;
  readonly zoteroTab: Locator;
  readonly fileTab: Locator;
  readonly closeButton: Locator;
  // Search tab
  readonly searchInput: Locator;
  readonly searchButton: Locator;
  readonly searchResults: Locator;
  // Zotero tab
  readonly zoteroStatus: Locator;
  readonly zoteroImportButton: Locator;
  readonly zoteroCollections: Locator;
  readonly zoteroSyncButton: Locator;
  // File tab
  readonly fileInput: Locator;
  readonly fileUploadButton: Locator;

  constructor(page: Page) {
    this.page = page;
    this.openButton = page.locator('button:has-text("Importă studii"), button:has-text("Import"), button:has-text("Adaugă studii")').first();
    this.modal = page.locator('[role="dialog"], .modal, .import-modal').first();
    this.tabs = page.locator('[role="tablist"] button[role="tab"]');
    this.searchTab = this.tabs.filter({ hasText: /Caută|Search/i }).first();
    this.zoteroTab = this.tabs.filter({ hasText: /Zotero/i }).first();
    this.fileTab = this.tabs.filter({ hasText: /Fișier|File/i }).first();
    this.closeButton = page.locator('button[aria-label*="Închide" i], button[aria-label*="Close" i], .modal button:has-text("×")').first();
    // Search tab elements
    this.searchInput = page.locator('input[placeholder*="DOI" i], input[placeholder*="titlu" i], input[placeholder*="title" i]').first();
    this.searchButton = page.locator('button:has-text("Caută"), button:has-text("Search")').first();
    this.searchResults = page.locator('[data-result-id], .search-result, .study-result').first();
    // Zotero tab elements
    this.zoteroStatus = page.locator('[data-zotero-status], .zotero-status, .zotero-info').first();
    this.zoteroImportButton = page.locator('button:has-text("Importă local"), button:has-text("Import local")').first();
    this.zoteroCollections = page.locator('[data-collection-id], .zotero-collection, .collection-item').first();
    this.zoteroSyncButton = page.locator('button:has-text("Sincronizează"), button:has-text("Sync")').first();
    // File tab elements
    this.fileInput = page.locator('input[type="file"]').first();
    this.fileUploadButton = page.locator('button:has-text("Încarcă"), button:has-text("Upload")').first();
  }

  async open() {
    await this.openButton.click();
    await expect(this.modal).toBeVisible({ timeout: 10000 });
  }

  async close() {
    await this.closeButton.click();
    await expect(this.modal).toBeHidden({ timeout: 3000 });
  }

  async selectTab(tab: 'search' | 'zotero' | 'file') {
    const tabMap = {
      search: this.searchTab,
      zotero: this.zoteroTab,
      file: this.fileTab,
    };
    await tabMap[tab].click();
    await this.page.waitForLoadState('networkidle');
  }

  async searchByTitle(title: string) {
    await this.selectTab('search');
    await this.searchInput.fill(title);
    await this.searchButton.click();
    await this.page.waitForLoadState('networkidle');
  }

  async checkZoteroStatus() {
    await this.selectTab('zotero');
    await expect(this.zoteroStatus).toBeVisible({ timeout: 10000 });
    return await this.zoteroStatus.textContent();
  }

  async importFromZoteroLocal() {
    await this.selectTab('zotero');
    await this.zoteroImportButton.click();
    await this.page.waitForLoadState('networkidle', { timeout: 60000 });
  }

  async uploadFile(filePath: string) {
    await this.selectTab('file');
    await this.fileInput.setInputFiles(filePath);
    await this.fileUploadButton.click();
    await this.page.waitForLoadState('networkidle', { timeout: 60000 });
  }
}