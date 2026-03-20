import { test, expect } from '@playwright/test';
const APP_URL = 'https://www.saucedemo.com/';

// Positive Test Cases

// Test Case ID: EC-298-TC-001
test('Positive: EC-298-TC-001 — Successful Login and Navigation to Products Page', async ({ page }) => {
    await page.goto(APP_URL);
    await expect(page).toHaveURL(APP_URL);
    await expect(page.locator('[data-test="username"]')).toBeVisible();
    await expect(page.locator('[data-test="password"]')).toBeVisible();
    await expect(page.locator('[data-test="login-button"]')).toBeVisible();
    await page.fill('[data-test="username"]', 'standard_user');
    await expect(page.locator('[data-test="username"]')).toHaveValue('standard_user');
    await page.fill('[data-test="password"]', 'secret_sauce');
    await expect(page.locator('[data-test="password"]')).toHaveValue('secret_sauce');
    await page.click('[data-test="login-button"]');
    await expect(page).toHaveURL(/.*inventory\.html/);
    await expect(page.locator('.inventory_list')).toBeVisible();
});

// Negative Test Cases

// Test Case ID: EC-298-TC-002
 test('Negative: EC-298-TC-002 — Unsuccessful Login with Invalid Credentials', async ({ page }) => {
    await page.goto(APP_URL);
    await expect(page).toHaveURL(APP_URL);
    await expect(page.locator('[data-test="username"]')).toBeVisible();
    await expect(page.locator('[data-test="password"]')).toBeVisible();
    await expect(page.locator('[data-test="login-button"]')).toBeVisible();
    await page.fill('[data-test="username"]', 'invalid_user');
    await expect(page.locator('[data-test="username"]')).toHaveValue('invalid_user');
    await page.fill('[data-test="password"]', 'wrong_password');
    await expect(page.locator('[data-test="password"]')).toHaveValue('wrong_password');
    await page.click('[data-test="login-button"]');
    await expect(page.locator('[data-test="error"]')).toBeVisible();
    await expect(page.locator('[data-test="error"]')).toContainText('Invalid username or password.');
});

// Boundary Test Cases

// Test Case ID: EC-298-TC-003
 test('Boundary: EC-298-TC-003 — Login with Maximum Username Length', async ({ page }) => {
    await page.goto(APP_URL);
    await expect(page).toHaveURL(APP_URL);
    await expect(page.locator('[data-test="username"]')).toBeVisible();
    await expect(page.locator('[data-test="password"]')).toBeVisible();
    await expect(page.locator('[data-test="login-button"]')).toBeVisible();
    // Assuming "a" repeated 255 times to simulate maximum length username
    await page.fill('[data-test="username"]', 'a'.repeat(255));
    await expect(page.locator('[data-test="username"]')).toHaveValue('a'.repeat(255));
    await page.fill('[data-test="password"]', 'secret_sauce');
    await expect(page.locator('[data-test="password"]')).toHaveValue('secret_sauce');
    await page.click('[data-test="login-button"]');
    await expect(page).toHaveURL(/.*inventory\.html/);
    await expect(page.locator('.inventory_list')).toBeVisible();
});

// Edge Test Cases

// Test Case ID: EC-298-TC-004
 test('Edge: EC-298-TC-004 — Login with Empty Username and Valid Password', async ({ page }) => {
    await page.goto(APP_URL);
    await expect(page).toHaveURL(APP_URL);
    await expect(page.locator('[data-test="username"]')).toBeVisible();
    await expect(page.locator('[data-test="password"]')).toBeVisible();
    await expect(page.locator('[data-test="login-button"]')).toBeVisible();
    await expect(page.locator('[data-test="username"]')).toHaveValue('');
    await page.fill('[data-test="password"]', 'secret_sauce');
    await expect(page.locator('[data-test="password"]')).toHaveValue('secret_sauce');
    await page.click('[data-test="login-button"]');
    await expect(page.locator('[data-test="error"]')).toBeVisible();
    await expect(page.locator('[data-test="error"]')).toContainText('Username is required');
});
