import { ArrowLeftRight, Check, UserCheck, X } from "lucide-react";

const ICONS = { APPROVE: Check, REJECT: X, COUNTER: ArrowLeftRight, ESCALATE: UserCheck };
const SIZES = { sm: 12, md: 16, lg: 20 };

export default function StatusBadge({ result, size = "md", animate = false }) {
  const Icon = ICONS[result] ?? Check;
  return (
    <span className={`badge ${result} ${size !== "md" ? size : ""} ${animate ? "pop" : ""}`}>
      <Icon size={SIZES[size]} strokeWidth={2.5} aria-hidden="true" />
      {result}
    </span>
  );
}
