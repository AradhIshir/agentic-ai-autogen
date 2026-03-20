import { test, expect } from '@playwright/test';
const APP_URL = 'https://www.saucedemo.com/';

// Positive Test Cases

test('Positive: EC-298-TC-01 — Successful Login', async ({ page }) => {
    await page.goto(APP_URL);
    await expect(page).toHaveURL(APP_URL);

    await page.fill('[data-test="username"]', 'standard_user');
    await page.fill('[data-test="password"]', 'secret_sauce');
    await page.click('[data-test="login-button"]');

    await expect(page).toHaveURL(/.*inventory\.html/);
    await expect(page.locator('.inventory_list')).toBeVisible();
});

// Negative Test Cases

test('Negative: EC-298-TC-02 — Unsuccessful Login with Incorrect Password', async ({ page }) => {
    await page.goto(APP_URL);
    await expect(page).toHaveURL(APP_URL);

    await page.fill('[data-test="username"]', 'standard_user');
    await page.fill('[data-test="password"]', 'wrong_password');
    await page.click('[data-test="login-button"]');

    await expect(page.locator('[data-test="error"]')).toBeVisible();
    await expect(page.locator('[data-test="error"]')).toContainText('Username and password do not match');
});

// Boundary Test Cases

test('Boundary: EC-298-TC-03 — Username Field Boundary Test', async ({ page }) => {
    await page.goto(APP_URL);
    await expect(page).toHaveURL(APP_URL);

    await page.fill('[data-test="username"]', '');
    await page.fill('[data-test="password"]', 'secret_sauce');
    await page.click('[data-test="login-button"]');

    await expect(page.locator('[data-test="error"]')).toBeVisible();
    await expect(page.locator('[data-test="error"]')).toContainText('Username cannot be empty');
});

// Edge Test Cases

test('Edge: EC-298-TC-04 — Login with Maximum Length Username', async ({ page }) => {
    await page.goto(APP_URL);
    await expect(page).toHaveURL(APP_URL);

    await page.fill('[data-test="username"]', 'a_very_long_username_exceeding_normal_length');
    await page.fill('[data-test="password"]', 'secret_sauce');
    await page.click('[data-test="login-button"]');

    await expect(page.locator('[data-test="error"]')).toBeVisible();
    await expect(page.locator('[data-test="error"]')).toContainText('Username is too long');
});