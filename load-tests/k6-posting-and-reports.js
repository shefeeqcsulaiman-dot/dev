import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  scenarios: {
    reports_smoke: {
      executor: "constant-vus",
      vus: Number(__ENV.REPORT_VUS || 10),
      duration: __ENV.REPORT_DURATION || "30s",
      exec: "reportsSmoke",
    },
    source_posting_smoke: {
      executor: "constant-vus",
      vus: Number(__ENV.POSTING_VUS || 5),
      duration: __ENV.POSTING_DURATION || "30s",
      exec: "postingSmoke",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.05"],
    http_req_duration: ["p(95)<750"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:8010/api/v1";
const EMAIL = __ENV.TAXFLOW_EMAIL || "qa-admin@taxflowqa.com";
const PASSWORD = __ENV.TAXFLOW_PASSWORD || "admin123";

function token() {
  const response = http.post(
    `${BASE_URL}/auth/login`,
    JSON.stringify({ email: EMAIL, password: PASSWORD }),
    { headers: { "Content-Type": "application/json" } },
  );
  check(response, { "login ok": (r) => r.status === 200 });
  return response.json("access_token");
}

export function setup() {
  return { token: token() };
}

function authHeaders(data) {
  return {
    headers: {
      Authorization: `Bearer ${data.token}`,
      "Content-Type": "application/json",
    },
  };
}

export function reportsSmoke(data) {
  for (const path of ["/reports/dashboard", "/reports/summary", "/tax/vat-return", "/source-transactions"]) {
    const response = http.get(`${BASE_URL}${path}`, authHeaders(data));
    check(response, { [`GET ${path} 200`]: (r) => r.status === 200 });
  }
  sleep(1);
}

export function postingSmoke(data) {
  const suffix = `${__VU}-${__ITER}-${Date.now()}`;
  const create = http.post(
    `${BASE_URL}/source-transactions`,
    JSON.stringify({
      module: "sales",
      reference: `K6-SRC-${suffix}`,
      party_name: "k6 Load Customer",
      lines: [{ description: "Load test sale", account_code: "3000", quantity: "1", unit_price: "100.00", vat_rate: "5" }],
    }),
    authHeaders(data),
  );
  check(create, { "source created": (r) => r.status === 201 });
  if (create.status !== 201) return;

  const id = create.json("id");
  const validate = http.post(`${BASE_URL}/source-transactions/${id}/validate`, null, authHeaders(data));
  check(validate, { "source validated": (r) => r.status === 200 });

  const approve = http.post(`${BASE_URL}/source-transactions/${id}/approve`, null, authHeaders(data));
  check(approve, { "posting queued": (r) => r.status === 202 });
  sleep(1);
}
