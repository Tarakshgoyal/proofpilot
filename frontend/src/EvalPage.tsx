import { useCallback, useEffect, useState } from 'react'
import { AgGridReact } from 'ag-grid-react'
import { ColDef } from 'ag-grid-community'
import { EvalRunDetail, EvalRunOut, fetchEvalRun, fetchEvalRuns, startEval } from './api'

function Metric({ label, value, hint }: { label: string; value: string | null; hint?: string }) {
  return (
    <div className="metric">
      <div className="metric-value">{value ?? '—'}</div>
      <div className="metric-label">{label}</div>
      {hint && <div className="dim small">{hint}</div>}
    </div>
  )
}

const pct = (v: number | null | undefined) =>
  v === null || v === undefined ? null : `${(v * 100).toFixed(1)}%`

export function EvalPage() {
  const [runs, setRuns] = useState<EvalRunOut[]>([])
  const [detail, setDetail] = useState<EvalRunDetail | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async (latestId?: string) => {
    const { runs } = await fetchEvalRuns()
    setRuns(runs)
    const id = latestId || runs.find(r => r.status === 'DONE')?.id || runs[0]?.id
    if (id) setDetail(await fetchEvalRun(id))
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(() => load(), 15000)
    return () => clearInterval(t)
  }, [load])

  const run = async (quick: boolean) => {
    setBusy(true)
    try {
      const { runId } = await startEval(quick)
      await load(runId)
    } finally { setBusy(false) }
  }

  const t = detail?.totals || {}
  const rawRate = pct(t.raw_unsupported_rate as number | null)
  const scenarioRows = detail?.results.map(r => ({
    scenario: r.scenario,
    ok: r.ok ? '✓' : '✗',
    action: r.found.action ?? r.found.error?.slice(0, 40) ?? '—',
    expected: r.label.expected_action,
    contradictions: (r.found.contradictions || []).join(',') || '—',
    expectedContradictions: r.label.expected_contradictions.join(',') || '—',
    struck: (r.found.struck_types || []).join(',') || '—',
    claims: `${r.found.claims_verified ?? 0}v/${r.found.claims_rejected ?? 0}r`,
  })) ?? []

  const colDefs: ColDef[] = [
    { headerName: 'Scenario', field: 'scenario', flex: 1.2 },
    { headerName: 'Pass', field: 'ok', width: 60,
      cellClass: r => (r.value === '✓' ? '' : 'hot') },
    { headerName: 'Action', field: 'action', width: 120 },
    { headerName: 'Expected', field: 'expected', width: 120 },
    { headerName: 'Contradictions', field: 'contradictions', flex: 1.2 },
    { headerName: 'Expected rules', field: 'expectedContradictions', flex: 1.2 },
    { headerName: 'Struck claims', field: 'struck', flex: 1 },
    { headerName: 'v/r', field: 'claims', width: 80 },
  ]

  return (
    <div className="eval-page">
      <div className="panel-head">
        <div>
          <b>Measured eval</b>
          <span className="dim"> · labelled synthetic scenarios · {detail?.model || ''}</span>
        </div>
        <div className="panel-actions">
          <span className="dim small">~1–3 min/scenario on the free NIM tier — results stream in below</span>
          <button disabled={busy} onClick={() => run(true)}>Run quick eval (11)</button>
          <button disabled={busy} onClick={() => run(false)}>Run full eval (33)</button>
        </div>
      </div>

      <div className="metrics-row">
        <Metric label="raw-LLM unsupported-claim rate" value={rawRate}
          hint="claims the model asserted that the verifier rejected" />
        <Metric label="verifier-gated rate" value="0.0%"
          hint="only VERIFIED claims can enter a packet — by construction" />
        <Metric label="contradiction recall" value={pct(t.contradiction_recall as number | null)}
          hint={`${t.contradiction_hits}/${t.contradiction_expected} labelled rules fired`} />
        <Metric label="false positives" value={String(t.contradiction_fp ?? '—')} />
        <Metric label="action accuracy" value={pct(t.action_accuracy as number | null)} />
        <Metric label="must-strike recall" value={pct(t.must_strike_recall as number | null)}
          hint="baited claims the verifier correctly struck" />
      </div>

      <div className="dim small" style={{ marginBottom: 8 }}>
        {detail ? `${detail.results.length} scenarios · ${detail.status}` : 'no runs yet'}
        {t.fallbacks ? ` · ${t.fallbacks} rules-fallback analyses` : ''}
        {' '}· synthetic sandbox data; adjudication outcomes are never claimed as real-world signal.
      </div>

      <div className="ag-theme-quartz" style={{ height: 420 }}>
        <AgGridReact rowData={scenarioRows} columnDefs={colDefs} pagination={false} />
      </div>
    </div>
  )
}
