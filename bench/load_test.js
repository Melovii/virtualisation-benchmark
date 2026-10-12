// k6 load test used for both the VM and the containers.
// MODE=saturation : closed model, constant VUs, no think time  -> maximum throughput
// MODE=rate       : open model, constant arrival rate (req/s)  -> latency at equal offered load
// The request mix is deterministic (by iteration number) so every target gets the same traffic:
//   50% GET /   30% GET /health   20% GET /compute?n=N
import http from 'k6/http';
import { check } from 'k6';

const MODE = __ENV.MODE || 'saturation';
const BASE = __ENV.BASE_URL || 'http://127.0.0.1:5001';
const DURATION = __ENV.DURATION || '120s';
const VUS = parseInt(__ENV.VUS || '50');
const RATE = parseInt(__ENV.RATE || '100');
const N = __ENV.N || '5000';

export const options = {
  scenarios: {
    load: MODE === 'rate'
      ? { executor: 'constant-arrival-rate', rate: RATE, timeUnit: '1s',
          duration: DURATION, preAllocatedVUs: 20, maxVUs: 200 }
      : { executor: 'constant-vus', vus: VUS, duration: DURATION },
  },
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

export default function () {
  const i = __ITER % 10;
  let path = '/';
  if (i >= 5 && i < 8) path = '/health';
  else if (i >= 8) path = `/compute?n=${N}`;
  const res = http.get(`${BASE}${path}`);
  check(res, { 'status is 200': (r) => r.status === 200 });
}

// Tolerant of both the older (m.values.x) and newer (m.x) summary layouts.
function val(metric, key) {
  if (!metric) return null;
  const v = metric.values || metric;
  return v[key] !== undefined ? v[key] : null;
}

export function handleSummary(data) {
  const d = data.metrics.http_req_duration;
  const r = data.metrics.http_reqs;
  const f = data.metrics.http_req_failed;
  const dr = data.metrics.dropped_iterations;
  let failed = val(f, 'rate');
  if (failed === null) failed = val(f, 'value');
  const out = {
    mode: MODE,
    rps: val(r, 'rate'),
    requests: val(r, 'count'),
    avg_ms: val(d, 'avg'),
    med_ms: val(d, 'med'),
    p90_ms: val(d, 'p(90)'),
    p95_ms: val(d, 'p(95)'),
    p99_ms: val(d, 'p(99)'),
    max_ms: val(d, 'max'),
    failed_rate: failed,
    dropped: val(dr, 'count'),
  };
  return { [__ENV.OUT || 'summary.json']: JSON.stringify(out, null, 2) };
}
