import { ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import type { SecurityStatus } from "@/types/api";

export function SecurityStatusIcon({ status }: { status: SecurityStatus }) {
  if (status === "clean") {
    return (
      <span title="בדיקת אבטחה: תקין" aria-label="בדיקת אבטחה: תקין">
        <ShieldCheck size={14} className="text-ok" aria-hidden="true" />
      </span>
    );
  }
  if (status === "quarantined") {
    return (
      <span title="בהסגר — נבדק ידנית" aria-label="בהסגר — נבדק ידנית">
        <ShieldQuestion size={14} className="text-warn" aria-hidden="true" />
      </span>
    );
  }
  return (
    <span title="סומן כחשוד (הזרקת הנחיות)" aria-label="סומן כחשוד">
      <ShieldAlert size={14} className="text-danger" aria-hidden="true" />
    </span>
  );
}
