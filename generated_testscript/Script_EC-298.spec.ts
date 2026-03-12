import { test, expect } from '@playwright/test';

// Test case for Jira ticket ID EC-298

test.describe('Login and Inventory Access', () => {

    test('Positive Test Case: Successful Login', async ({ page }) => {
        await page.goto('https://www.saucedemo.com/');
        await expect(page.locator('text=Username')).toBeVisible();
        await expect(page.locator('text=Password')).toBeVisible();
        await page.fill('#user-name', 'standard_user');
        await page.fill('#password', 'sauce');
        await page.click('#login-button');
        await expect(page).toHaveURL('/inventory.html');
        await expect(page.locator('.inventory_list')).toBeVisible();
    });

    test('Negative Test Case: Unsuccessful Login with Invalid Credentials', async ({ page }) => {
        await page.goto('https://www.saucedemo.com/');
        await page.fill('#user-name', 'invalid_user');
        await page.fill('#password', 'invalid');
        await page.click('#login-button');
        await expect(page.locator('.error-message-container')).toBeVisible();
    });

    test('Boundary Test Case: Login without Credentials', async ({ page }) => {
        await page.goto('https://www.saucedemo.com/');
        await page.click('#login-button');
        await expect(page.locator('.error-message-container')).toBeVisible();
    });

    test('Edge Test Case: Long Username and Password', async ({ page }) => {
        await page.goto('https://www.saucedemo.com/');
        await page.fill('#user-name', 'a'.repeat(100));
        await page.fill('#password', 'b'.repeat(100));
        await page.click('#login-button');
        await expect(page.locator('.error-message-container')).toBeVisible();
    });

});
