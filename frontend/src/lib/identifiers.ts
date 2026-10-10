// Turning what an author types into the identifiers the server stores.

/** "The Salt Road" → "the-salt-road". */
export function slugify(name: string): string {
  return name
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60)
}

/** "Sea ship" → "SeaShip": type names are single identifiers. */
export function typeIdentifier(text: string): string {
  return text
    .replace(/[^A-Za-z0-9 _-]/g, '')
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join('')
    .replace(/^[0-9]+/, '')
    .slice(0, 64)
}

/** "Home port" → "home_port". */
export function fieldIdentifier(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .replace(/^[0-9]+/, '')
    .slice(0, 64)
}

/** "trained by" → "TRAINED_BY". */
export function predicateIdentifier(text: string): string {
  return fieldIdentifier(text).toUpperCase()
}
