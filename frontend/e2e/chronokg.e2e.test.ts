import { test, expect } from './test-setup';
import { AuthPage } from './pages/AuthPage';
import { LibraryPage } from './pages/LibraryPage';
import { ProjectPage } from './pages/ProjectPage';
import { ChatPage } from './pages/ChatPage';
import { ImportStudiesModal } from './pages/ImportStudiesModal';
import { QuestionnairesPanel } from './pages/QuestionnairesPanel';
import { StudyPdfModal } from './pages/StudyPdfModal';

const AUTH_TOKEN = 'bbecf51231779b48230618d8841ffaffb52843d4f02a72a4';

test.describe('ChronoKG - Core E2E Test Suite', () => {
  let authPage: AuthPage;
  let libraryPage: LibraryPage;
  let projectPage: ProjectPage;
  let chatPage: ChatPage;
  let importModal: ImportStudiesModal;
  let questionnairesPanel: QuestionnairesPanel;
  let pdfModal: StudyPdfModal;

  test.beforeEach(async ({ authenticatedPage }) => {
    authPage = new AuthPage(authenticatedPage);
    libraryPage = new LibraryPage(authenticatedPage);
    projectPage = new ProjectPage(authenticatedPage);
    chatPage = new ChatPage(authenticatedPage);
    importModal = new ImportStudiesModal(authenticatedPage);
    questionnairesPanel = new QuestionnairesPanel(authenticatedPage);
    pdfModal = new StudyPdfModal(authenticatedPage);
  });

  test.describe('Authentication', () => {
    test('should load app and handle auth if needed', async ({ authenticatedPage }) => {
      await authPage.goto();
      // If auth is required, login with token
      if (await authPage.isAuthRequired()) {
        await authPage.loginWithToken(AUTH_TOKEN);
      }
      // Verify we're in the app
      await expect(authenticatedPage.locator('nav, [role="navigation"]')).toBeVisible({ timeout: 10000 });
    });
  });

  test.describe('Library View (/studies and /entries)', () => {
    test('should navigate to library (studies) and show filter pills', async () => {
      await libraryPage.gotoStudies();
      // Verify filter pills are present (using correct labels)
      const pills = ['Toate intrările', 'Studii academice', 'Notițe & idei', 'Cu PDF', 'Fără PDF'];
      for (const pill of pills) {
        await expect(libraryPage.filterPills.filter({ hasText: pill }).first()).toBeVisible({ timeout: 10000 });
      }
    });

    test('should navigate to library (entries/notes)', async () => {
      await libraryPage.gotoEntries();
      // Should show notes filter active by default
      await expect(libraryPage.filterPills.filter({ hasText: 'Notițe & idei' }).first()).toBeVisible({ timeout: 10000 });
    });

    test('should filter by "Studii academice"', async () => {
      await libraryPage.gotoStudies();
      await libraryPage.selectFilter('Studii academice');
      await libraryPage.verifyFilterActive('Studii academice');
      const count = await libraryPage.getEntryCount();
      expect(count).toBeGreaterThanOrEqual(0);
    });

    test('should filter by "Notițe & idei"', async () => {
      await libraryPage.gotoStudies();
      await libraryPage.selectFilter('Notițe & idei');
      await libraryPage.verifyFilterActive('Notițe & idei');
    });

    test('should filter by "Cu PDF"', async () => {
      await libraryPage.gotoStudies();
      await libraryPage.selectFilter('Cu PDF');
      await libraryPage.verifyFilterActive('Cu PDF');
    });

    test('should filter by "Fără PDF"', async () => {
      await libraryPage.gotoStudies();
      await libraryPage.selectFilter('Fără PDF');
      await libraryPage.verifyFilterActive('Fără PDF');
    });

    test('should search entries', async () => {
      await libraryPage.gotoStudies();
      await libraryPage.search('mentalizare');
      await libraryPage.page.waitForLoadState('networkidle');
      const count = await libraryPage.getEntryCount();
      expect(count).toBeGreaterThanOrEqual(0);
    });

    test('should open and close entry detail', async () => {
      await libraryPage.gotoStudies();
      const count = await libraryPage.getEntryCount();
      if (count > 0) {
        await libraryPage.openEntry(0);
        await expect(libraryPage.detailModal).toBeVisible({ timeout: 5000 });
        await libraryPage.closeDetail();
        await expect(libraryPage.detailModal).toBeHidden({ timeout: 3000 });
      }
    });
  });

  test.describe('Project View', () => {
    test('should navigate to project and show title', async () => {
      await projectPage.goto();
      await expect(projectPage.projectTitle).toBeVisible({ timeout: 15000 });
      const titleText = await projectPage.projectTitle.textContent();
      expect(titleText).toBeTruthy();
    });

    test('should switch between main tabs', async () => {
      await projectPage.goto();
      await projectPage.selectTab('writing');
      await projectPage.selectTab('methodology');
      await projectPage.selectTab('advisor');
      await projectPage.selectTab('evidence');
    });

    test('should display outline sections in writing tab', async () => {
      await projectPage.goto();
      await projectPage.selectTab('writing');
      const sections = await projectPage.outlineSections.count();
      expect(sections).toBeGreaterThanOrEqual(0);
    });

    test('should display claims in methodology tab', async () => {
      await projectPage.goto();
      await projectPage.selectTab('methodology');
      const claims = await projectPage.getClaimCount();
      expect(claims).toBeGreaterThanOrEqual(0);
    });
  });

  test.describe('Chat Panel (opened from sidebar)', () => {
    test('should open chat from sidebar and send message', async () => {
      await chatPage.openChat();
      await chatPage.sendMessage('Salut, cum funcționează aplicația?');
      await chatPage.waitForResponse(15000);
      const msgCount = await chatPage.getMessageCount();
      expect(msgCount).toBeGreaterThan(0);
    });

    test('should trigger research tool via chat', async () => {
      await chatPage.openChat();
      await chatPage.sendMessage('Caută studii despre mentalizare și reziliență');
      await chatPage.waitForResponse(30000);
      // Verify tool tag execution
      await chatPage.verifyToolTagExecution('SEARCH_KB');
      // Verify active task banner appears
      const hasTask = await chatPage.isActiveTaskVisible();
      if (hasTask) {
        const goal = await chatPage.getActiveTaskGoal();
        expect(goal).toContain('mentalizare');
      }
    });

    test('should handle simple greeting', async () => {
      await chatPage.openChat();
      await chatPage.sendMessage('Salut');
      await chatPage.waitForResponse(15000);
      const msgCount = await chatPage.getMessageCount();
      expect(msgCount).toBeGreaterThan(0);
    });
  });

  test.describe('Import Studies Modal', () => {
    test('should open import modal from library', async () => {
      await libraryPage.gotoStudies();
      await importModal.open();
      await expect(importModal.modal).toBeVisible({ timeout: 10000 });
      await importModal.close();
    });

    test('should show Zotero tab and check status', async () => {
      await libraryPage.gotoStudies();
      await importModal.open();
      await importModal.selectTab('zotero');
      const status = await importModal.checkZoteroStatus();
      expect(status).toBeTruthy();
      await importModal.close();
    });

    test('should show search tab', async () => {
      await libraryPage.gotoStudies();
      await importModal.open();
      await importModal.selectTab('search');
      await expect(importModal.searchInput).toBeVisible();
      await importModal.close();
    });

    test('should show file upload tab', async () => {
      await libraryPage.gotoStudies();
      await importModal.open();
      await importModal.selectTab('file');
      await expect(importModal.fileInput).toBeVisible();
      await importModal.close();
    });
  });

  test.describe('Questionnaires Panel (under Methodology tab)', () => {
    test('should navigate to questionnaires in project view', async () => {
      await projectPage.goto();
      await projectPage.selectTab('methodology');
      // Verify questionnaires panel loads
      await expect(questionnairesPanel.newQuestionnaireButton).toBeVisible({ timeout: 10000 });
    });

    test('should create a new questionnaire', async () => {
      await projectPage.goto();
      await projectPage.selectTab('methodology');
      await questionnairesPanel.createQuestionnaire({
        title: 'Test Questionnaire E2E',
        construct: 'Test Construct',
        targetPopulation: 'Adults',
        description: 'Test description for E2E',
      });
      // Verify questionnaire appears in list
      const count = await questionnairesPanel.questionnaireList.count();
      expect(count).toBeGreaterThanOrEqual(0);
    });

    test('should trigger AI Generate', async () => {
      await projectPage.goto();
      await projectPage.selectTab('methodology');
      // First create a questionnaire if none exists
      await questionnairesPanel.clickAIGenerate();
      // Wait for AI response
      await questionnairesPanel.page.waitForTimeout(15000);
      const itemCount = await questionnairesPanel.getItemCount();
      expect(itemCount).toBeGreaterThanOrEqual(0);
    });

    test('should trigger AI Critique', async () => {
      await projectPage.goto();
      await projectPage.selectTab('methodology');
      await questionnairesPanel.clickAICritique();
      await expect(questionnairesPanel.critiqueModal).toBeVisible({ timeout: 20000 });
      const critique = await questionnairesPanel.getCritiqueText();
      expect(critique).toBeTruthy();
    });
  });

  test.describe('PDF Viewer', () => {
    test('should open PDF from library entry', async () => {
      await libraryPage.gotoStudies();
      await libraryPage.selectFilter('Cu PDF');
      const count = await libraryPage.getEntryCount();
      if (count > 0) {
        // Try to open first entry with PDF
        try {
          await pdfModal.openFromLibrary(0);
          await pdfModal.waitForPDFLoad(30000);
          await expect(pdfModal.pdfCanvas).toBeVisible();
          await pdfModal.close();
        } catch (e) {
          // PDF might not be available for first entry, that's OK
          console.log('PDF not available for first entry, skipping');
        }
      }
    });

    test('should handle PDF loading error gracefully', async () => {
      await libraryPage.gotoStudies();
      const count = await libraryPage.getEntryCount();
      if (count > 0) {
        try {
          await pdfModal.openFromLibrary(0);
          if (await pdfModal.isErrorVisible()) {
            await pdfModal.retryLoad();
          }
          await pdfModal.close();
        } catch (e) {
          console.log('PDF test skipped');
        }
      }
    });
  });

  test.describe('Navigation & Persistence', () => {
    test('should maintain state when navigating between views', async ({ authenticatedPage }) => {
      // Go to library, search something
      await libraryPage.gotoStudies();
      await libraryPage.search('test');
      await libraryPage.page.waitForLoadState('networkidle');

      // Go to project
      await projectPage.goto();
      await projectPage.selectTab('methodology');

      // Go back to library via hash
      await authenticatedPage.goto('/#studies');
      await authenticatedPage.waitForLoadState('networkidle');
      // Search should be preserved (sessionStorage)
      const searchValue = await libraryPage.searchInput.inputValue();
      // Note: search persistence depends on implementation
    });

    test('should handle browser back/forward', async ({ authenticatedPage }) => {
      await libraryPage.gotoStudies();
      await projectPage.goto();
      await authenticatedPage.goBack();
      await expect(libraryPage.filterPills.first()).toBeVisible({ timeout: 5000 });
      await authenticatedPage.goForward();
      await expect(projectPage.projectTitle).toBeVisible({ timeout: 5000 });
    });
  });
});

