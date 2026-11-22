export function financeActions({ verified, has }) {
  const actions = [];
  if (verified) actions.push({ id: "club-finances-mine", label: "My club's finances", view: "clubFinances" });
  if (has("finance")) actions.push({ id: "club-finances-admin", label: "Approve expenses and grants", view: "clubFinanceAdmin" });
  return actions;
}

export function registrationActions({ verified, has }) {
  const actions = [];
  if (verified) actions.push({ id: "club-register", label: "Register or manage a club", view: "clubRegistration" });
  if (has("clubs")) actions.push({ id: "club-review", label: "Review registrations", view: "clubAdmin" });
  return actions;
}
