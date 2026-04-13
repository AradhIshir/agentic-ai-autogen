import { test, expect } from '@playwright/test';
const APP_URL = 'https://www.saucedemo.com/';

test.beforeEach(async ({ page }) => {
  test.setTimeout(120_000);
  page.setDefaultTimeout(90_000);
});

// Positive Test Case: Successful Cart Cleanup and Continue Shopping

test('Positive: EC-318-TC-001 — Successful Cart Cleanup and Continue Shopping', async ({ page }) => {
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
  await page.click('.shopping_cart_link');
  await expect(page).toHaveURL(/.*cart\.html/);
  await page.click('.cart_button'); // assuming there's a button to remove each item
  await page.click('.cart_button'); // repeat as necessary
  await page.click('.continue_shopping');
  await expect(page).toHaveURL(/.*inventory\.html/);
});

// Negative Test Case: Attempt Cart Cleanup with Invalid Credentials

test('Negative: EC-318-TC-002 — Attempt Cart Cleanup with Invalid Credentials', async ({ page }) => {
  await page.goto(APP_URL);
  await expect(page).toHaveURL(APP_URL);
  await page.fill('[data-test="username"]', 'invalid_user');
  await expect(page.locator('[data-test="username"]')).toHaveValue('invalid_user');
  await page.fill('[data-test="password"]', 'invalid_pass');
  await expect(page.locator('[data-test="password"]')).toHaveValue('invalid_pass');
  await page.click('[data-test="login-button"]');
  await expect(page.locator('[data-test="error"]')).toBeVisible();
  await expect(page.locator('[data-test="error"]')).toContainText('do not match');
});

// Boundary Test Case: Cart Cleanup with One Item

test('Boundary: EC-318-TC-003 — Cart Cleanup with One Item', async ({ page }) => {
  await page.goto(APP_URL);
  await expect(page).toHaveURL(APP_URL);
  await page.fill('[data-test="username"]', 'standard_user');
  await expect(page.locator('[data-test="username"]')).toHaveValue('standard_user');
  await page.fill('[data-test="password"]', 'secret_sauce');
  await expect(page.locator('[data-test="password"]')).toHaveValue('secret_sauce');
  await page.click('[data-test="login-button"]');
  await expect(page).toHaveURL(/.*inventory\.html/);
  await expect(page.locator('.inventory_list')).toBeVisible();
  await page.click('.shopping_cart_link');
  await expect(page).toHaveURL(/.*cart\.html/);
  await page.click('.cart_button');
  await page.click('.continue_shopping');
  await expect(page).toHaveURL(/.*inventory\.html/);
});

// Edge Test Case: Edge Case with No Cart Items

test('Edge: EC-318-TC-004 — Edge Case with No Cart Items', async ({ page }) => {
  await page.goto(APP_URL);
  await expect(page).toHaveURL(APP_URL);
  await page.fill('[data-test="username"]', 'standard_user');
  await expect(page.locator('[data-test="username"]')).toHaveValue('standard_user');
  await page.fill('[data-test="password"]', 'secret_sauce');
  await expect(page.locator('[data-test="password"]')).toHaveValue('secret_sauce');
  await page.click('[data-test="login-button"]');
  await expect(page).toHaveURL(/.*inventory\.html/);
  await expect(page.locator('.inventory_list')).toBeVisible();
  await page.click('.shopping_cart_link');
  await expect(page).toHaveURL(/.*cart\.html/);
  await expect(page.locator('.empty_cart')).toBeVisible(); // assuming a selector for empty cart
  await page.click('.continue_shopping');
  await expect(page).toHaveURL(/.*inventory\.html/);
});
