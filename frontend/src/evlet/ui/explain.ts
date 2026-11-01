// Turn the model's expressions (as /api/schema sends them) into short readable text, so the UI
// can say why: "= round(qty * rate, 2)" for a calculated value, "Locked when Stage is won" for a
// rule, "from product" for an initial value. Text only; nothing here evaluates.

import type { Expression, FieldSchema } from '../model/schema';

type Node = Record<string, any>;
export type LabelOf = (name: string) => string;

const INFIX: Record<string, [string, number]> = {
  or: ['or', 1], and: ['and', 2],
  eq: ['=', 3], ne: ['!=', 3], lt: ['<', 3], lte: ['<=', 3], gt: ['>', 3], gte: ['>=', 3],
  add: ['+', 4], sub: ['-', 4], date_add: ['+', 4], date_sub: ['-', 4], date_diff: ['-', 4],
  mul: ['*', 5], div: ['/', 5],
};

function literal(node: Node): string {
  if (node.value === null || node.value === undefined) return 'empty';
  if (node.type === 'text' || node.type === 'date') return `"${String(node.value)}"`;
  return String(node.value);
}

/** The expression as formula text: round(qty * rate, 2), issued + days(customer.credit_days). */
export function formula(expression: Expression | undefined, label: LabelOf = name => name): string {
  const visit = (node: Node, outer = 0): string => {
    if (!node || typeof node !== 'object') return '?';
    const op = node.$expr;
    const args: Node[] = node.args ?? [];
    if (op === 'literal') return literal(node);
    if (op === 'ref') return node.scope === 'parent' ? `parent.${label(node.field)}` : label(node.field);
    if (op === 'hop') return [label(node.field), ...(node.steps ?? []).map((step: string[]) => step[1])].join('.');
    if (op === 'today') return 'today()';
    if (op === 'aggregate') return node.kind === 'count' ? `${node.table}.count()` : `${node.table}.${node.kind}("${node.field}")`;
    if (op === 'bool_to_decimal') return visit(args[0], outer);
    if (op === 'neg') return `-${visit(args[0], 6)}`;
    if (op === 'not') return `not ${visit(args[0], 6)}`;
    if (op === 'round') return `round(${visit(args[0])}, ${visit(args[1])})`;
    if (op === 'coalesce' || op === 'when' || op === 'safe_divide') return `${op}(${args.map(arg => visit(arg)).join(', ')})`;
    const infix = INFIX[op];
    if (infix) {
      const [symbol, level] = infix;
      const right = op === 'date_add' || op === 'date_sub' ? `days(${visit(args[1])})` : visit(args[1], level + 1);
      const text = `${visit(args[0], level)} ${symbol} ${right}`;
      return level < outer ? `(${text})` : text;
    }
    return String(op ?? '?');
  };
  return expression ? visit(expression as Node) : '';
}

const WORDS: Record<string, string> = { eq: 'is', ne: 'is not', lt: 'is before', lte: 'is on or before', gt: 'is after', gte: 'is on or after' };

/** A rule as a phrase: "Stage is lost", "Stage is lost or Amount > 1000". */
export function phrase(expression: Expression | undefined, label: LabelOf = name => name): string {
  const visit = (node: Node): string => {
    if (!node) return '';
    const op = node.$expr, args: Node[] = node.args ?? [];
    if ((op === 'and' || op === 'or') && args.length) return args.map(visit).join(` ${op} `);
    if (op === 'not') return `not (${visit(args[0])})`;
    if (WORDS[op] && args[1]?.$expr === 'literal') {
      if (args[1].value === null) return `${formula(args[0], label)} ${op === 'eq' ? 'is empty' : 'is set'}`;
      const value = args[1].type === 'text' ? String(args[1].value) : literal(args[1]);
      const word = args[1].type === 'date' || args[1].type === 'datetime' ? WORDS[op] : op === 'eq' ? 'is' : op === 'ne' ? 'is not' : INFIX[op][0];
      return `${formula(args[0], label)} ${word} ${value}`;
    }
    return formula(node, label);
  };
  return visit(expression as Node);
}

/** The field an initial value comes from: "from product" for initial=product.price. */
export function source(expression: Expression | undefined, label: LabelOf = name => name): string | null {
  const find = (node: Node): string | null => {
    if (!node || typeof node !== 'object') return null;
    if (node.$expr === 'hop' || node.$expr === 'ref') return label(node.field);
    for (const arg of node.args ?? []) { const found = find(arg); if (found) return found; }
    return null;
  };
  return expression ? find(expression as Node) : null;
}

/** Labels of a model's fields, lower-cased for use inside sentences. */
export function labeller(fields: FieldSchema[]): LabelOf {
  const labels = new Map(fields.map(field => [field.name, field.label]));
  return name => labels.get(name) ?? name.replace(/_/g, ' ');
}
