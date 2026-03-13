import { test, expect } from '@playwright/test';

test('Successful login and inventory access', async ({ page }) => {
    // Step 1: Navigate to the login page
    await page.goto('https://www.saucedemo.com/');

    // Step 2: Fill the username and password
    await page.fill('[data-test="username"]', 'standard_user');
    await page.fill('[data-test="password"]', 'sauce');

    // Step 3: Click on the login button
    await page.click('[data-test="login-button"]');

    // Step 4: Wait for the inventory page to load
    await expect(page).toHaveURL('/inventory.html');

    // Step 5: Verify the product list is visible
    const productList = await page.isVisible('.inventory_list');
    expect(productList).toBe(true);
});