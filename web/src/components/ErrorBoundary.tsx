import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
}

/**
 * Last line of defense against unguarded property access on API data (a
 * null/partial payload the specific page didn't account for). Catches any
 * render error thrown by its subtree and shows a Hebrew fallback instead of
 * blanking the whole app. Rendered around the router `<Outlet />` in
 * AppShell, keyed by the current path, so navigating away from the broken
 * page recovers it automatically.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error("Unhandled UI error caught by ErrorBoundary", error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          role="alert"
          className="flex h-full min-h-[16rem] flex-col items-center justify-center gap-3 p-8 text-center"
        >
          <AlertTriangle size={28} className="text-danger" aria-hidden="true" />
          <p className="text-lg font-semibold text-fg">משהו השתבש בטעינת המסך הזה</p>
          <p className="max-w-sm text-sm text-fg-muted">
            אירעה שגיאה בלתי צפויה בעת הצגת הנתונים. אפשר לנסות לטעון את האפליקציה מחדש.
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:opacity-90"
          >
            טען מחדש
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
