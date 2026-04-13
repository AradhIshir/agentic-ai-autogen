import { test, expect } from '@playwright/test';
const APP_URL = 'https://www.saucedemo.com/';

test.beforeEach(async ({ page }) => {
  test.setTimeout(120_000);
  page.setDefaultTimeout(90_000);
});

// Positive: EC-298-TC-001 — Successful Login with Valid Credentials
test('Positive: EC-298-TC-001 — Successful Login with Valid Credentials', async ({ page }) => {
  await page.goto(APP_URL);
  await expect(page).toHaveURL(APP_URL);
  await expect(page).toHaveTitle(/.*Sauce Demo.*/);
  await expect(page.locator('[data-test="username"]')).toBeVisible();
  await expect(page.locator('[data-test="password"]')).toBeVisible();
  await expect(page.locator('[data-test="login-button"]).toBeVisible();

  await page.fill('[data-test="username"]', 'standard_user');
  await expect(page.locator('[data-test="username"]')).toHaveValue('standard_user');

  await page.fill('[data-test="password"]', 'secret_sauce');
  await expect(page.locator('[data-test="password"]')).toHaveValue('secret_sauce');

  await page.click('[data-test="login-button"]');
  await expect(page).toHaveURL(/.*inventory\.html/);
  await expect(page.locator('.inventory_list')).toBeVisible();
});

// Negative: EC-298-TC-002 — Unsuccessful Login with Invalid Password
test('Negative: EC-298-TC-002 — Unsuccessful Login with Invalid Password', async ({ page }) => {
  await page.goto(APP_URL);
  await expect(page).toHaveURL(APP_URL);
  await expect(page.locator('[data-test="username"]')).toBeVisible();
  await expect(page.locator('[data-test="password"]')).toBeVisible();
  await expect(page.locator('[data-test="login-button"]).toBeVisible();

  await page.fill('[data-test="username"]', 'standard_user');
  await expect(page.locator('[data-test="username"]')).toHaveValue('standard_user');

  await page.fill('[data-test="password"]', 'wrong_password');
  await expect(page.locator('[data-test="password"]')).toHaveValue('wrong_password');

  await page.click('[data-test="login-button"]');
  await expect(page.locator('[data-test="error"]')).toBeVisible();
  await expect(page.locator('[data-test="error"]')).toContainText('do not match');
});

// Boundary: EC-298-TC-003 — Login Attempt with Empty Username and Valid Password
test('Boundary: EC-298-TC-003 — Login Attempt with Empty Username and Valid Password', async ({ page }) => {
  await page.goto(APP_URL);
  await expect(page).toHaveURL(APP_URL);
  await expect(page.locator('[data-test="username"]')).toBeVisible();
  await expect(page.locator('[data-test="password"]')).toBeVisible();
  await expect(page.locator('[data-test="login-button"]').toBeVisible();

  await page.fill('[data-test="password"]', 'secret_sauce');
  await expect(page.locator('[data-test="password"]')).toHaveValue('secret_sauce');

  await page.click('[data-test="login-button"]');
  await expect(page.locator('[data-test="error"]')).toBeVisible();
  await expect(page.locator('[data-test="error"]')).toContainText('is required');
});

// Edge: EC-298-TC-004 — Login Attempt with Maximum Length Username and Password
test('Edge: EC-298-TC-004 — Login Attempt with Maximum Length Username and Password', async ({ page }) => {
  await page.goto(APP_URL);
  await expect(page).toHaveURL(APP_URL);
  await expect(page.locator('[data-test="username"]')).toBeVisible();
  await expect(page.locator('[data-test="password"]')).toBeVisible();
  await expect(page.locator('[data-test="login-button"]').toBeVisible();

  const maxLengthUsername = 'xxxxxxxxxxxxxxxxxxxx'; // 20 characters
  const maxLengthPassword = 'xxxxxxxxxxxxxxxxxxxx'; // 20 characters

  await page.fill('[data-test="username"]', maxLengthUsername);
  await expect(page.locator('[data-test="username"]')).toHaveValue(maxLengthUsername);

  await page.fill('[data-test="password"]', maxLengthPassword);
  await expect(page.locator('[data-test="password"]')).toHaveValue(maxLengthPassword);

  await page.click('[data-test="login-button"]');
  await expect(page).toHaveURL(/.*inventory\.html/);
  await expect(page.locator('.inventory_list')).toBeVisible();
});
