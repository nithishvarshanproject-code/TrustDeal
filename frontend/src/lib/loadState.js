// What a panel shows while its data loads: "loading" (spinner / skeleton), "failed" (the error, and no
// spinner) or "ready". Never a spinner and an error at the same time.
export const loadState = (data, failed) => (data != null ? "ready" : failed ? "failed" : "loading");

export const LOAD_FAILED = "Could not load this. Please try again.";
