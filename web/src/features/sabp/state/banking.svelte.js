import { api } from "../../../lib/api.js";
import { session } from "../../../lib/session.svelte.js";

export const bankingStatus = $state({ status: null, error: "", busy: false });

let requestId = 0;
let pending = null;
let owner = null;

export function clearBankingStatus() {
  requestId++;
  pending = null;
  owner = null;
  bankingStatus.status = null;
  bankingStatus.error = "";
  bankingStatus.busy = false;
}

export function loadBankingStatus({ force = false } = {}) {
  const token = session.token;
  const account = session.me;
  if (!token || account?.status !== "verified") {
    clearBankingStatus();
    return Promise.resolve(null);
  }
  if (owner?.token !== token || owner.account !== account) {
    clearBankingStatus();
    owner = { token, account };
  }
  if (!force && bankingStatus.status) return Promise.resolve(bankingStatus.status);
  if (pending?.token === token && pending.account === account) return pending.promise;

  const id = ++requestId;
  bankingStatus.status = null;
  bankingStatus.error = "";
  bankingStatus.busy = true;
  const isCurrent = () => requestId === id && session.token === token && session.me === account;
  const promise = api("/sap/banking").then(status => {
    if (isCurrent()) bankingStatus.status = status;
    return status;
  }).catch(error => {
    if (isCurrent()) bankingStatus.error = error.message;
    throw error;
  }).finally(() => {
    if (requestId === id) {
      pending = null;
      bankingStatus.busy = false;
    }
  });
  pending = { token, account, promise };
  return promise;
}

export async function refreshBankingStatus() {
  try {
    return await loadBankingStatus();
  } catch {
    // The feature status component presents the failure without blocking the shell.
    return null;
  }
}
