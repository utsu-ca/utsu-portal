import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import NoticeButton from "./NoticeButton.svelte";

vi.mock("../lib/api.js", async importOriginal => ({ ...(await importOriginal()), api: vi.fn() }));
import { api } from "../lib/api.js";

afterEach(() => { cleanup(); vi.restoreAllMocks(); api.mockReset(); });

describe("status notice", () => {
  it("previews, asks for confirmation, and sends the previewed notice", async () => {
    api.mockImplementation(async path => path.endsWith("/preview")
      ? { to: "test@example.test", subject: "Subject", body: "Body", preview_hash: "hash", delivery_status: null }
      : { status: "sent" });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    render(NoticeButton, { program: "sap", uid: "student" });
    const button = screen.getByRole("button", { name: "Preview status notice" });

    await fireEvent.click(button);  // declined: nothing is sent
    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1));
    expect(confirmSpy.mock.calls[0][0]).toContain("test@example.test");
    expect(api).toHaveBeenCalledTimes(1);

    await fireEvent.click(button);
    expect(await screen.findByText("Notice sent.")).toBeInTheDocument();
    const [path, opts] = api.mock.calls.at(-1);
    expect(path).toBe("/admin/notices/sap/send");
    expect(opts.body).toEqual({ uid: "student", preview_hash: "hash", confirm: true });
  });

  it("refuses a notice that was already sent", async () => {
    api.mockResolvedValue({ to: "a@b.c", subject: "s", body: "b", preview_hash: "h", delivery_status: "unknown" });
    render(NoticeButton, { program: "sap", uid: "student" });
    await fireEvent.click(screen.getByRole("button", { name: "Preview status notice" }));
    expect(await screen.findByText("This notice was already sent or its delivery is unresolved.")).toBeInTheDocument();
    expect(api).toHaveBeenCalledTimes(1);
  });
});
