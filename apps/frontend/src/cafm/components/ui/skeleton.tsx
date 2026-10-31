"use client";

import { cn } from "@/utils";

/**
 * Shared loading skeleton primitive — one consistent shimmer used across panels instead of
 * the previous mix of spinners / plain "Loading…" text / blank states. Use `Skeleton` for a
 * single bar/block, or the `SkeletonText` / `SkeletonPanel` compositions for common shapes.
 *
 * Rule of thumb: use a skeleton when CONTENT is loading (a fetch fills the same shape); keep a
 * Spinner only for an in-flight PROCESS with no content shape yet (e.g. "running…").
 */
export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden
      className={cn("animate-pulse rounded-md bg-slate-200/70", className)}
      {...props}
    />
  );
}

/** A stack of shimmer lines, last line shortened — for paragraph/list-shaped content. */
export function SkeletonText({
  lines = 3,
  className,
}: {
  lines?: number;
  className?: string;
}) {
  return (
    <div role="status" aria-busy="true" aria-label="Loading" className={cn("space-y-2", className)}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} className={cn("h-3", i === lines - 1 ? "w-2/3" : "w-full")} />
      ))}
    </div>
  );
}

/** Generic panel-loading state: a small header bar + a few content rows, in a bordered card.
 *  Drop-in replacement for "Loading …" text / lone spinners at the top of a panel's content. */
export function SkeletonPanel({
  rows = 4,
  className,
}: {
  rows?: number;
  className?: string;
}) {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label="Loading"
      className={cn("rounded-xl border border-slate-200 bg-white p-4 space-y-3", className)}
    >
      <div className="flex items-center gap-2">
        <Skeleton className="h-4 w-4 rounded-full" />
        <Skeleton className="h-3 w-32" />
      </div>
      <div className="space-y-2">
        {Array.from({ length: rows }).map((_, i) => (
          <Skeleton key={i} className={cn("h-3", i % 3 === 2 ? "w-1/2" : "w-full")} />
        ))}
      </div>
    </div>
  );
}
