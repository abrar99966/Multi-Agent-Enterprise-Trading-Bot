/**
 * Markets module — public surface.
 *
 *   import MarketsModule from '../components/ws/modules/markets';
 *
 * MarketsModule is the composition root and the only piece the shell needs.
 * The panels are exported individually so a future dashboard-grid layout can
 * place them independently — note that WatchlistPanel and MarketMovers are
 * controlled (they render rows they are given and issue no requests), while
 * OrderBook fetches for whatever `symbol` it is handed.
 */
export { MarketsModule, default } from './MarketsModule';
export { WatchlistPanel } from './WatchlistPanel';
export { MarketMovers } from './MarketMovers';
export { OrderBook } from './OrderBook';
export { DepthLadder } from './DepthLadder';

// Data layer — exported so sibling views can reuse the quote normaliser rather
// than re-deriving the three incompatible provider shapes.
export {
  MARKETS_CADENCE,
  DEFAULT_SYMBOLS,
  normalizeQuote,
  normalizeSeries,
  reconcileWatchlist,
  buildSyntheticLadder,
  tickSizeFor,
} from './marketsApi';
