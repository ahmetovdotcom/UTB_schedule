// Shared search helpers for the pickers and table filters.

// Case-, space-, dash- and dot-insensitive: "ис242" matches "ИС-242со".
export function normalizeSearch(value) {
  return String(value ?? '')
    .toLowerCase()
    .replace(/ё/g, 'е')
    .replace(/[\s\-–—_.,·/()]+/g, '')
}

// True when the query is empty or found in any of the given texts.
export function matchesSearch(query, ...texts) {
  const q = normalizeSearch(query)
  if (!q) return true
  return texts.some((text) => normalizeSearch(text).includes(q))
}
