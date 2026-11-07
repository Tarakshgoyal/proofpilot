import { useEffect, useRef, useState } from 'react'
import { locate } from './api'

/**
 * Provenance viewer: server renders the cited PDF page as PNG (PyMuPDF),
 * the locator returns the quote's bounding box in PDF units, and we overlay
 * the highlight scaled to the rendered image.
 */
export function PdfViewer({ documentId, quote, onClose }: {
  documentId: string; quote: string; onClose: () => void
}) {
  const [hit, setHit] = useState<{ page: number; box: number[]; matchType: string } | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null)
  const [displayed, setDisplayed] = useState(0)
  const imgRef = useRef<HTMLImageElement>(null)

  useEffect(() => {
    setNotFound(false)
    locate(documentId, quote).then(h => {
      if (h.found && h.page && h.box) setHit({ page: h.page, box: h.box, matchType: h.matchType || 'EXACT' })
      else { setHit(null); setNotFound(true) }
    }).catch(() => setNotFound(true))
  }, [documentId, quote])

  const page = hit?.page ?? 1
  const onImgLoad = () => {
    if (imgRef.current) {
      setNatural({ w: imgRef.current.naturalWidth, h: imgRef.current.naturalHeight })
      setDisplayed(imgRef.current.clientWidth)
    }
  }

  // PyMuPDF word boxes share the raster's top-left origin — no flip needed.
  const scale = natural && displayed ? displayed / natural.w : 0
  const boxStyle = hit && scale
    ? {
        left: hit.box[0] * scale,
        top: hit.box[1] * scale,
        width: (hit.box[2] - hit.box[0]) * scale,
        height: (hit.box[3] - hit.box[1]) * scale,
      }
    : null

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal pdf-modal" onClick={e => e.stopPropagation()}>
        <div className="panel-head">
          <div>
            <b>Source document</b>
            <span className="dim">
              {' '}· locator: {hit ? `${hit.matchType} match, page ${hit.page}`
                : notFound ? 'quote NOT found in document (verifier will reject it)'
                : 'locating…'}
            </span>
          </div>
          <button onClick={onClose}>Close</button>
        </div>
        <div className="quote-bar dim small">“{quote.slice(0, 140)}{quote.length > 140 ? '…' : ''}”</div>
        <div className="pdf-wrap">
          <div style={{ position: 'relative', display: 'inline-block', width: '100%' }}>
            <img
              ref={imgRef}
              src={`/api/documents/${documentId}/page/${page}`}
              onLoad={onImgLoad}
              alt={`document page ${page}`}
              style={{ width: '100%', display: 'block' }}
            />
            {boxStyle && <div className="highlight-box" style={boxStyle} />}
          </div>
        </div>
      </div>
    </div>
  )
}
