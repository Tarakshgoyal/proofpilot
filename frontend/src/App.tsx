import { useCallback, useEffect, useRef, useState } from 'react'
import { AgGridReact } from 'ag-grid-react'
import { ColDef } from 'ag-grid-community'
import 'ag-grid-community/styles/ag-grid.css'
import 'ag-grid-community/styles/ag-theme-quartz.css'
import {
  ActionOut, AuditOut, CaseRow, Decision, PlanOut, Proposal,
  analyseCase, approvePlan, buildPlan, fetchAction, fetchAudit, fetchCases, fetchDecision, fetchLatestPlan, fetchProposal,
} from './api'
import { PdfViewer } from './PdfViewer'
import { EvalPage } from './EvalPage'
import { ErrorBoundary } from './ErrorBoundary'

function StatusBadge({ status }: { status: string }) {
  const cls =
    status === 'VERIFIED' || status === 'COMPLETED' || status === 'valid' ? 'badge ok' :
    ['UNSUPPORTED', 'CONTRADICTED', 'FAILED', 'EXECUTION_UNKNOWN'].includes(status) ? 'badge bad' :
    status === 'PENDING' || status === 'QUEUED' || status === 'DISPATCHING' ? 'badge pend' : 'badge'
  return <span className={cls}>{status}</span>
}

function usd(minor: number) { return `$${(minor / 100).toFixed(2)}` }

