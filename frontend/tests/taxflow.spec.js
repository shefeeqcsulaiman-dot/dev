import { expect, test } from "@playwright/test";

test("login opens the TaxFlow workspace", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByText("TaxFlow").first()).toBeVisible();
  await expect(page.locator('input[name="email"]')).toHaveValue("admin@taxflowapp.com");

  await page.locator('input[name="password"]').fill("admin123");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.getByText("TaxFlow app loaded")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("Dashboard").first()).toBeVisible();
});

test("wrong login shows a useful error", async ({ page }) => {
  await page.goto("/");

  await page.locator('input[name="email"]').fill("admin@taxflowapp.com");
  await page.locator('input[name="password"]').fill("wrong-password");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.getByText(/Could not sign in\. Use admin@taxflowapp\.com \/ admin123/)).toBeVisible();
});

test("core legacy navigation is available after login", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[name="password"]').fill("admin123");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.getByText("TaxFlow app loaded")).toBeVisible({ timeout: 20_000 });
  await page.getByText("Sales & Invoices").first().click();
  await expect(page.getByText("Sales", { exact: false }).first()).toBeVisible();

  await page.getByText("Purchases").first().click();
  await expect(page.getByText("Purchase", { exact: false }).first()).toBeVisible();
});

test("dashboard shortcuts open purchase upload and reports before adding an invoice", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[name="password"]').fill("admin123");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.getByText("TaxFlow app loaded")).toBeVisible({ timeout: 20_000 });

  await page.getByRole("button", { name: "Upload Purchase" }).click();
  await expect(page.locator("#page-purchase")).toHaveClass(/on/);
  await expect(page.locator("#p-upload")).toHaveClass(/on/);

  await page.getByText("Dashboard").first().click();
  await page.getByRole("button", { name: "Open Reports" }).click();
  await expect(page.locator("#page-reports")).toHaveClass(/on/);

  await page.getByText("Sales & Invoices").first().click();
  await page.getByText("Create Invoice").click();
  const invoiceNo = `E2E-${Date.now()}`;
  await page.locator("#inv-no").fill(invoiceNo);
  await page.locator("#inv-cust").fill("E2E Customer LLC");
  await page.locator("#inv-lines .inv-product").first().fill("E2E Consulting");
  await page.locator("#inv-lines .inv-price").first().fill("100");
  await page.getByRole("button", { name: /Save Draft/ }).click();

  await expect(page.locator("#sales-invoice-tbody")).toContainText(invoiceNo);
});
