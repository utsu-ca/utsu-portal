import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/svelte";
import OrdersView from "../views/OrdersView.svelte";
import PaymentOrdersView from "../views/PaymentOrdersView.svelte";

vi.mock("../../../lib/api.js", async importOriginal => {
  const api = vi.fn();
  return {
    ...(await importOriginal()),
    api,
    apiAllPages: async path => {
      const [pathname, query = ""] = path.split("?", 2);
      const params = new URLSearchParams(query);
      params.set("limit", "100");
      const items = [];
      let cursor = "";
      do {
        if (cursor) params.set("cursor", cursor);
        const page = await api(`${pathname}?${params}`);
        items.push(...page.items);
        cursor = page.next_cursor;
      } while (cursor);
      return items;
    },
  };
});
import { api } from "../../../lib/api.js";

afterEach(() => { cleanup(); api.mockReset(); });
const order = { id: "order-1", uuid: "student-1", description: "Membership", amount_cents: 1500, currency: "cad", status: "paid" };

it("shows only server-confirmed payment status and refreshes after returning from Stripe", async () => {
  api.mockResolvedValueOnce({ items: [{ ...order, status: "processing" }], next_cursor: "next-page" })
    .mockResolvedValueOnce({
      items: [{ ...order, id: "order-2", description: "Donation", status: "processing" }],
      next_cursor: null,
    })
    .mockResolvedValueOnce({ items: [order], next_cursor: null });
  render(OrdersView);
  expect(await screen.findByText("Donation")).toBeInTheDocument();
  expect(screen.getAllByText("processing")).toHaveLength(2);
  expect(api).toHaveBeenCalledWith("/orders?limit=100&cursor=next-page");
  expect(screen.queryByRole("button", { name: "Pay with Stripe" })).not.toBeInTheDocument();
  await fireEvent.click(screen.getByRole("button", { name: "Refresh orders" }));
  expect(await screen.findByText("paid")).toBeInTheDocument();
});

it("refuses to navigate to a checkout URL outside Stripe", async () => {
  api.mockResolvedValueOnce({ items: [{ ...order, status: "awaiting_payment" }], next_cursor: null })
    .mockResolvedValueOnce({ url: "https://attacker.example/pay" });
  render(OrdersView);
  await fireEvent.click(await screen.findByRole("button", { name: "Pay with Stripe" }));
  expect(await screen.findByText(/invalid checkout link/)).toBeInTheDocument();
  expect(api).toHaveBeenCalledWith("/orders/order-1/checkout", { method: "POST" });
});

it("keeps the same request id when order creation has an uncertain outcome", async () => {
  let posts = 0;
  api.mockImplementation(async (path, opts) => {
    if (opts?.method === "POST") {
      if (++posts === 1) throw Object.assign(new Error("Provider unavailable"), { status: 502 });
      return order;
    }
    return { items: [], next_cursor: null };
  });
  render(PaymentOrdersView);
  const profile = screen.getByLabelText("Profile UUID");
  await fireEvent.input(profile, { target: { value: "student-1" } });
  await fireEvent.input(screen.getByLabelText("Description"), { target: { value: "Membership" } });
  await fireEvent.input(screen.getByLabelText(/Amount/), { target: { value: "1500" } });
  await fireEvent.submit(profile.closest("form"));
  expect(await screen.findByText("Provider unavailable")).toBeInTheDocument();
  expect(profile).toBeDisabled();
  await fireEvent.submit(profile.closest("form"));
  await screen.findByText("Order created.");
  const bodies = api.mock.calls.filter(([, opts]) => opts?.method === "POST").map(([, opts]) => opts.body);
  expect(bodies).toHaveLength(2);
  expect(bodies[0]).toEqual(bodies[1]);
  expect(bodies[0].amount_cents).toBe(1500);
});
