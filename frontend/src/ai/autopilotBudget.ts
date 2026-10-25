// An editable round cap must never round above the amount the server can reserve.
export function availableBudget(limit: number, available: number) {
  if (!Number.isFinite(limit) || !Number.isFinite(available)) return 0;
  return Math.max(0, Math.floor((Math.min(limit, available) + 1e-9) * 100) / 100);
}
