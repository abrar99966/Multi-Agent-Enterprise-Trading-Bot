import React from 'react';

export default class ErrorBoundary extends React.Component {
  state = { err: null };
  static getDerivedStateFromError(err) { return { err }; }
  componentDidCatch(err, info) {
    if (typeof console !== 'undefined') console.error('UI error:', err, info);
  }
  reset = () => this.setState({ err: null });
  render() {
    if (this.state.err) {
      return (
        <div className="min-h-screen flex items-center justify-center p-6 text-white">
          <div className="glass rounded-2xl p-6 max-w-lg w-full">
            <div className="text-base font-semibold text-rose-300">Something rendered badly.</div>
            <pre className="mt-3 text-xs text-white/60 whitespace-pre-wrap overflow-auto max-h-48">
              {String(this.state.err?.message || this.state.err)}
            </pre>
            <button onClick={this.reset}
              className="mt-4 px-4 py-2 rounded-lg text-sm font-semibold text-ink-900 bg-gold-500 hover:bg-gold-400 transition">
              Retry
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
