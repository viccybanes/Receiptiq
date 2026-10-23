import type { ReceiptData } from './types'

/** Multiply decimal strings and round to cents without binary floating point. */
export function amount(quantity: string, price: string): string | null {
  if (![quantity, price].every(value => /^-?\d{1,12}(?:\.\d{1,12})?$/.test(value))) return null
  const parts = [quantity, price].map(value => ({
    value: BigInt(value.replace('.', '')), scale: value.split('.')[1]?.length || 0,
  }))
  const product = parts[0].value * parts[1].value
  const divisor = 10n ** BigInt(parts[0].scale + parts[1].scale)
  const absolute = product < 0n ? -product : product
  const cents = (absolute * 100n + divisor / 2n) / divisor
  return (product < 0n && cents ? '-' : '') + cents / 100n + '.' + String(cents % 100n).padStart(2, '0')
}

function decimal(value: string): bigint | null {
  if (!/^-?\d{1,12}(?:\.\d{1,6})?$/.test(value)) return null
  const [whole, fraction = ''] = value.replace('-', '').split('.')
  return BigInt(whole + fraction.padEnd(6, '0')) * (value.startsWith('-') ? -1n : 1n)
}
function format(value: bigint, places: number): string {
  const divisor = 10n ** BigInt(places)
  const absolute = value < 0n ? -value : value
  return (value < 0n ? '-' : '') + absolute / divisor + '.' + String(absolute % divisor).padStart(places, '0')
}
function divide(numerator: string, denominator: string, places: number): string | null {
  const a = decimal(numerator), b = decimal(denominator)
  if (a === null || b === null || b === 0n) return null
  if (places === 0) return a % b === 0n ? String(a / b) : null
  const sign = (a < 0n) !== (b < 0n) ? -1n : 1n
  const absA = a < 0n ? -a : a, absB = b < 0n ? -b : b
  return format(sign * ((absA * 10n ** BigInt(places) + absB / 2n) / absB), places)
}
function sum(data: ReceiptData): string | null {
  if (!data.items.length) return null
  const values = data.items.map(item => decimal(item.line_total))
  if (values.some(value => value === null)) return null
  const total = values.reduce<bigint>((result, value) => result + value!, 0n)
  return amount(format(total, 6), '1')
}
function equal(a: string, b: string | null): boolean {
  return b !== null && decimal(a) !== null && decimal(a) === decimal(b)
}

/** Fill missing arithmetic; preserve differing written amounts and totals (discounts). */
export function completeAmounts(data: ReceiptData, previous?: ReceiptData): ReceiptData {
  const items = data.items.map(item => {
    const next = { ...item }, old = previous?.items.find(value => value.id === item.id)
    if (/^-?\d+\.0+$/.test(next.quantity)) next.quantity = next.quantity.split('.')[0]
    const amountEdited = old && next.line_total !== old.line_total
    const factorsEdited = old && (next.quantity !== old.quantity || next.unit_price !== old.unit_price)
    if (amountEdited && next.line_total && next.unit_price && next.quantity === old.quantity) {
      next.quantity = divide(next.line_total, next.unit_price, 0) ?? next.quantity
    } else if (!next.quantity && next.line_total && next.unit_price) {
      next.quantity = divide(next.line_total, next.unit_price, 0) ?? ''
    }
    if (!next.unit_price && next.line_total && /^-?\d+$/.test(next.quantity)) {
      next.unit_price = divide(next.line_total, next.quantity, 2) ?? ''
    }
    if (!next.line_total || (factorsEdited && equal(old.line_total, amount(old.quantity, old.unit_price)))) {
      if (/^-?\d+$/.test(next.quantity)) next.line_total = amount(next.quantity, next.unit_price) ?? next.line_total
    }
    return next
  })
  const next = { ...data, items }
  if (!next.total || (previous && next.total === previous.total && equal(previous.total, sum(previous)))) {
    next.total = sum(next) ?? ''
  }
  return next
}
