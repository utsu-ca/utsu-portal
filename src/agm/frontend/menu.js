export function agmActions({ verified, has, enabled }) {
  const actions = [];
  if (verified) actions.push(
    { id: "agm-rsvp", label: "RSVP for the AGM", view: "form", props: { id: "agm_rsvp" } },
    { id: "agm-invitation", label: "My invitation", view: "agm" },
  );
  if (has("agm_admin")) actions.push({ id: "agm-admin", label: "Registrations and roster", view: "agmAdmin" });
  if (enabled("agm_verify") && has("agm_id_reviewer")) {
    actions.push({ id: "agm-verifications", label: "Online ID review queue", view: "agmIdQueue" });
  }
  if (has("checkin")) actions.push({ id: "agm-checkin", label: "Check-in desk", href: "/static/checkin.html" });
  return actions;
}
