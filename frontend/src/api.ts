export interface CaseRow {
  id: string
  dispute_id: string
  reason: string
  stage: string
  provider_status: string
  workflow_status: string
  synthetic: boolean
  amount_minor: number
  currency: string
  response_due_at: string | null
  fact_count: number
  document_count: number
  contradiction_count: number
  claims_verified: number
  claims_rejected: number
  recommended_action: string
}

export interface Proposal {
  case: CaseRow & { version: number }
  analysis: {
    id: string
    status: string
    model: string
    promptVersion: string
    recommendedAction: string
    error: string | null
    output: {
      draft_message?: string
      evidence_checklist?: { requested: string; status: string; fact_ids: string[] }[]
      missing_or_contradictory?: { finding: string; severity: string }[]
    }
  } | null
  claims: {
    id: string
    claimType: string
    statement: string
    status: string
    reason: string
    factIds: string[]
    quoteRef: { document_id: string; quote: string } | null
  }[]
  contradictions: { id: string; rule: string; severity: string; explanation: string }[]
  blocking: number
}

export async function fetchCases(): Promise<CaseRow[]> {
  const r = await fetch('/api/cases')
  const body = await r.json()
  return body.cases ?? []
}

export async function fetchProposal(caseId: string): Promise<Proposal> {
  const r = await fetch(`/api/cases/${caseId}/proposal`)
  if (!r.ok) throw new Error(`proposal ${r.status}`)
  return r.json()
}

export async function analyseCase(caseId: string): Promise<void> {
  await fetch(`/api/cases/${caseId}/analyse`, { method: 'POST' })
}
