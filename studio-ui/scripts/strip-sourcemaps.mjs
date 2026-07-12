import { readdir, readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../src/ledgerline/data/studio/assets/", import.meta.url));
const files = await readdir(root);

await Promise.all(
  files.map(async (name) => {
    const path = join(root, name);
    if (name.endsWith(".map")) {
      await rm(path);
      return;
    }
    if (name.endsWith(".js")) {
      const source = await readFile(path, "utf8");
      await writeFile(path, source.replace(/\n\/\/# sourceMappingURL=.*?\.map\s*$/u, "\n"));
    }
  }),
);
