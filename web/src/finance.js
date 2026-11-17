import { mountPage } from "./standalone.js";
import Page from "./features/sabp/views/Finance.svelte";

mountPage(Page, { staff: true });
