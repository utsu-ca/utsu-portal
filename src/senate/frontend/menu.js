export function navigation({ verified, has }) {
  const actions = [];
  if (verified) actions.push(
    { id: "senate-apply", label: "Apply for a seat", view: "form", props: { id: "senate_application" } },
    { id: "senate-mine", label: "My application", view: "mySenate" },
  );
  if (has("senate_admin")) actions.push({ id: "senate-admin", label: "Administer seating", view: "senateAdmin" });
  return actions;
}
