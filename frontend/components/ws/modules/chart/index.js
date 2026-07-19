/**
 * Chart module — price surface shared by Dashboard and Markets.
 *
 *   import { ChartWorkspace } from '../components/ws/modules/chart';
 *
 * ChartWorkspace owns the timeframe state and the candle subscription;
 * CandleChart is the pure canvas renderer beneath it and takes a series it is
 * given, so a host that already holds bars can skip the fetch entirely.
 */
export { ChartWorkspace, TIMEFRAMES, default } from './ChartWorkspace';
export { CandleChart } from './CandleChart';
