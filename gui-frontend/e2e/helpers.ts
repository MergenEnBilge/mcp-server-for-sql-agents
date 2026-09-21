import { expect, type Page } from "@playwright/test";

const password = process.env.KEYCLOAK_DEV_USER_PASSWORD ?? "";

/** Sign in through Keycloak's real login page, the way a person does. */
export async function signIn(page: Page, username: "alice" | "bob" | "carol", path = "/") {
  expect(password, "KEYCLOAK_DEV_USER_PASSWORD must be set").not.toBe("");
  await page.goto(path);
  await page.locator("input[name=username]").fill(username);
  await page.locator("input[name=password]").fill(password);
  await page.locator("input[type=submit], button[type=submit]").first().click();
  await expect(page.locator(".rail")).toBeVisible();
}
