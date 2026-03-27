import { test, expect } from '@playwright/test';
const APP_URL = 'https://www.saucedemo.com/';

test('Positive: EC-334-TC-001 — Add Product to Cart Successfully', async ({ page }) => {
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
    await page.click('.btn.btn_primary.btn_inventory');
    await expect(page.locator('.shopping_cart_badge')).toContainText('1');
    await page.click('.shopping_cart_link');
    await expect(page).toHaveURL(/.*cart\.html/);
    await expect(page.locator('.cart_item')).toBeVisible();
});

test('Negative: EC-334-TC-002 — Unable to Add Product When Logged Out', async ({ page }) => {
    await page.goto(APP_URL);
    await expect(page).toHaveURL(APP_URL);
    await page.click('.btn.btn_primary.btn_inventory');
    await expect(page.locator('[data-test="error"]').first()).toBeVisible();
    await expect(page.locator('[data-test="error"]')).toContainText('log in');
});

test('Boundary: EC-334-TC-003 — Add Maximum Products to Cart', async ({ page }) => {
    await page.goto(APP_URL);
    await expect(page).toHaveURL(APP_URL);
    await page.fill('[data-test="username"]', 'standard_user');
    await expect(page.locator('[data-test="username"]')).toHaveValue('standard_user');
    await page.fill('[data-test="password"]', 'secret_sauce');
    await expect(page.locator('[data-test="password"]')).toHaveValue('secret_sauce');
    await page.click('[data-test="login-button"]');
    await expect(page).toHaveURL(/.*inventory\.html/);
    await expect(page.locator('.inventory_list')).toBeVisible();
    const addToCartButtons = await page.locator('.btn.btn_primary.btn_inventory');
    const count = await addToCartButtons.count();
    for (let i = 0; i < count; i++) {
        await addToCartButtons.nth(i).click();
    }
    await expect(page.locator('.shopping_cart_badge')).toContainText(count.toString());
    await page.click('.shopping_cart_link');
    await expect(page).toHaveURL(/.*cart\.html/);
    await expect(page.locator('.cart_item')).toHaveCount(count);
});

test('Edge: EC-334-TC-004 — No Products in Cart', async ({ page }) => {
    await page.goto(APP_URL);
    await expect(page).toHaveURL(APP_URL);
    await page.fill('[data-test="username"]', 'standard_user');
    await expect(page.locator('[data-test="username"]')).toHaveValue('standard_user');
    await page.fill('[data-test="password"]', 'secret_sauce');
    await expect(page.locator('[data-test="password"]')).toHaveValue('secret_sauce');
    await page.click('[data-test="login-button"]');
    await expect(page).toHaveURL(/.*inventory\.html/);
    await expect(page.locator('.cart_item')).toHaveCount(0);
    await page.click('.shopping_cart_link');
    await expect(page).toHaveURL(/.*cart\.html/);
    await expect(page.locator('.cart_quantity')).toHaveText('0');
});
