export type ThumbnailAnchor = { x: number; top: number; bottom: number; viewportWidth: number; viewportHeight: number }

export function timelineThumbnailLayout(anchor: ThumbnailAnchor, width: number, height: number) {
  const margin = 8
  const gap = 8
  const chrome = 28 // Timestamp and card border.
  const above = Math.max(0, anchor.top - gap - margin)
  const below = Math.max(0, anchor.viewportHeight - anchor.bottom - gap - margin)
  const preferredHeight = Math.min(270, height * Math.min(1, 238 / width)) + chrome
  const useAbove = above >= preferredHeight || above >= below
  const maxHeight = Math.max(1, Math.min(270, (useAbove ? above : below) - chrome))
  const scale = Math.min(1, Math.max(1, Math.min(238, anchor.viewportWidth - margin * 2 - 2)) / width, maxHeight / height)
  const imageWidth = width * scale
  const imageHeight = height * scale
  const popupWidth = imageWidth + 2
  const popupHeight = imageHeight + chrome
  return {
    left: Math.max(margin, Math.min(anchor.viewportWidth - popupWidth - margin, anchor.x - popupWidth / 2)),
    top: useAbove ? anchor.top - gap - popupHeight : anchor.bottom + gap,
    width: popupWidth,
    imageHeight,
  }
}
