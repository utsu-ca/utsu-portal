import { mountPage } from "./standalone.js";
import Page from "./pages/Finance.svelte";

mountPage(Page, { staff: true });
