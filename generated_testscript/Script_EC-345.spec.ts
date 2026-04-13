import { test, expect } from '@playwright/test';
const APP_URL = 'https://www.saucedemo.com/';

test.beforeEach(async ({ page }) => {
  test.setTimeout(120_000);
  page.setDefaultTimeout(90_000);
});

test('Positive: EC-345-TC-001 — View Product Details', async ({ page }) => {
  await page.goto(APP_URL);
  await expect(page).toHaveURL(APP_URL);

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

  await page.click('.inventory_item img');
  await expect(page.locator('h1')).toBeVisible();
  await expect(page.locator('.inventory_details_desc')).toBeVisible();
  await expect(page.locator('.inventory_details_price')).toBeVisible();
  await expect(page.locator('[data-test="add-to-cart"]')).toBeVisible();

  await expect(page.locator('[data-test="back-to-products"]').first()).toBeVisible();

  await page.click('[data-test="back-to-products"]');
  await expect(page).toHaveURL(/.*inventory\.html/);
});
