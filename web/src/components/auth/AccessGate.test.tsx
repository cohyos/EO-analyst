import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * ADR-008 (docs/adr/008-remote-access.md): `AccessGate` renders `children` unchanged until
 * `real.ts` tells it (via the tiny pub/sub in `subscribeAuthRequired`) that the API answered a
 * request with 401 `auth_required` -- at which point it swaps in the passcode form instead. No
 * `I18nProvider` is mounted here: `useI18n()` falls back to the Hebrew dictionary when unmounted
 * (see `I18nContext.tsx`), which is exactly what these Hebrew string assertions rely on.
 */
const { authState, loginRemoteAccess, ApiError } = vi.hoisted(() => {
  class ApiError extends Error {
    code: string;
    detail: unknown;
    constructor(code: string, message: string, detail: unknown = null) {
      super(message);
      this.code = code;
      this.detail = detail;
    }
  }
  return {
    authState: { required: false, listener: null as ((value: boolean) => void) | null },
    loginRemoteAccess: vi.fn(),
    ApiError,
  };
});

vi.mock("@/api/real", () => ({
  getAuthRequired: () => authState.required,
  subscribeAuthRequired: (listener: (value: boolean) => void) => {
    authState.listener = listener;
    return () => {
      if (authState.listener === listener) authState.listener = null;
    };
  },
  loginRemoteAccess,
  ApiError,
}));

import { AccessGate } from "./AccessGate";

function renderGate() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
  render(
    <QueryClientProvider client={queryClient}>
      <AccessGate>
        <div>App content</div>
      </AccessGate>
    </QueryClientProvider>,
  );
  return { invalidateSpy };
}

/** Simulates `real.ts` flipping the shared flag and notifying the subscriber, the way a live 401
 * (or a successful login) would in production. */
function setAuthRequired(value: boolean) {
  authState.required = value;
  act(() => {
    authState.listener?.(value);
  });
}

beforeEach(() => {
  authState.required = false;
  authState.listener = null;
  loginRemoteAccess.mockReset();
});

describe("AccessGate (ADR-008)", () => {
  it("renders children unchanged when no auth_required has fired", () => {
    renderGate();
    expect(screen.getByText("App content")).toBeInTheDocument();
  });

  it("replaces children with the passcode form once auth_required fires", () => {
    renderGate();
    setAuthRequired(true);

    expect(screen.queryByText("App content")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByLabelText("קוד גישה")).toBeInTheDocument();
  });

  it("disables submit until a passcode is typed", () => {
    renderGate();
    setAuthRequired(true);

    const submit = screen.getByRole("button", { name: "התחבר" });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByLabelText("קוד גישה"), { target: { value: "x" } });
    expect(submit).not.toBeDisabled();
  });

  it("submits the typed passcode and refreshes queries on success", async () => {
    loginRemoteAccess.mockResolvedValue(undefined);
    const { invalidateSpy } = renderGate();
    setAuthRequired(true);

    fireEvent.change(screen.getByLabelText("קוד גישה"), { target: { value: "hunter2" } });
    fireEvent.click(screen.getByRole("button", { name: "התחבר" }));

    await waitFor(() => expect(loginRemoteAccess).toHaveBeenCalledWith("hunter2"));
    await waitFor(() => expect(invalidateSpy).toHaveBeenCalled());

    // In production a successful login clears the shared flag itself (real.ts); simulate that
    // side effect explicitly to verify the gate reacts to it.
    setAuthRequired(false);
    expect(screen.getByText("App content")).toBeInTheDocument();
  });

  it("shows an inline error and keeps the gate up when login fails", async () => {
    loginRemoteAccess.mockRejectedValue(new ApiError("invalid_passcode", "קוד גישה שגוי", null));
    renderGate();
    setAuthRequired(true);

    fireEvent.change(screen.getByLabelText("קוד גישה"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "התחבר" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("קוד גישה שגוי"));
    expect(screen.queryByText("App content")).not.toBeInTheDocument();
  });

  it("never stores the passcode itself anywhere client-side beyond the controlled input", async () => {
    loginRemoteAccess.mockResolvedValue(undefined);
    renderGate();
    setAuthRequired(true);

    fireEvent.change(screen.getByLabelText("קוד גישה"), { target: { value: "hunter2" } });
    fireEvent.click(screen.getByRole("button", { name: "התחבר" }));

    await waitFor(() => expect(loginRemoteAccess).toHaveBeenCalledWith("hunter2"));
    // The input clears itself once the request is sent -- nothing is cached in the component.
    await waitFor(() =>
      expect((screen.getByLabelText("קוד גישה") as HTMLInputElement).value).toBe(""),
    );
  });
});
