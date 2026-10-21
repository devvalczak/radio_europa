import type { ModuleDef } from "@jarvis/sdk";

// Every folder in /modules with a frontend/index.tsx is bundled; the backend
// decides which of them are actually enabled (GET /api/modules).
const found = import.meta.glob<{ default: ModuleDef }>("../../modules/*/frontend/index.tsx", { eager: true });

export const frontendModules: Record<string, ModuleDef> = Object.fromEntries(
  Object.values(found).map((m) => [m.default.id, m.default]),
);
