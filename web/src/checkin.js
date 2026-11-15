import { mountPage } from "./standalone.js";
import Page from "./pages/Checkin.svelte";

mountPage(Page, { staff: true });
