import { type Page, type Locator, expect } from '@playwright/test';

export class ProjectPage {
  readonly page: Page;
  readonly mainTabs: Locator;
  readonly writingTab: Locator;
  readonly methodologyTab: Locator;
  readonly advisorTab: Locator;
  readonly evidenceTab: Locator;
  readonly projectTitle: Locator;
  readonly outlineSections: Locator;
  readonly claimList: Locator;
  readonly addClaimButton: Locator;
  readonly claimInput: Locator;
  readonly claimSectionSelect: Locator;

  constructor(page: Page) {
    this.page = page;
    // Main project tabs are buttons with role="tab" in the main tablist
    this.mainTabs = page.locator('[role="tablist"] button[role="tab"]');
    this.writingTab = this.mainTabs.filter({ hasText: /Structură & Redactare|Structură/i }).first();
    this.methodologyTab = this.mainTabs.filter({ hasText: /Chestionare & Scale|Chestionare/i }).first();
    this.advisorTab = this.mainTabs.filter({ hasText: /Diagnostic & Literatură|Diagnostic/i }).first();
    this.evidenceTab = this.mainTabs.filter({ hasText: /Hartă Teză & Bibliografie|Hartă/i }).first();
    this.projectTitle = page.locator('h1, [data-testid="project-title"]').first();
    this.outlineSections = page.locator('[data-section-id], .outline-section, .section-card, .card:has(> div.flex)').first();
    this.claimList = page.locator('[data-claim-id], .claim-item, .claims-list > div');
    this.addClaimButton = page.locator('button:has-text("Adaugă claim"), button:has-text("Add claim"), button:has-text("+")').first();
    this.claimInput = page.locator('textarea[placeholder*="claim" i], input[placeholder*="claim" i]').first();
    this.claimSectionSelect = page.locator('select[name*="section" i], select[id*="section" i]').first();
  }

  async goto() {
    await this.page.goto('/#project');
    await this.page.waitForLoadState('networkidle');
    await expect(this.projectTitle).toBeVisible({ timeout: 15000 });
  }

  async selectTab(tabName: 'writing' | 'methodology' | 'advisor' | 'evidence') {
    const tabMap = {
      writing: this.writingTab,
      methodology: this.methodologyTab,
      advisor: this.advisorTab,
      evidence: this.evidenceTab,
    };
    const tab = tabMap[tabName];
    await expect(tab).toBeVisible();
    await tab.click();
    await this.page.waitForLoadState('networkidle');
  }

  async addClaim(text: string, sectionId?: string) {
    await this.addClaimButton.click();
    await this.claimInput.fill(text);
    if (sectionId && await this.claimSectionSelect.isVisible({ timeout: 2000 })) {
      await this.claimSectionSelect.selectOption(sectionId);
    }
    await this.page.locator('button:has-text("Salvează"), button:has-text("Save"), button[type="submit"]').first().click();
    await this.page.waitForLoadState('networkidle');
  }

  async getClaimCount(): Promise<number> {
    return await this.claimList.count();
  }
}