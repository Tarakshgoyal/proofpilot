import React from 'react'

export class ErrorBoundary extends React.Component<
  { children: React.ReactNode }, { error: string | null }
> {
  state = { error: null as string | null }
  static getDerivedStateFromError(e: any) { return { error: String(e) } }
  render() {
    if (this.state.error) {
      return (
        <div className="modal-backdrop" onClick={() => this.setState({ error: null })}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h3>Viewer failed to render</h3>
            <p className="dim small">{this.state.error}</p>
            <button onClick={() => this.setState({ error: null })}>Close</button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
