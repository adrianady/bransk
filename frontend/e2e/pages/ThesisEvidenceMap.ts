import { type Page, type Locator, expect } from '@playwright/test';

export class ThesisEvidenceMap {
  readonly page: Page;
  readonly container: Locator;
  readonly sections: Locator;
  readonly claims: Locator;
  readonly evidenceLinks: Locator;
  readonly addEvidenceButtons: Locator;
  readonly progressStats: Locator;

  constructor(page: Page) {
    this.page = page;
    this.container = page.locator('[data-testid="thesis-evidence-map"], .thesis-evidence-map, .evidence-map').first();
    this.sections = page.locator('[data-section-id], .evidence-section, .section-row').first();
    this.claims = page.locator('[data-claim-id], .evidence-claim, .claim-item').first();
    this.evidenceLinks = page.locator('[data-evidence-id], .evidence-link, .evidence-item').first();
    this.addEvidenceButtons = page.locator('button:has-text("Adaugă dovadă"), button:has-text("Add evidence"), button[aria-label*="evidence" i]').first();
    this.progressStats = page.locator('.progress-stats, .evidence-progress, [data-progress]').first();
  }

  async goto() {
    await this.page.goto('/#project');
    await this.page.waitForLoadState('networkidle');
    // Navigate to evidence map (might be a tab or separate view)
    const evidenceTab = this.page.locator('button:has-text("Dovadă"), button:has-text("Evidence"), button:has-text("Hartă")').first();
    if (await evidenceTab.isVisible({ timeout: 5000 })) {
      await evidenceTab.click();
      await this.page.waitForLoadState('networkidle');
    }
    await expect(this.container).toBeVisible({ timeout: 10000 });
  }

  async getSectionCount(): Promise<number> {
    return await this.sections.count();
  }

  async getClaimCount(): Promise<number> {
    return await this.claims.count();
  }

  async getEvidenceCount(): Promise<number> {
    return await this.evidenceLinks.count();
  }

  async clickAddEvidence(claimIndex: number = 0) {
    const buttons = this.addEvidenceButtons;
    const count = await buttons.count();
    if (count > claimIndex) {
      await buttons.nth(claimIndex).click();
      await this.page.waitForLoadState('networkidle');
    }
  }

  async getProgressText(): Promise<string | null> {
    if (await this.progressStats.isVisible({ timeout: 3000 })) {
      return await this.progressStats.textContent();
    }
    return null;
  }
}