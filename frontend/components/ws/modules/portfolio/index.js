/**
 * Portfolio module — public surface.
 *
 *   import PortfolioModule from '../components/ws/modules/portfolio';
 *   <PortfolioModule selectedSymbol={sel} onSelectSymbol={setSel} />
 *
 * The panels are exported individually so the shell can also drop a single one
 * into a custom layout (e.g. AllocationDonut on a dashboard tab) without
 * pulling the whole workspace. They are presentational: every one of them takes
 * data + loading/error/onRetry as props and fetches nothing itself.
 *
 * usePortfolioData is exported for the same reason — a host that already owns
 * the polling can bypass it, and a host that does not can reuse it verbatim.
 */
export { PortfolioModule, default } from './PortfolioModule';

export { AllocationDonut } from './AllocationDonut';
export { ExposureTreemap, squarify } from './ExposureTreemap';
export { SectorHeatmap } from './SectorHeatmap';
export { PositionsGrid } from './PositionsGrid';
export { PerformanceChart } from './PerformanceChart';
export { DrawdownChart } from './DrawdownChart';

export {
  usePortfolioData,
  classifyInstrument,
  sectorOf,
  normalizeQuote,
  positionsFromTrades,
} from './usePortfolioData';
