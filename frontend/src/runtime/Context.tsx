import { createContext, useContext, type Accessor } from "solid-js";
import type { UIDocument, ModelSchema, User } from "./types";
import type { createRouter } from "./Router";
export interface ApplicationContext {
  ui: UIDocument;
  models: ModelSchema[];
  user: User;
  tenant: string;
  router: ReturnType<typeof createRouter>;
  notify: (message: string) => void;
  refresh: Accessor<number>;
  applicationState?: Record<string, any>;
  capabilities: { events?: boolean; jobs?: boolean };
}
export const AppContext = createContext<ApplicationContext>();
export function useApp() {
  const value = useContext(AppContext);
  if (!value) throw new Error("Application context is missing");
  return value;
}
export const messageFor = (error: any) =>
  [error?.message, ...Object.values(error?.fields || {})]
    .filter(Boolean)
    .join(" ");
