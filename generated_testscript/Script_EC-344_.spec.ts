import { test, expect } from '@playwright/test';
const APP_URL = 'https://www.saucedemo.com/';

test('Positive: EC-344-TC-001 — Successful Checkout Information Entry', async ({ page }) => {
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

    await page.click('.btn_primary'); // assuming the button triggers the add to cart
    await page.click('.shopping_cart_link');
    await expect(page).toBeVisible();

    await page.click('.checkout_button');
    await expect(page.locator('[data-test="firstName"]')).toBeVisible();
    await expect(page.locator('[data-test="lastName"]')).toBeVisible();
    await expect(page.locator('[data-test="postalCode"]')).toBeVisible();

    await page.fill('[data-test="firstName"]', 'Aradhana');
    await expect(page.locator('[data-test="firstName"]')).toHaveValue('Aradhana');

    await page.fill('[data-test="lastName"]', 'Goyal');
    await expect(page.locator('[data-test="lastName"]')).toHaveValue('Goyal');

    await page.fill('[data-test="postalCode"]', '11111');
    await expect(page.locator('[data-test="postalCode"]')).toHaveValue('11111');

    await page.click('.btn_primary');
    await expect(page.locator('.summary_container')).toBeVisible();
});

test('Negative: EC-344-TC-002 — Unsuccessful Checkout Information Entry with Missing Fields', async ({ page }) => {
    await page.goto(APP_URL);
    await expect(page).toHaveURL(APP_URL);

    await page.fill('[data-test="username"]', 'standard_user');
    await expect(page.locator('[data-test="username"]')).toHaveValue('standard_user');

    await page.fill('[data-test="password"]', 'secret_sauce');
    await expect(page.locator('[data-test="password"]')).toHaveValue('secret_sauce');

    await page.click('[data-test="login-button"]');
    await expect(page).toHaveURL(/.*inventory\.html/);

    await page.click('.btn_primary'); // assuming the button triggers the add to cart
    await page.click('.shopping_cart_link');

    await page.click('.checkout_button');

    await page.fill('[data-test="firstName"]', '');
    await page.fill('[data-test="lastName"]', '');
    await page.fill('[data-test="postalCode"]', '');

    await page.click('.btn_primary');
    await expect(page.locator('[data-test="error"]').toBeVisible());
    await expect(page.locator('[data-test="error"]').toContainText('is required');
});

test('Boundary: EC-344-TC-003 — Borderline Zip/Postal Code Input Validation', async ({ page }) => {
    await page.goto(APP_URL);
    await expect(page).toHaveURL(APP_URL);

    await page.fill('[data-test="username"]', 'standard_user');
    await expect(page.locator('[data-test="username"]')).toHaveValue('standard_user');

    await page.fill('[data-test="password"]', 'secret_sauce');
    await expect(page.locator('[data-test="password"]')).toHaveValue('secret_sauce');

    await page.click('[data-test="login-button"]');
    await expect(page).toHaveURL(/.*inventory\.html/);

    await page.click('.btn_primary'); // assuming the button triggers the add to cart
    await page.click('.shopping_cart_link');

    await page.click('.checkout_button');
    await page.fill('[data-test="postalCode"]', '11110');
    await expect(page.locator('[data-test="postalCode"]')).toHaveValue('11110');

    await page.click('.btn_primary');
    await expect(page.locator('.summary_container')).toBeVisible();
});

test('Edge: EC-344-TC-004 — Edge Case with Extremely Long Names', async ({ page }) => {
    await page.goto(APP_URL);
    await expect(page).toHaveURL(APP_URL);

    await page.fill('[data-test="username"]', 'standard_user');
    await expect(page.locator('[data-test="username"]')).toHaveValue('standard_user');

    await page.fill('[data-test="password"]', 'secret_sauce');
    await expect(page.locator('[data-test="password"]')).toHaveValue('secret_sauce');

    await page.click('[data-test="login-button"]');
    await expect(page).toHaveURL(/.*inventory\.html/);

    await page.click('.btn_primary'); // assuming the button triggers the add to cart
    await page.click('.shopping_cart_link');

    await page.click('.checkout_button');
    await page.fill('[data-test="firstName"]', 'A very very long string that exceeds the input limit');
    await page.fill('[data-test="lastName"]', 'A very very long string that exceeds the input limit');

    // Add assertions to check for truncation/error messages here
});
