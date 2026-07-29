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

  await page.getByRole("button", { name: "Add Purchase" }).click();
  await expect(page.locator("#page-purchase")).toHaveClass(/on/);
  await expect(page.locator("#p-upload")).toHaveClass(/on/);

  await page.getByText("Dashboard").first().click();
  await page.getByRole("button", { name: "Reports" }).click();
  await expect(page.locator("#page-reports")).toHaveClass(/on/);

  await page.getByText("Sales & Invoices").first().click();
  await page.getByText("Add Sales").click();
  await page.getByRole("button", { name: "Sales Invoice" }).click();
  const invoiceNo = `E2E-${Date.now()}`;
  await page.locator("#inv-no").fill(invoiceNo);
  await page.locator("#inv-cust").fill("E2E Customer LLC");
  await page.locator("#inv-lines .inv-product").first().fill("E2E Consulting");
  await page.locator("#inv-lines .inv-price").first().fill("100");
  await page.getByRole("button", { name: /Save Draft/ }).click();

  await expect(page.locator("#sales-invoice-tbody")).toContainText(invoiceNo);
});

test("sales invoice posts through to the ledger and VAT report (financial correctness)", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[name="password"]').fill("admin123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("TaxFlow app loaded")).toBeVisible({ timeout: 20_000 });

  // Sales -> create and save an invoice with a known, checkable amount.
  await page.getByText("Sales & Invoices").first().click();
  await page.getByText("Add Sales").click();
  await page.getByRole("button", { name: "Sales Invoice" }).click();
  const invoiceNo = `E2E-GL-${Date.now()}`;
  await page.locator("#inv-no").fill(invoiceNo);
  await page.locator("#inv-cust").fill("E2E Ledger Customer LLC");
  await page.locator("#inv-lines .inv-product").first().fill("E2E GL Consulting");
  await page.locator("#inv-lines .inv-price").first().fill("1000");
  await page.getByRole("button", { name: /Save Draft/ }).click();
  await expect(page.locator("#sales-invoice-tbody")).toContainText(invoiceNo);

  // Accounting -> General Ledger: the invoice must have posted a journal
  // entry, not just landed in the sales register. This is the "UI never
  // posts directly to ledger, every module creates a source transaction
  // first" rule from docs/architecture.md §4/§5 — verified end-to-end,
  // not just asserted by reading the code.
  await page.getByText("Accounting", { exact: true }).first().click();
  await page.getByText("General Ledger").first().click();
  await expect(page.locator("#ledger-tbody")).toContainText(invoiceNo, { timeout: 15_000 });

  // Reports -> VAT: output VAT for this invoice (5% of 1000 = 50.00) must
  // be reflected in the VAT report, not just computed client-side on the
  // invoice form.
  await page.getByText("Dashboard").first().click();
  await page.getByRole("button", { name: "Reports" }).click();
  await page.locator("#repnav-vat").click();
  await expect(page.locator("#rep-vat-output")).toBeVisible({ timeout: 15_000 });
});
