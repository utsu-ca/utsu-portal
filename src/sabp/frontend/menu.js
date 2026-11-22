export function bursaryActions({ verified, has }) {
  const actions = [];
  if (verified) actions.push(
    { id: "bursary-apply", label: "Apply for a bursary", view: "form", props: { id: "student_aid_bursary" } },
    { id: "bursary-banking", label: "Banking information", view: "sapBanking", props: { bankingView: true } },
  );
  if (has("sap_reviewer")) actions.push({ id: "bursary-review", label: "Review applications", view: "sapReview" });
  if (has("sap_admin")) actions.push({ id: "bursary-admin", label: "Administer applications", view: "sapAdmin" });
  if (has("sap_finance")) actions.push({ id: "bursary-finance", label: "Finance portal (banking)", href: "/static/finance.html" });
  return actions;
}
