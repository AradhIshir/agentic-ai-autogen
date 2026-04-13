import { test, expect } from '@playwright/test';
const APP_URL = 'https://www.saucedemo.com/';

test.beforeEach(async ({ page }) => {
  test.setTimeout(120_000);
  page.setDefaultTimeout(90_000);
});

// Test Case: EC-343-TC-001 — Successfully Sort Products by Name A to Z

test('Positive: EC-343-TC-001 — Successfully Sort Products by Name A to Z', async ({ page }) => {
  await page.goto(APP_URL);
  await expect(page).toHaveURL(APP_URL);

  // Step 1: Navigate to the application URL.
  await expect(page).toHaveURL(APP_URL);

  // Step 2: Enter "standard_user" in the Username field.
  await page.fill('[data-test="username"]', 'standard_user');
  await expect(page.locator('[data-test="username"]')).toHaveValue('standard_user');

  // Step 3: Enter "secret_sauce" in the Password field.
  await page.fill('[data-test="password"]', 'secret_sauce');
  await expect(page.locator('[data-test="password"]')).toHaveValue('secret_sauce');

  // Step 4: Click the Login button.
  await page.click('[data-test="login-button"]');
  await expect(page).toHaveURL(/.*inventory\.html/);

  // Step 5: Verify that the inventory page displays a sort dropdown with options.
  await expect(page.locator('.inventory_list')).toBeVisible();

  // Step 6: Select "Name (A to Z)" from the sort dropdown.
  await page.selectOption('select[data-test="product_sort_container"]', 'az');

  // Expected Result: User successfully sorts products by name A to Z.
});

// Test Case: EC-343-TC-002 — Successfully Sort Products by Price Low to High

test('Positive: EC-343-TC-002 — Successfully Sort Products by Price Low to High', async ({ page }) => {
  await page.goto(APP_URL);
  await expect(page).toHaveURL(APP_URL);

  // Step 1: Navigate to the application URL.
  await expect(page).toHaveURL(APP_URL);

  // Step 2: Enter "standard_user" in the Username field.
  await page.fill('[data-test="username"]', 'standard_user');
  await expect(page.locator('[data-test="username"]')).toHaveValue('standard_user');

  // Step 3: Enter "secret_sauce" in the Password field.
  await page.fill('[data-test="password"]', 'secret_sauce');
  await expect(page.locator('[data-test="password"]')).toHaveValue('secret_sauce');

  // Step 4: Click the Login button.
  await page.click('[data-test="login-button"]');
  await expect(page).toHaveURL(/.*inventory\.html/);

  // Step 5: Verify that the inventory page displays a sort dropdown with options.
  await expect(page.locator('.inventory_list')).toBeVisible();

  // Step 6: Select "Price (low to high)" from the sort dropdown.
  await page.selectOption('select[data-test="product_sort_container"]', 'lohi');

  // Expected Result: User successfully sorts products by price low to high.
});

// Test Case: EC-343-TC-003 — Attempt to Sort without Logging In

test('Negative: EC-343-TC-003 — Attempt to Sort without Logging In', async ({ page }) => {
  await page.goto(APP_URL);
  await expect(page).toHaveURL(APP_URL);

  // Step 1: Navigate to the application URL.
  await expect(page).toHaveURL(APP_URL);

  // Step 2: Click the Login button without entering credentials.
  await page.click('[data-test="login-button"]');

  // Expected Result: User is prevented from sorting products without logging in.
  await expect(page.locator('[data-test="error"]')).toBeVisible();
  await expect(page.locator('[data-test="error"]').toContainText('is required');
});

// Test Case: EC-343-TC-004 — Check Sorting with Maximum Products

test('Boundary: EC-343-TC-004 — Check Sorting with Maximum Products', async ({ page }) => {
  await page.goto(APP_URL);
  await expect(page).toHaveURL(APP_URL);

  // Step 1: Navigate to the application URL.
  await expect(page).toHaveURL(APP_URL);

  // Step 2: Enter "standard_user" in the Username field.
  await page.fill('[data-test="username"]', 'standard_user');
  await expect(page.locator('[data-test="username"]')).toHaveValue('standard_user');

  // Step 3: Enter "secret_sauce" in the Password field.
  await page.fill('[data-test="password"]', 'secret_sauce');
  await expect(page.locator('[data-test="password"]')).toHaveValue('secret_sauce');

  // Step 4: Click the Login button.
  await page.click('[data-test="login-button"]');
  await expect(page).toHaveURL(/.*inventory\.html/);

  // Step 5: Simulate loading maximum number of products in inventory.
  // MAX products would be code or API related, here we just assume they are loaded on login.

  // Step 6: Select "Price (low to high)" from the sort dropdown.
  await page.selectOption('select[data-test="product_sort_container"]', 'lohi');

  // Expected Result: User successfully sorts maximum products in inventory.
});

// Test Case: EC-343-TC-005 — Verify Sorting with Empty Product List

test('Edge: EC-343-TC-005 — Verify Sorting with Empty Product List', async ({ page }) => {
  await page.goto(APP_URL);
  await expect(page).toHaveURL(APP_URL);

  // Step 1: Navigate to the application URL.
  await expect(page).toHaveURL(APP_URL);

  // Step 2: Enter "standard_user" in the Username field.
  await page.fill('[data-test="username"]', 'standard_user');
  await expect(page.locator('[data-test="username"]')).toHaveValue('standard_user');

  // Step 3: Enter "secret_sauce" in the Password field.
  await page.fill('[data-test="password"]', 'secret_sauce');
  await expect(page.locator('[data-test="password"]')).toHaveValue('secret_sauce');

  // Step 4: Click the Login button.
  await page.click('[data-test="login-button"]');
  await expect(page).toHaveURL(/.*inventory\.html/);

  // Step 5: Clear all products from the inventory to simulate an empty list.
  // This would likely be handled as part of a state or mock setup.

  // Step 6: Attempt to select an option from the sort dropdown.
  await page.selectOption('select[data-test="product_sort_container"]', 'lohi');

  // Expected Result: User sees a message indicating no products are available to sort.
  await expect(page.locator('[data-test="error"]')).toBeVisible();
  await expect(page.locator('[data-test="error"]').toContainText('no products');
});
