import { useCallback, useEffect, useRef, useState } from 'react'
import { AgGridReact } from 'ag-grid-react'
import { ColDef } from 'ag-grid-community'
import 'ag-grid-community/styles/ag-grid.css'
import 'ag-grid-community/styles/ag-theme-quartz.css'
import { analyseCase, fetchCases, fetchProposal, CaseRow, Proposal } from './api'

function StatusBadge({ status }: { status: string }) {
  const cls =
    status === 'VERIFIED' ? 'badge ok' :
    status === 'UNSUPPORTED' || status === 'CONTRADICTED' ? 'badge bad' :
    status === 'PENDING' ? 'badge pend' : 'badge'
  return <span className={cls}>{status}</span>
}

function CasePanel({ caseId, onClose }: { caseId: string; onClose: () => void }) {
  const [p, setP] = useState<Proposal | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(async () => {
    try { setP(await fetchProposal(caseId)); setErr(null) }
    catch (e) { setErr(String(e)) }
  }, [caseId])

  useEffect(() => {
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [load])

  return (
    <div className="panel">
      <div className="panel-head">
        <div>
          <b>{p?.case.dispute_id ?? caseId}</b>
          {p && <span className="dim"> · {p.case.reason} · {p.case.provider_status}</span>}
        </div>
        <div className="panel-actions">
          <button onClick={() => analyseCase(caseId).then(() => setTimeout(load, 1500))}>
            Re-run agent
          </button>
          <button onClick={onClose}>Close</button>
        </div>
      </div>

      {err && <div className="error">failed to load: {err}</div>}
      {p?.blocking ? (
        <div className="blocking">
          ⛔ {p.blocking} BLOCKING contradiction(s) — submission must not proceed
          until resolved.
        </div>
      ) : null}

      {p?.contradictions.map((c) => (
        <div key={c.id} className={c.severity === 'BLOCKING' ? 'contra blocking-row' : 'contra'}>
          <b>{c.rule}</b> <span className="dim">({c.severity})</span> — {c.explanation}
        </div>
      ))}

      <h4>Claims {p ? `(${p.claims.filter(c => c.status === 'VERIFIED').length} verified / ${p.claims.filter(c => c.status !== 'VERIFIED' && c.status !== 'PENDING').length} rejected)` : ''}</h4>
      <div className="claims">
        {p?.claims.map((c) => (
          <div key={c.id} className="claim">
            <div>
              <StatusBadge status={c.status} />{' '}
              <span className={c.status === 'UNSUPPORTED' || c.status === 'CONTRADICTED' ? 'struck' : ''}>
                {c.statement}
              </span>
            </div>
            <div className="dim small">
              {c.claimType} · {c.reason}
              {c.quoteRef ? ` · quote: “${c.quoteRef.quote.slice(0, 60)}…”` : ''}
            </div>
          </div>
        )) ?? <div className="dim">no analysis yet — run the agent</div>}
      </div>

      {p?.analysis?.output.draft_message ? (
        <>
          <h4>Draft message</h4>
          <div className="draft">{p.analysis.output.draft_message}</div>
        </>
      ) : null}
      {p?.analysis ? (
        <div className="dim small footer">
          model {p.analysis.model} · prompt {p.analysis.promptVersion} ·
          analysis {p.analysis.status}{p.analysis.error ? ` (error: ${p.analysis.error})` : ''} ·
          action <b>{p.analysis.recommendedAction}</b>
        </div>
      ) : null}
    </div>
  )
}

export default function App() {
  const gridRef = useRef<AgGridReact>(null)
  const [cases, setCases] = useState<CaseRow[]>([])
  const [selected, setSelected] = useState<string | null>(null)

  useEffect(() => {
    const load = () => fetchCases().then(setCases).catch(() => {})
    load()
    const t = setInterval(load, 10000)
    return () => clearInterval(t)
  }, [])

  const colDefs: ColDef<CaseRow>[] = [
    { headerName: 'Dispute', field: 'dispute_id', flex: 1.4,
      cellRenderer: (r: any) => r.value + ((r.data?.synthetic) ? ' ⚙syn' : '') },
    { headerName: 'Reason', field: 'reason', flex: 1.3,
      valueFormatter: (r) => String(r.value ?? '').replace('MERCHANDISE_OR_SERVICE_', '') },
    { headerName: 'Stage', field: 'stage', width: 110 },
    { headerName: 'PayPal', field: 'provider_status', width: 150 },
    { headerName: 'Flow', field: 'workflow_status', width: 110 },
    { headerName: 'Amount', field: 'amount_minor', width: 90,
      valueFormatter: (r) => `$${(r.value / 100).toFixed(2)}` },
    { headerName: 'Facts', field: 'fact_count', width: 70 },
    { headerName: 'Contradictions', field: 'contradiction_count', width: 120,
      cellClass: (r) => (r.value > 0 ? 'hot' : '') },
    { headerName: 'Claims v/r', valueGetter: (r) =>
        `${r.data?.claims_verified ?? 0}/${r.data?.claims_rejected ?? 0}`, width: 100 },
    { headerName: 'Action', field: 'recommended_action', width: 90 },
  ]

  return (
    <div className="app">
      <header>
        <b>ProofPilot</b>
        <span className="dim"> · no claim without a citation, no citation without a checksum</span>
      </header>
      <div className="layout">
        <div className="ag-theme-quartz grid">
          <AgGridReact
            ref={gridRef}
            rowData={cases}
            columnDefs={colDefs}
            onRowClicked={(e) => setSelected((e.data as CaseRow).id)}
            getRowClass={(r) => (r.data?.contradiction_count > 0 ? 'row-hot' : '')}
            pagination={false}
          />
        </div>
        {selected ? <CasePanel caseId={selected} onClose={() => setSelected(null)} /> : null}
      </div>
    </div>
  )
}
