import { type Page, type Locator, expect } from '@playwright/test';

export class QuestionnairesPanel {
  readonly page: Page;
  readonly projectPage: any; // Reference to project page for tab navigation
  readonly questionnaireList: Locator;
  readonly newQuestionnaireButton: Locator;
  readonly titleInput: Locator;
  readonly constructInput: Locator;
  readonly targetPopulationInput: Locator;
  readonly descriptionInput: Locator;
  readonly saveButton: Locator;
  readonly itemsTab: Locator;
  readonly aiGenerateButton: Locator;
  readonly aiCritiqueButton: Locator;
  readonly itemRows: Locator;
  readonly addItemButton: Locator;
  readonly critiqueModal: Locator;
  readonly critiqueContent: Locator;

  constructor(page: Page) {
    this.page = page;
    this.questionnaireList = page.locator('[data-questionnaire-id], .questionnaire-item, .questionnaires-list > div').first();
    this.newQuestionnaireButton = page.locator('button:has-text("Nou chestionar"), button:has-text("New questionnaire"), button:has-text("+ Chestionar")').first();
    this.titleInput = page.locator('input[name="title"], input[placeholder*="Titlu" i]').first();
    this.constructInput = page.locator('input[name="construct"], input[placeholder*="Construct" i]').first();
    this.targetPopulationInput = page.locator('input[name="target_population"], input[placeholder*="Populație" i]').first();
    this.descriptionInput = page.locator('textarea[name="description"], textarea[placeholder*="Descriere" i]').first();
    this.saveButton = page.locator('button:has-text("Salvează"), button:has-text("Save"), button[type="submit"]').first();
    this.itemsTab = page.locator('button[role="tab"]:has-text("Item"), button[role="tab"]:has-text("Întrebări")').first();
    this.aiGenerateButton = page.locator('button:has-text("Generează cu AI"), button:has-text("AI Generate"), button:has-text("Generează")').first();
    this.aiCritiqueButton = page.locator('button:has-text("Critică AI"), button:has-text("AI Critique"), button:has-text("Critică")').first();
    this.itemRows = page.locator('[data-item-id], .item-row, .questionnaire-item-row');
    this.addItemButton = page.locator('button:has-text("Adaugă item"), button:has-text("Add item"), button:has-text("+ Item")').first();
    this.critiqueModal = page.locator('[role="dialog"], .modal').filter({ hasText: /Critică|Critique/i }).first();
    this.critiqueContent = page.locator('.critique-content, .markdown-content, [data-critique], .prose').first();
  }

  async goto() {
    await this.page.goto('/#project');
    await this.page.waitForLoadState('networkidle');
    // Click methodology tab (Chestionare & Scale)
    const methodologyTab = this.page.locator('button[role="tab"]:has-text("Chestionare & Scale"), button[role="tab"]:has-text("Chestionare")').first();
    if (await methodologyTab.isVisible({ timeout: 5000 })) {
      await methodologyTab.click();
      await this.page.waitForLoadState('networkidle');
    }
  }

  async createQuestionnaire(data: { title: string; construct: string; targetPopulation: string; description?: string }) {
    if (await this.newQuestionnaireButton.isVisible({ timeout: 3000 })) {
      await this.newQuestionnaireButton.click();
      await this.titleInput.fill(data.title);
      await this.constructInput.fill(data.construct);
      await this.targetPopulationInput.fill(data.targetPopulation);
      if (data.description) {
        await this.descriptionInput.fill(data.description);
      }
      await this.saveButton.click();
      await this.page.waitForLoadState('networkidle');
    }
  }

  async clickAIGenerate() {
    if (await this.aiGenerateButton.isVisible({ timeout: 3000 })) {
      await this.aiGenerateButton.click();
      await this.page.waitForLoadState('networkidle', { timeout: 60000 });
    }
  }

  async clickAICritique() {
    if (await this.aiCritiqueButton.isVisible({ timeout: 3000 })) {
      await this.aiCritiqueButton.click();
      await expect(this.critiqueModal).toBeVisible({ timeout: 15000 });
      await this.page.waitForLoadState('networkidle', { timeout: 60000 });
    }
  }

  async getCritiqueText(): Promise<string | null> {
    if (await this.critiqueContent.isVisible({ timeout: 5000 })) {
      return await this.critiqueContent.textContent();
    }
    return null;
  }

  async getItemCount(): Promise<number> {
    return await this.itemRows.count();
  }
}