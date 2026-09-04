import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      <p className="text-4xl font-bold text-fg-dim">404</p>
      <p className="text-fg-muted">הדף המבוקש לא נמצא.</p>
      <Link to="/" className="text-accent hover:underline">
        חזרה לדף הבוקר
      </Link>
    </div>
  );
}
