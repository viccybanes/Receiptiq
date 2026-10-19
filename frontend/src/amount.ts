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
