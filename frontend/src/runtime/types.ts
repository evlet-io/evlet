export interface FieldSchema {
  name: string;
  label: string;
  type: string;
  required: boolean;
  read_only?: boolean;
  hidden?: boolean;
  default_kind: "none" | "static" | "callable";
  default?: any;
  choices?: string[];
  target?: string;
  max_length?: number;
  min?: number;
  max?: number;
  scale?: number;
  help_text?: string;
  placeholder?: string;
}
export interface ModelSchema {
  name: string;
  label: string;
  plural: string;
  title_field: string;
  fields: FieldSchema[];
  list_fields: string[];
  search_fields: string[];
  layout?: { label: string; fields: string[] }[];
  child_only?: boolean;
  permissions?: { create: boolean; update: boolean; delete: boolean };
  attachments?: boolean;
  children?: { name: string; model: string; link: string; label: string }[];
  actions?: { name: string; label: string }[];
}
export interface NodeSchema {
  id: string;
  type: string;
  version: number;
  props: Record<string, any>;
  bindings: Record<string, string>;
  events: Record<string, Operation[]>;
  slots: Record<string, NodeSchema[]>;
}
export interface Operation {
  op: string;
  [key: string]: any;
}
export interface ViewSchema {
  version: number;
  name: string;
  kind: string;
  model?: string;
  route?: string;
  title: string;
  subtitle: string;
  icon: string;
  section: string;
  layout: NodeSchema;
  state: Record<string, any>;
  computed: Record<string, any>;
  options: Record<string, any>;
  dependencies: Record<string, string[]>;
}
export interface ComponentContract {
  name: string;
  category: string;
  props: Record<string, string>;
  defaults: Record<string, any>;
  events: string[];
  bindings: string[];
  slots: string[];
  renderer: string;
  version: number;
  description: string;
  accessibility: string;
  example: string;
}
export interface UIDocument {
  version: number;
  name: string;
  views: ViewSchema[];
  components: ComponentContract[];
  extensions: { name: string; layer: string; target: string }[];
}
export interface User {
  id: number;
  username: string;
  role: string;
}
export type RecordData = {
  id: number;
  children?: Record<string, any[]>;
  [key: string]: any;
};
