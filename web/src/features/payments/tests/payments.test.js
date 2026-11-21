import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/svelte";
import OrdersView from "../views/OrdersView.svelte";
import PaymentOrdersView from "../views/PaymentOrdersView.svelte";

vi.mock("../../../lib/api.js", async importOriginal => ({ ...(await importOriginal()), api: vi.fn() }));
import { api } from "../../../lib/api.js";

afterEach(() => { cleanup(); api.mockReset(); });
const order = { id: "order-1", uuid: "student-1", description: "Membership", amount_cents: 1500, currency: "cad", status: "paid" };

it("shows only server-confirmed payment status and refreshes after returning from Stripe", async () => {
  api.mockResolvedValueOnce([{ ...order, status: "processing" }]).mockResolvedValueOnce([order]);
  render(OrdersView);
  expect(await screen.findByText("processing")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Pay with Stripe" })).not.toBeInTheDocument();
  await fireEvent.click(screen.getByRole("button", { name: "Refresh orders" }));
  expect(await screen.findByText("paid")).toBeInTheDocument();
});

it("refuses to navigate to a checkout URL outside Stripe", async () => {
  api.mockResolvedValueOnce([{ ...order, status: "awaiting_payment" }])
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
    return [];
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
