function defaultApiBaseUrl() {
  const currentHost = window.location.hostname || "127.0.0.1";
  const host = ["localhost", "::1", ""].includes(currentHost) ? "127.0.0.1" : currentHost;
  return `http://${host}:8000/api/v1`;
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || defaultApiBaseUrl();
const LOCAL_FALLBACK_API_BASE_URL = "http://127.0.0.1:8000/api/v1";

export function getToken() {
  return localStorage.getItem("taxflow_token");
}

export function setToken(token) {
  localStorage.setItem("taxflow_token", token);
}

export function clearToken() {
  localStorage.removeItem("taxflow_token");
}

export async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (!(options.body instanceof FormData)) headers.set("Content-Type", "application/json");

  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  } catch (err) {
    if (API_BASE_URL !== LOCAL_FALLBACK_API_BASE_URL) {
      response = await fetch(`${LOCAL_FALLBACK_API_BASE_URL}${path}`, { ...options, headers });
    } else {
      throw err;
    }
  }
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed: ${response.status}`);
  }
  if (response.status === 204) return null;
  return response.json();
}
