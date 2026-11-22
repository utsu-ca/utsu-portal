import { publicViewContributions } from "virtual:portal-programs";
import { combineContributions } from "./views.js";

export const publicViews = combineContributions("public view", ...publicViewContributions);
