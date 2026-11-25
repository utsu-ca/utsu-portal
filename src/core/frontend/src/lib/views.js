import { viewContributions } from "virtual:portal-programs";

const coreViews = {
  account: () => import("../views/AccountView.svelte"),
  adminFees: () => import("../features/fees/views/AdminFeesView.svelte"),
  adminForms: () => import("../views/AdminForms.svelte"),
  adminMembers: () => import("../views/AdminMembers.svelte"),
  adminRoles: () => import("../views/AdminRoles.svelte"),
  adminContent: () => import("../views/AdminContent.svelte"),
  adminRoster: () => import("../views/AdminRoster.svelte"),
  adminVerifications: () => import("../views/AdminVerifications.svelte"),
  auth: () => import("../views/AuthView.svelte"),
  banking: () => import("../features/profile/views/BankingView.svelte"),
  feeOptOut: () => import("../features/fees/views/FeeOptOutView.svelte"),
  feeRefunds: () => import("../features/fees/views/FeeRefundsView.svelte"),
  form: () => import("../views/FormView.svelte"),
  formEditor: () => import("../views/FormEditor.svelte"),
  home: () => import("../views/HomeView.svelte"),
  levyGroups: () => import("../features/fees/views/LevyGroupView.svelte"),
  orders: () => import("../features/payments/views/OrdersView.svelte"),
  paymentOrders: () => import("../features/payments/views/PaymentOrdersView.svelte"),
  profile: () => import("../views/ProfileView.svelte"),
  program: () => import("../views/ProgramPage.svelte"),
  recovery: () => import("../views/RecoveryView.svelte"),
  register: () => import("../views/RegisterView.svelte"),
  status: () => import("../views/StatusView.svelte"),
};

export function combineContributions(kind, ...contributions) {
  const result = Object.create(null);
  for (const contribution of contributions) {
    for (const [name, loader] of Object.entries(contribution)) {
      if (Object.hasOwn(result, name)) throw new Error(`Duplicate ${kind}: ${name}`);
      result[name] = loader;
    }
  }
  return result;
}

export const combineViews = (...contributions) => combineContributions("view", ...contributions);

// Each view is a lazily loaded chunk.
export const views = combineViews(coreViews, ...viewContributions);
