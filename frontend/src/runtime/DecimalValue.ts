// Exact decimal arithmetic for the bounded document expression contract.
export class DecimalValue {
  constructor(public coefficient: bigint, public scale = 0) {
    if (scale > 36 || coefficient.toString().replace('-', '').length > 40)
      throw new Error('Calculation exceeds the supported range');
  }
  static from(value: any): DecimalValue {
    if (value instanceof DecimalValue) return value;
    if (value == null || typeof value === 'boolean' || (typeof value === 'number' && !Number.isSafeInteger(value)))
      throw new Error('A valid calculation input is required');
    const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(String(value));
    if (!match) throw new Error('Use an exact decimal value');
    return new DecimalValue(BigInt(match[1] + match[2] + (match[3] || '')), (match[3] || '').length);
  }
  align(other: DecimalValue) {
    const scale = Math.max(this.scale, other.scale);
    return [this.coefficient * 10n ** BigInt(scale - this.scale), other.coefficient * 10n ** BigInt(scale - other.scale), scale] as const;
  }
  compare(other: any) {
    const [a, b] = this.align(DecimalValue.from(other));
    return a === b ? 0 : a < b ? -1 : 1;
  }
  fixed(scale: number): string {
    const coefficient = this.scale > scale
      ? rounded(this.coefficient, 10n ** BigInt(this.scale - scale))
      : this.coefficient * 10n ** BigInt(scale - this.scale);
    if (coefficient > 9223372036854775807n || coefficient < -9223372036854775807n)
      throw new Error('Decimal is outside the supported range');
    const digits = (coefficient < 0 ? -coefficient : coefficient).toString().padStart(scale + 1, '0');
    return (coefficient < 0 ? '-' : '') + (scale ? digits.slice(0, -scale) + '.' + digits.slice(-scale) : digits);
  }
}
function rounded(numerator: bigint, denominator: bigint) {
  if (denominator === 0n) throw new Error('Division by zero');
  const negative = (numerator < 0n) !== (denominator < 0n);
  const a = numerator < 0n ? -numerator : numerator;
  const b = denominator < 0n ? -denominator : denominator;
  let quotient = a / b;
  const remainder = a % b;
  if (remainder * 2n > b || (remainder * 2n === b && quotient % 2n !== 0n)) quotient++;
  return negative ? -quotient : quotient;
}
export function decimalOperation(op: string, left: any, right?: any): DecimalValue {
  const a = DecimalValue.from(left);
  if (op === 'neg') return new DecimalValue(-a.coefficient, a.scale);
  const b = DecimalValue.from(right);
  if (op === 'mul') return new DecimalValue(a.coefficient * b.coefficient, a.scale + b.scale);
  if (op === 'div') {
    const exponent = 18 + b.scale - a.scale;
    return new DecimalValue(rounded(a.coefficient * 10n ** BigInt(Math.max(0, exponent)), b.coefficient * 10n ** BigInt(Math.max(0, -exponent))), 18);
  }
  const [x, y, scale] = a.align(b);
  return new DecimalValue(op === 'add' ? x + y : x - y, scale);
}
