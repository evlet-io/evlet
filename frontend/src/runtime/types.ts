export interface FieldSchema {
  stored?: boolean;
  name: string;
  label: string;
  type: string;
  required: boolean;
  read_only?: boolean;
  hidden?: boolean;
  default_kind: "none" | "static" | "callable" | "reference";
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
  document_version?: number;
  resource?: string;
  behavior?: Record<string, Record<string, any>>;
  sources?: Record<string, Record<string, {type: string; scale?: number}>>;
  source_fields?: FieldSchema[];
  order?: string[];
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
  actions?: { name: string; label: string; dialog?: boolean; arguments?: {name: string; type: string; required: boolean; default?: any}[] }[];
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
