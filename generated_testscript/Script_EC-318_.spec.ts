import { test, expect } from '@playwright/test';
const APP_URL = 'https://www.saucedemo.com/';

test('Positive: EC-318-TC-001 — Cart Cleanup and Continue Shopping - Positive Flow', async ({ page }) => {
  await page.goto(APP_URL);
  await expect(page).toHaveURL(APP_URL);

  await expect(page).toBeVisible('[data-test="username"]');
  await expect(page).toBeVisible('[data-test="password"]');
  await expect(page).toBeVisible('[data-test="login-button"]');

  await page.fill('[data-test="username"]', 'standard_user');
  await expect(page.locator('[data-test="username"]')).toHaveValue('standard_user');

  await page.fill('[data-test="password"]', 'secret_sauce');
  await expect(page.locator('[data-test="password"]')).toHaveValue('secret_sauce');

  await page.click('[data-test="login-button"]');
  await expect(page).toHaveURL(/.*inventory\.html/);
  await expect(page.locator('.inventory_list')).toBeVisible();

  await page.click('.shopping_cart_link');
  await expect(page).toHaveURL(/.*cart\.html/);

  await page.click('.cart_item .btn_secondary');
  await page.click('.cart_item .btn_secondary');
  await expect(page.locator('.cart_list')).toHaveCount(0);

  await page.click('.btn_secondary');
  await expect(page).toHaveURL(/.*inventory\.html/);
});

test('Negative: EC-318-TC-002 — Cart Cleanup and Continue Shopping - Negative Flow (Invalid Credentials)', async ({ page }) => {
  await page.goto(APP_URL);
  await expect(page).toHaveURL(APP_URL);

  await page.fill('[data-test="username"]', 'invalid_user');
  await expect(page.locator('[data-test="username"]')).toHaveValue('invalid_user');

  await page.fill('[data-test="password"]', 'invalid_password');
  await expect(page.locator('[data-test="password"]')).toHaveValue('invalid_password');

  await page.click('[data-test="login-button"]');
  await expect(page.locator('[data-test="error"]')).toBeVisible();
  await expect(page.locator('[data-test="error"]')).toContainText('do not match');
});

test('Boundary: EC-318-TC-003 — Cart Cleanup and Continue Shopping - Empty Cart Scenario', async ({ page }) => {
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
  await expect(page.locator('.cart_list')).toContainText('Your cart is empty');

  await page.click('.btn_secondary');
  await expect(page).toHaveURL(/.*inventory\.html/);
});

test('Edge: EC-318-TC-004 — Cart Cleanup and Continue Shopping - Multiple Removals', async ({ page }) => {
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

  await page.click('.cart_item .btn_secondary');
  await expect(page.locator('.inventory_list')).toBeVisible();

  await page.click('.cart_item .btn_secondary');
  await expect(page.locator('.inventory_list')).toBeVisible();

  await page.click('.btn_secondary');
  await expect(page).toHaveURL(/.*inventory\.html/);
});