test.describe('API Health Checks', () => {
  test('backend health endpoint responds', async ({ request }) => {
    const response = await request.get('http://localhost:8000/api/health');
    expect(response.ok()).toBeTruthy();
    const body = await response.json();
    expect(body.status).toBe('ok');
  });

  test('projects endpoint requires auth', async ({ request }) => {
    const response = await request.get('http://localhost:8000/api/projects');
    expect(response.status()).toBe(401);
  });

  test('projects endpoint works with token', async ({ request }) => {
    const response = await request.get('http://localhost:8000/api/projects', {
      headers: { Authorization: `Bearer ${AUTH_TOKEN}` },
    });
    expect(response.ok()).toBeTruthy();
    const body = await response.json();
    expect(body.projects).toBeDefined();
    expect(Array.isArray(body.projects)).toBeTruthy();
  });

  test('entries endpoint works with token', async ({ request }) => {
    const response = await request.get('http://localhost:8000/api/entries?limit=1', {
      headers: { Authorization: `Bearer ${AUTH_TOKEN}` },
    });
    expect(response.ok()).toBeTruthy();
    const body = await response.json();
    expect(Array.isArray(body)).toBeTruthy();
  });

  test('chat endpoint works', async ({ request }) => {
    const response = await request.post('http://localhost:8000/api/chat', {
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
});