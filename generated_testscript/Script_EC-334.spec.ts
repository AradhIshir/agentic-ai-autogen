import { test, expect } from '@playwright/test';
const APP_URL = 'https://www.saucedemo.com/';

test.beforeEach(async ({ page }) => {
  test.setTimeout(120_000);
  page.setDefaultTimeout(90_000);
});

// Positive Test Cases

test('Positive: EC-334-TC-001 — Add a product to the cart successfully', async ({ page }) => {
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
  
  await expect(page.locator('.inventory_list')).toBeVisible();
  await page.click('text=Add to Cart');
  await expect(page.locator('[data-test="shopping_cart_container"]').locator('.shopping_cart_badge')).toBeVisible();
  
  await page.click('.shopping_cart_link');
  await expect(page).toHaveURL(/.*cart\.html/);
  await expect(page.locator('.cart_list')).toBeVisible();
  
  await page.click('text=Add to Cart');
  await expect(page.locator('[data-test="shopping_cart_container"]').locator('.shopping_cart_badge')).toHaveText('5');
});

// Negative Test Cases

test('Negative: EC-334-TC-002 — Attempt to view Cart without adding products', async ({ page }) => {
  await page.goto(APP_URL);
  await expect(page).toHaveURL(APP_URL);

  await page.fill('[data-test="username"]', 'standard_user');
  await page.fill('[data-test="password"]', 'secret_sauce');

  await page.click('[data-test="login-button"]');
  await expect(page).toHaveURL(/.*inventory\.html/);
  
  await page.click('.shopping_cart_link');
  await expect(page.locator('.cart_list')).toBeVisible();
  await expect(page.locator('[data-test="error"]').toBeVisible());
  await expect(page.locator('[data-test="error"]')).toContainText('empty state');
});

// Boundary Test Cases

test('Boundary: EC-334-TC-003 — Add maximum products to the cart', async ({ page }) => {
  await page.goto(APP_URL);
  await expect(page).toHaveURL(APP_URL);

  await page.fill('[data-test="username"]', 'standard_user');
  await page.fill('[data-test="password"]', 'secret_sauce');

  await page.click('[data-test="login-button"]');
  await expect(page).toHaveURL(/.*inventory\.html/);
  
  for (let i = 0; i < 5; i++) {
    await page.click('text=Add to Cart');
  }
  await expect(page.locator('[data-test="shopping_cart_container"]').locator('.shopping_cart_badge')).toHaveText('5');
});

// Edge Test Cases

test('Edge: EC-334-TC-004 — Adding and removing products in cart', async ({ page }) => {
  await page.goto(APP_URL);
  await expect(page).toHaveURL(APP_URL);

  await page.fill('[data-test="username"]', 'standard_user');
  await page.fill('[data-test="password"]', 'secret_sauce');

  await page.click('[data-test="login-button"]');
  await expect(page).toHaveURL(/.*inventory\.html/);
  
  await page.click('text=Add to Cart');
  await page.click('text=Remove');
  await expect(page.locator('[data-test="error"]').toBeVisible());
});
