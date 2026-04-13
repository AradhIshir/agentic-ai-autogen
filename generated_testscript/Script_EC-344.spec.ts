import { test, expect } from '@playwright/test';
const APP_URL = 'https://www.saucedemo.com/';

test.beforeEach(async ({ page }) => {
  test.setTimeout(120_000);
  page.setDefaultTimeout(90_000);
});

test('Positive: EC-344-TC-001 — Successful Checkout Information Entry', async ({ page }) => {
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

  await page.click('text=Add to cart'); // Click on Add to Cart for the first product
  await page.click('[data-test="shopping_cart_link"]');
  await expect(page).toHaveURL(/.*cart\.html/);

  await page.click('[data-test="checkout"]');
  await expect(page).toHaveURL(/.*checkout-step-one\.html/);

  await page.fill('[data-test="firstName"]', 'Aradhana');
  await expect(page.locator('[data-test="firstName"]')).toHaveValue('Aradhana');

  await page.fill('[data-test="lastName"]', 'Goyal');
  await expect(page.locator('[data-test="lastName"]')).toHaveValue('Goyal');

  await page.fill('[data-test="postalCode"]', '11111');
  await expect(page.locator('[data-test="postalCode"]')).toHaveValue('11111');

  await page.click('[data-test="continue"]');
  await expect(page).toHaveURL(/.*checkout-step-two\.html/);
  await expect(page.locator('.summary_info')).toBeVisible();
});