// The reader's guide as a file (QRY-002).

/** A file name from the guide's title, keeping letters, numbers and plain punctuation. */
export function guideFileName(title: string): string {
  return `${title.replace(/[^\p{L}\p{N} ,'-]+/gu, ' ').replace(/\s+/g, ' ').trim() || 'reader guide'}.md`
}
