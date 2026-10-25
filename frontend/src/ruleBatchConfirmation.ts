export type RuleConfirmationResult = {
  state: "confirmed";
  updated: number;
  confirmed_ids: string[];
  failed_ids: string[];
  failures: { id: string; message: string }[];
};

export async function confirmSelectedRules(
  ids: string[],
  confirm: (id: string) => Promise<unknown>,
): Promise<RuleConfirmationResult> {
  const result: RuleConfirmationResult = {
    state: "confirmed",
    updated: 0,
    confirmed_ids: [],
    failed_ids: [],
    failures: [],
  };
  for (const id of new Set(ids)) {
    try {
      await confirm(id);
      result.confirmed_ids.push(id);
    } catch (error) {
      result.failed_ids.push(id);
      result.failures.push({
        id,
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }
  result.updated = result.confirmed_ids.length;
  return result;
}
