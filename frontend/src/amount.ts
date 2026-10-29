import type { Item, ReceiptData } from './types'

/** Multiply decimal strings and round to cents without binary floating point. */
export function amount(quantity: string, price: string): string | null {
  return multiply(quantity, price, 2)
}

function multiply(quantity: string, price: string, places: number): string | null {
  if (![quantity, price].every(value => /^-?\d{1,12}(?:\.\d{1,12})?$/.test(value))) return null
  const parts = [quantity, price].map(value => ({
    value: BigInt(value.replace('.', '')), scale: value.split('.')[1]?.length || 0,
  }))
  const product = parts[0].value * parts[1].value
  const divisor = 10n ** BigInt(parts[0].scale + parts[1].scale)
  const absolute = product < 0n ? -product : product
  const rounded = (absolute * 10n ** BigInt(places) + divisor / 2n) / divisor
  return format(product < 0n ? -rounded : rounded, places)
}

function decimal(value: string): bigint | null {
  if (!/^-?\d{1,12}(?:\.\d{1,12})?$/.test(value)) return null
  const [whole, fraction = ''] = value.replace('-', '').split('.')
  return BigInt(whole + fraction.padEnd(12, '0')) * (value.startsWith('-') ? -1n : 1n)
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
  return amount(format(total, 12), '1')
}
function equal(a: string, b: string | null): boolean {
  return b !== null && decimal(a) !== null && decimal(a) === decimal(b)
}

export function effectiveDiscount(item: Item, data: ReceiptData): string {
  return item.discount_percent || data.total_discount_percent || ''
}

function discountFactor(percent: string): string | null {
  if (!percent) return '1'
  if (!/^\d{1,3}(?:\.\d{1,6})?$/.test(percent)) return null
  const [whole, fraction = ''] = percent.split('.')
  const value = BigInt(whole + fraction.padEnd(6, '0'))
  return value <= 100000000n ? format(100000000n - value, 8) : null
}

export function discountedPrice(price: string, percent: string): string | null {
  const factor = discountFactor(percent)
  return factor === null ? null : multiply(price, factor, 12)
}

function itemAmount(item: Item, data: ReceiptData): string | null {
  const price = discountedPrice(item.unit_price, effectiveDiscount(item, data))
  return price === null || !/^-?\d+$/.test(item.quantity) ? null : amount(item.quantity, price)
}

/** Fill missing arithmetic; preserve differing written amounts and totals (discounts). */
export function completeAmounts(data: ReceiptData, previous?: ReceiptData): ReceiptData {
  let discountChanged = false
  const items = data.items.map(item => {
    const next = { ...item }, old = previous?.items.find(value => value.id === item.id)
    if (/^-?\d+\.0+$/.test(next.quantity)) next.quantity = next.quantity.split('.')[0]
    const percent = effectiveDiscount(next, data)
    const changedDiscount = !!previous && percent !== (old ? effectiveDiscount(old, previous) : '')
    discountChanged ||= changedDiscount
    const price = discountedPrice(next.unit_price, percent)
    const amountEdited = old && next.line_total !== old.line_total
    const factorsEdited = old && (next.quantity !== old.quantity || next.unit_price !== old.unit_price)
    if (!changedDiscount && amountEdited && next.line_total && price && next.quantity === old.quantity) {
      next.quantity = divide(next.line_total, price, 0) ?? next.quantity
    } else if (!changedDiscount && !next.quantity && next.line_total && price) {
      next.quantity = divide(next.line_total, price, 0) ?? ''
    }
    if (!changedDiscount && !next.unit_price && next.line_total && /^-?\d+$/.test(next.quantity)) {
      const factor = discountFactor(percent)
      const divisor = factor === null ? null : multiply(next.quantity, factor, 12)
      next.unit_price = divisor === null ? '' : divide(next.line_total, divisor, 2) ?? ''
    }
    if (!next.line_total || changedDiscount || (factorsEdited && equal(old.line_total, itemAmount(old, previous!)))) {
      next.line_total = itemAmount(next, data) ?? next.line_total
    }
    return next
  })
  const next = { ...data, items }
  if (!next.total || (previous && next.total === previous.total && (discountChanged || equal(previous.total, sum(previous))))) {
    next.total = sum(next) ?? ''
  }
  return next
}
