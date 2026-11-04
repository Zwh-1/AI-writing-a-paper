// Minimal Node config typing; the browser bundle does not import node:fs.
declare module "node:fs" {
  export function readFileSync(path: URL, encoding: "utf8"): string;
}
