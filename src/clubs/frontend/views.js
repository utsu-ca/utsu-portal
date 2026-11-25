export const views = {
  clubAdmin: () => import("./views/ClubAdminView.svelte"),
  clubDirectory: () => import("./views/ClubDirectoryView.svelte"),
  clubFinanceAdmin: () => import("./views/ClubFinanceAdminView.svelte"),
  clubFinances: () => import("./views/ClubFinancesView.svelte"),
  clubMembers: () => import("./views/ClubMembersView.svelte"),
  clubMembership: () => import("./views/ClubMembershipView.svelte"),
  clubRegistration: () => import("./views/ClubRegistrationView.svelte"),
};
