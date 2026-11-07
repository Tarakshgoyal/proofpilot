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

export interface Decision {
  recommendedAction: string
  pWin: number
  evFightMinor: number
  evFoldMinor: number
  rationale: string
}

export interface PlanOut {
  id: string
  caseId: string
  version: number
  status: string
  payload: any
  payloadHash: string
  packetHash: string
  action?: ActionOut | null
}

export interface ActionOut {
  id: string
  planId: string
  actionType: string
  status: string
  response: any
  executedAt: string | null
}

export interface AuditOut {
  verification: { valid: boolean; entries?: number; head?: string; reason?: string }
  entries: { seq: number; actor: string; event: string; payload: any; hash: string }[]
}

export const fetchDecision = (id: string) =>
  fetch(`/api/cases/${id}/decision`).then(r => r.json() as Promise<Decision>)

export const buildPlan = (id: string) =>
  fetch(`/api/cases/${id}/plan`, { method: 'POST' }).then(async r => {
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText)
    return r.json() as Promise<PlanOut>
  })

export const approvePlan = (planId: string, expectedPlanHash: string, expectedCaseVersion: number) =>
  fetch(`/api/plans/${planId}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expectedPlanHash, expectedCaseVersion, approver: 'merchant' }),
  }).then(async r => {
    const body = await r.json()
    if (!r.ok) throw new Error(body.detail || r.statusText)
    return body as { plan: PlanOut; action: ActionOut | null }
  })

export const fetchAction = (id: string) =>
  fetch(`/api/actions/${id}`).then(r => r.json() as Promise<ActionOut>)

export const fetchAudit = (caseId: string) =>
  fetch(`/api/cases/${caseId}/audit`).then(r => r.json() as Promise<AuditOut>)

export const locate = (docId: string, quote: string) =>
  fetch(`/api/documents/${docId}/locate?quote=${encodeURIComponent(quote)}`)
    .then(r => r.json() as Promise<{ found: boolean; page?: number; box?: number[]; matchType?: string }>)

export const fetchLatestPlan = (caseId: string) =>
  fetch(`/api/cases/${caseId}/plan`).then(r => r.json() as Promise<PlanOut | {}>)
