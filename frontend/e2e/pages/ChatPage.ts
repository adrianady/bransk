import { type Page, type Locator, expect } from '@playwright/test';

export class ChatPage {
  readonly page: Page;
  readonly chatContainer: Locator;
  readonly messageInput: Locator;
  readonly sendButton: Locator;
  readonly messages: Locator;
  readonly activeTaskBanner: Locator;
  readonly taskProgress: Locator;
  readonly taskCancelButton: Locator;
  readonly toolTags: Locator;
  readonly openChatButton: Locator;

  constructor(page: Page) {
    this.page = page;
    // Chat is opened via sidebar button, not a route
    this.openChatButton = page.locator('button:has-text("Chat AI"), button[title="Chat AI"], button[aria-label*="Chat"]').first();
    this.chatContainer = page.locator('.chat-panel, [data-testid="chat-panel"], aside:has-text("Chat AI")').first();
    this.messageInput = page.locator('textarea[placeholder="Întreabă orice... (Shift+Enter pentru linie nouă)"]');
    this.sendButton = page.locator('button:has-text("Trimite")');
    this.messages = page.locator('[data-message-id], .chat-message, .message-bubble, .prose');
    this.activeTaskBanner = page.locator('div.bg-amber-500\\/10, .active-task-banner:has-text("Task activ")');
    this.taskProgress = page.locator('.task-progress, [role="progressbar"], .progress-bar').first();
    this.taskCancelButton = page.locator('button:has-text("Anulează task")');
    this.toolTags = page.locator('code:has-text("["), .tool-tag').first();
  }

  async openChat() {
    // Go to a view first (project), then open chat panel
    await this.page.goto('/#project');
    await this.page.waitForLoadState('networkidle');
    await expect(this.openChatButton).toBeVisible({ timeout: 10000 });
    await this.openChatButton.click();
    await this.page.waitForTimeout(1000);
    await expect(this.messageInput).toBeVisible({ timeout: 10000 });
  }

  async sendMessage(message: string) {
    await this.messageInput.fill(message);
    await this.sendButton.click();
    await this.page.waitForLoadState('networkidle');
    // Wait for response (at least one new message)
    await this.page.waitForTimeout(5000);
  }

  async waitForResponse(timeout: number = 30000) {
    await this.page.waitForTimeout(timeout);
  }

  async getMessageCount(): Promise<number> {
    return await this.messages.count();
  }

  async isActiveTaskVisible(): Promise<boolean> {
    return await this.activeTaskBanner.isVisible({ timeout: 5000 });
  }

  async getActiveTaskGoal(): Promise<string | null> {
    if (await this.isActiveTaskVisible()) {
      return await this.activeTaskBanner.textContent();
    }
    return null;
  }

  async cancelActiveTask() {
    if (await this.taskCancelButton.isVisible({ timeout: 2000 })) {
      await this.taskCancelButton.click();
      await expect(this.activeTaskBanner).toBeHidden({ timeout: 5000 });
    }
  }

  async verifyToolTagExecution(tagName: string) {
    const tag = this.messages.locator(`text=/\\\\[${tagName}:.*\\\\]/`).first();
    await expect(tag).toBeVisible({ timeout: 15000 });
  }
}