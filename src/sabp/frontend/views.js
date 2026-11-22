export const views = {
  sapAdmin: () => import("./views/SapAdminView.svelte"),
  sapBanking: () => import("./views/SapBankingView.svelte"),
  sapReview: () => import("./views/SapReviewView.svelte"),
  sapReviewDetail: () => import("./views/SapReviewDetail.svelte"),
};