function CasePanel({ caseId, onClose }: { caseId: string; onClose: () => void }) {
  const [p, setP] = useState<Proposal | null>(null)
  const [decision, setDecision] = useState<Decision | null>(null)
  const [plan, setPlan] = useState<PlanOut | null>(null)
  const [action, setAction] = useState<ActionOut | null>(null)
  const [audit, setAudit] = useState<AuditOut | null>(null)
  const [modal, setModal] = useState<'approve' | null>(null)
  const [pdf, setPdf] = useState<{ documentId: string; quote: string } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const [prop, dec, aud] = await Promise.all([
        fetchProposal(caseId), fetchDecision(caseId), fetchAudit(caseId),
      ])
      setP(prop); setDecision(dec); setAudit(aud); setErr(null)
      const pl = await fetchLatestPlan(caseId)
      if (pl && (pl as PlanOut).id) {
        setPlan(pl as PlanOut)
        setAction((pl as any).action ?? null)
      } else {
        setPlan(null); setAction(null)
      }
    } catch (e) { setErr(String(e)) }
  }, [caseId])

  useEffect(() => {
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [load])

  // poll live action until terminal
  useEffect(() => {
    if (!action || ['COMPLETED', 'FAILED', 'EXECUTION_UNKNOWN'].includes(action.status)) return
    const t = setInterval(async () => setAction(await fetchAction(action.id)), 2500)
    return () => clearInterval(t)
  }, [action])

  const prepare = async () => {
    setBusy(true); setErr(null)
    try {
      await analyseCase(caseId)
      await new Promise(r => setTimeout(r, 3000))
      const pl = await buildPlan(caseId)
      setPlan(pl)
      await load()
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  const doApprove = async () => {
    if (!plan) return
    setBusy(true); setErr(null)
    try {
      const res = await approvePlan(plan.id, plan.payloadHash, plan.payload.case_version)
      setModal(null)
      setPlan(res.plan)
      if (res.action) setAction(res.action)
      await load()
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <div>
          <b>{p?.case.dispute_id ?? caseId}</b>
          {p && <span className="dim"> · {p.case.reason} · {p.case.provider_status} · v{p.case.version}</span>}
        </div>
        <div className="panel-actions">
          <button disabled={busy} onClick={prepare}>{busy ? 'Working…' : 'Prepare response'}</button>
          <button onClick={onClose}>Close</button>
        </div>
      </div>

      {err && <div className="error">{err}</div>}
      {p?.blocking ? (
        <div className="blocking">
          ⛔ {p.blocking} BLOCKING contradiction(s) — no plan can be built until resolved.
        </div>
      ) : null}

      {decision && (
        <div className="decision">
          <b>Decision: {decision.recommendedAction}</b>
          <span className="dim"> · p(win) = {decision.pWin} (heuristic)</span>
          <div className="small">
            EV fight <b>{usd(decision.evFightMinor)}</b> vs fold <b>{usd(decision.evFoldMinor)}</b>
          </div>
          <div className="dim small">{decision.rationale}</div>
        </div>
      )}

      {p?.contradictions.map(c => (
        <div key={c.id} className={c.severity === 'BLOCKING' ? 'contra blocking-row' : 'contra'}>
          <b>{c.rule}</b> <span className="dim">({c.severity})</span> — {c.explanation}
        </div>
      ))}

      <h4>Claims {p ? `(${p.claims.filter(c => c.status === 'VERIFIED').length} verified / ${p.claims.filter(c => c.status !== 'VERIFIED' && c.status !== 'PENDING').length} rejected)` : ''}</h4>
      <div className="claims">
        {p?.claims.map(c => (
          <div key={c.id} className="claim">
            <div>
              <StatusBadge status={c.status} />{' '}
              <span className={c.status === 'UNSUPPORTED' || c.status === 'CONTRADICTED' ? 'struck' : ''}>
                {c.statement}
              </span>
              {c.quoteRef && (
                <button className="linklike" onClick={() =>
                  setPdf({ documentId: c.quoteRef!.document_id, quote: c.quoteRef!.quote })}>
                  view source ↗
                </button>
              )}
            </div>
            <div className="dim small">{c.claimType} · {c.reason}</div>
          </div>
        )) ?? <div className="dim">no analysis yet — Prepare response</div>}
      </div>

      {plan && (
        <>
          <h4>Plan v{plan.version} — {plan.status}</h4>
          <div className="plan">
            <div className="small">payload sha256: <code>{plan.payloadHash.slice(0, 32)}…</code></div>
            <div className="small">packet sha256: <code>{plan.packetHash.slice(0, 32)}…</code></div>
            <div className="small dim">
              endpoint chosen at execution by the dispute's live HATEOAS links ·
              attachment: {plan.payload?.attachments?.[0]?.filename}
            </div>
            {plan.status === 'AWAITING_APPROVAL' && (
              <button className="approve" onClick={() => setModal('approve')}>
                Review & approve exact payload
              </button>
            )}
            {plan.status === 'EXECUTED' && <span className="badge ok">EXECUTED</span>}
            {plan.status === 'STALE' && <span className="badge bad">STALE — evidence changed, rebuild</span>}
            <a className="small" href={`/api/plans/${plan.id}/packet`} target="_blank" rel="noreferrer">
              download evidence packet (PDF) ↗
            </a>
          </div>
        </>
      )}

      {action && (
        <>
          <h4>Execution receipt</h4>
          <div className="receipt">
            <div><StatusBadge status={action.status} /> {action.actionType}
              {action.executedAt && <span className="dim"> · {new Date(action.executedAt).toLocaleString()}</span>}</div>
            {action.response?.http && <div className="small dim">provider HTTP {action.response.http} via {action.response.endpoint}</div>}
            {action.response?.error && <div className="small error">{String(action.response.error).slice(0, 160)}</div>}
            <div className="dim small">
              Request acknowledged ≠ evidence submitted ≠ under review ≠ resolved.
            </div>
          </div>
        </>
      )}

      {audit && (
        <>
          <h4>Audit chain <StatusBadge status={audit.verification.valid ? 'valid' : 'invalid'} /></h4>
          <div className="audit">
            {audit.entries.map(e => (
              <div key={e.seq} className="small">
                <code>{e.hash}</code> {e.actor} · <b>{e.event}</b>
              </div>
            ))}
          </div>
        </>
      )}

      {p?.analysis?.output.draft_message ? (
        <>
          <h4>Draft message</h4>
          <div className="draft">{p.analysis.output.draft_message}</div>
        </>
      ) : null}
      {p?.analysis ? (
        <div className="dim small footer">
          model {p.analysis.model} · prompt {p.analysis.promptVersion} · analysis {p.analysis.status}
          {p.analysis.error ? ` (${p.analysis.error})` : ''} · action <b>{p.analysis.recommendedAction}</b>
        </div>
      ) : null}

      {modal === 'approve' && plan && (
        <div className="modal-backdrop" onClick={() => setModal(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h3>Approve the exact submission</h3>
            <p className="dim small">
              You are approving this exact payload, hash-bound. Any evidence change
              invalidates this approval automatically (STALE).
            </p>
            <pre>{JSON.stringify(plan.payload, null, 2)}</pre>
            <div className="small">
              payload sha256 <code>{plan.payloadHash.slice(0, 32)}…</code><br />
              packet sha256 <code>{plan.packetHash.slice(0, 32)}…</code><br />
              {decision && <>EV: fight {usd(decision.evFightMinor)} vs fold {usd(decision.evFoldMinor)}</>}
            </div>
            <div className="modal-actions">
              <button onClick={() => setModal(null)}>Cancel</button>
              <button className="approve" disabled={busy} onClick={doApprove}>
                {busy ? 'Approving…' : 'Approve & submit through PayPal'}
              </button>
            </div>
          </div>
        </div>
      )}

      {pdf && (
        <ErrorBoundary>
          <PdfViewer documentId={pdf.documentId} quote={pdf.quote} onClose={() => setPdf(null)} />
        </ErrorBoundary>
      )}
    </div>
  )
}

export default function App() {
  const gridRef = useRef<AgGridReact>(null)
  const [cases, setCases] = useState<CaseRow[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [tab, setTab] = useState<'queue' | 'eval'>('queue')

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
      valueFormatter: r => String(r.value ?? '').replace('MERCHANDISE_OR_SERVICE_', '') },
    { headerName: 'Stage', field: 'stage', width: 110 },
    { headerName: 'PayPal', field: 'provider_status', width: 150 },
    { headerName: 'Flow', field: 'workflow_status', width: 110 },
    { headerName: 'Amount', field: 'amount_minor', width: 90,
      valueFormatter: r => `$${(r.value / 100).toFixed(2)}` },
    { headerName: 'Facts', field: 'fact_count', width: 70 },
    { headerName: 'Contradictions', field: 'contradiction_count', width: 120,
      cellClass: r => (r.value > 0 ? 'hot' : '') },
    { headerName: 'Claims v/r', valueGetter: r =>
        `${r.data?.claims_verified ?? 0}/${r.data?.claims_rejected ?? 0}`, width: 100 },
    { headerName: 'Action', field: 'recommended_action', width: 90 },
  ]

  return (
    <div className="app">
      <header>
        <b>ProofPilot</b>
        <span className="dim"> · no claim without a citation, no citation without a checksum</span>
        <span className="tabs">
          <button className={tab === 'queue' ? 'tab active' : 'tab'} onClick={() => setTab('queue')}>Dispute queue</button>
          <button className={tab === 'eval' ? 'tab active' : 'tab'} onClick={() => setTab('eval')}>Eval</button>
        </span>
      </header>
      {tab === 'eval' ? <EvalPage /> : (
      <div className="layout">
        <div className="ag-theme-quartz grid">
          <AgGridReact
            ref={gridRef}
            rowData={cases}
            columnDefs={colDefs}
            onRowClicked={e => setSelected((e.data as CaseRow).id)}
            getRowClass={r => (r.data?.contradiction_count > 0 ? 'row-hot' : '')}
            pagination={false}
          />
        </div>
        {selected ? <CasePanel caseId={selected} onClose={() => setSelected(null)} /> : null}
      </div>
      )}
    </div>
  )
}
