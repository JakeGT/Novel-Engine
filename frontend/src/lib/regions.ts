/** Parts of a picture (AST-007): as fractions of the picture, and as pixels of it. */

/** A rectangle as fractions of a picture: left, top, width, height (0 to 1). */
export type Box = { x: number; y: number; width: number; height: number }

/** A part being marked or changed, in pixels of the picture as shown. */
export type PixelBox = { x: number; y: number; width: number; height: number }

export function boxStyle(box: Box) {
  return {
    left: `${box.x * 100}%`,
    top: `${box.y * 100}%`,
    width: `${box.width * 100}%`,
    height: `${box.height * 100}%`,
  }
}

export function toFractions(box: PixelBox, size: { width: number; height: number }): Box {
  return {
    x: box.x / size.width,
    y: box.y / size.height,
    width: box.width / size.width,
    height: box.height / size.height,
  }
}

/** Why a box cannot be kept, in plain words; null when it fits inside the picture. */
export function boxProblem(box: PixelBox, size: { width: number; height: number }): string | null {
  const values = [box.x, box.y, box.width, box.height]
  if (values.some((value) => !Number.isFinite(value) || !Number.isInteger(value))) {
    return 'Use whole numbers of pixels.'
  }
  if (box.x < 0 || box.y < 0) return 'Start inside the picture: from the left and from the top are 0 or more.'
  if (box.width < 1 || box.height < 1) return 'Give it a width and a height of at least 1 pixel.'
  if (box.x + box.width > size.width || box.y + box.height > size.height) {
    return `That goes past the picture’s edge. It is ${size.width} by ${size.height} pixels.`
  }
  return null
}

/** A sensible first box: the middle quarter of the picture. */
export function middleBox(size: { width: number; height: number }): PixelBox {
  const width = Math.max(1, Math.round(size.width / 4))
  const height = Math.max(1, Math.round(size.height / 4))
  return {
    x: Math.round((size.width - width) / 2),
    y: Math.round((size.height - height) / 2),
    width,
    height,
  }
}

/** The box between two points of a drag, whole pixels, inside the picture. */
export function boxBetween(
  a: { x: number; y: number },
  b: { x: number; y: number },
  size: { width: number; height: number },
): PixelBox {
  const left = Math.min(size.width - 1, Math.floor(Math.min(a.x, b.x)))
  const top = Math.min(size.height - 1, Math.floor(Math.min(a.y, b.y)))
  const right = Math.min(size.width, Math.max(left + 1, Math.ceil(Math.max(a.x, b.x))))
  const bottom = Math.min(size.height, Math.max(top + 1, Math.ceil(Math.max(a.y, b.y))))
  return { x: left, y: top, width: right - left, height: bottom - top }
}
