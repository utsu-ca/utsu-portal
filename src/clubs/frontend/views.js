export const views = {
  clubAdmin: () => import("./views/ClubAdminView.svelte"),
  clubDirectory: () => import("./views/ClubDirectoryView.svelte"),
  clubFinanceAdmin: () => import("./views/ClubFinanceAdminView.svelte"),
  clubFinances: () => import("./views/ClubFinancesView.svelte"),
  clubRegistration: () => import("./views/ClubRegistrationView.svelte"),
};
