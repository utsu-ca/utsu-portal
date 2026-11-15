<script>
  import { onMount } from "svelte";
  import { getToken } from "../lib/api.js";

  // Hands the signed-in portal session to an SSO client (Zoom): authorise, then follow the redirect.
  let message = $state("Signing you in…");

  onMount(async () => {
    const q = new URLSearchParams(location.search);
    if (!getToken()) {
      sessionStorage.setItem("sso_next", location.pathname + location.search);
      message = "Please sign in to the portal first; redirecting…";
      setTimeout(() => { location.href = "/"; }, 800);
      return;
    }
    const body = {
      client_id: q.get("client_id") || "", redirect_uri: q.get("redirect_uri") || "", state: q.get("state") || "",
      nonce: q.get("nonce") || "", code_challenge: q.get("code_challenge") || "",
      code_challenge_method: q.get("code_challenge_method") || "S256"
    };
    const response = await fetch("/api/sso/authorize", { method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + getToken() }, body: JSON.stringify(body) });
    const result = await response.json().catch(() => ({}));
    if (response.ok) location.replace(result.redirect);
    else message = typeof result.detail === "string" ? result.detail : "Sign-on was refused.";
  });
</script>

<main>
  <div class="card"><h2>UTSU single sign-on</h2><p>{message}</p></div>
</main>
