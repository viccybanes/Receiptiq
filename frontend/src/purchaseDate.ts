// Storage and CSV templates retain ISO dates. The review field uses MM/DD/YY.
export function displayDate(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  return match ? `${match[2]}/${match[3]}/${match[1].slice(-2)}` : value
}

export function inputDate(value: string): string {
  const match = /^(\d{2})\/(\d{2})\/(\d{2})$/.exec(value)
  if (!match) return value
  const year = 2000 + Number(match[3]), month = Number(match[1]), day = Number(match[2])
  const date = new Date(Date.UTC(year, month - 1, day))
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return value
  return `${year}-${match[1]}-${match[2]}`
}
