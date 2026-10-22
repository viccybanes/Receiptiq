export function localDay(timestamp: string): string {
  const date = new Date(timestamp)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

export function receiptDays(batches: { created_at: string; file_count: number }[]) {
  const counts = new Map<string, number>()
  for (const batch of batches) {
    const day = localDay(batch.created_at)
    counts.set(day, (counts.get(day) || 0) + batch.file_count)
  }
  return [...counts].sort(([a], [b]) => b.localeCompare(a)).map(([day, files]) => ({ day, files }))
}
