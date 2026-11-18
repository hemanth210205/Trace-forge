/** Traceforge mark: three offset bars, read as spans in a trace waterfall, on a rounded lime tile. */
export function BrandMark({ className = "h-6 w-6" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden className={className}>
      <rect width="24" height="24" rx="6" fill="#B8FF00" />
      <rect x="5" y="6" width="11.5" height="2.6" rx="1.3" fill="#09090B" />
      <rect x="7.5" y="10.7" width="11.5" height="2.6" rx="1.3" fill="#09090B" />
      <rect x="10.5" y="15.4" width="6" height="2.6" rx="1.3" fill="#09090B" />
      <circle cx="19.5" cy="19.5" r="2" fill="#00E5FF" />
    </svg>
  );
}
